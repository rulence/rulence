from __future__ import annotations

import json
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from rulence.classifier import classify_task
from rulence.checks import register_check
from rulence.cli import main
from rulence.feedback import read_feedback, record_feedback, summarize_feedback
from rulence.memory import LocalFileMemoryProvider, memory_items_to_context, retrieve_combined
from rulence.mcp_server import handle_request
from rulence.models import CheckResult
from rulence.policy_cases import run_policy_cases
from rulence.policies import (
    builtin_policies_dir,
    install_builtin_policy,
    list_builtin_policies,
    load_policy,
    validate_policy_dir,
    validate_policy_file,
    write_default_policies,
)
from rulence.preflight import preflight_task
from rulence.reasoning import add_thought, start_session, summarize_session
from rulence.sessions import SessionStore
from rulence.token_budget import estimate_tokens


class RulenceMvpTests(unittest.TestCase):
    def test_classifies_high_risk_destructive_task(self) -> None:
        result = classify_task("delete production credentials")

        self.assertEqual(result.tier, 5)
        self.assertIn("destructive or irreversible action term detected", result.reasons)

    def test_preflight_blocks_high_risk_task(self) -> None:
        result = preflight_task("delete production credentials")

        self.assertEqual(result.verdict, "block")
        self.assertIn("sensitive_or_destructive_action", result.blocks)
        self.assertIn("user_approval", result.policy.required_checks)

    def test_preflight_rejects_empty_task(self) -> None:
        with self.assertRaisesRegex(ValueError, "task cannot be empty"):
            preflight_task("   ")

    def test_preflight_warns_for_complex_migration(self) -> None:
        result = preflight_task(
            "migrate the database this week",
            memory="Release freeze is active this week. Backups run nightly.",
        )

        self.assertIn(result.verdict, {"warn", "approve"})
        self.assertEqual(result.classification.tier, 4)
        self.assertIn("context_budget", result.policy.required_checks)
        self.assertIsNotNone(result.token_budget)

    def test_formal_reasoning_is_at_least_moderate(self) -> None:
        result = classify_task("prove this claim is true")

        self.assertGreaterEqual(result.tier, 3)
        self.assertIn("formal reasoning or proof term detected", result.reasons)

    def test_classifier_uses_word_boundaries(self) -> None:
        result = classify_task("fix syntax in the rapid parser")

        self.assertNotIn("tax", result.signals["high_risk_terms"])
        self.assertNotIn("api", result.signals["tool_terms"])

    def test_local_model_fallback_attempts_long_unknown_tasks(self) -> None:
        with patch.dict(os.environ, {"RULENCE_CLASSIFIER_MODEL": "tiny-local"}), patch("rulence.classifier.request.urlopen", side_effect=OSError("offline")):
            result = classify_task("novel compliance umbrella packet coloring procedure needs interpretation today")

        self.assertEqual(result.signals["local_model_fallback"], "unavailable")

    def test_consistency_check_warns_instead_of_blocking(self) -> None:
        result = preflight_task(
            "Always backup, never migrate without rollback.",
            memory="Local policy requires careful release notes.",
        )

        self.assertNotIn("contradictions_found", result.blocks)
        self.assertNotEqual(result.verdict, "block")

    def test_explicit_constraint_conflicts_block(self) -> None:
        result = preflight_task(
            "plan database migration",
            memory="requires: deploy -> rollback\nforbids: deploy -> rollback",
        )

        self.assertEqual(result.verdict, "block")
        self.assertTrue(any(check.name == "constraint_solver" and check.status == "block" for check in result.checks))

    def test_custom_check_registry_allows_user_checks(self) -> None:
        register_check("demo_custom_check", lambda task, memory, classification, policy, model: CheckResult("demo_custom_check", "pass", "custom check ran"))

        with tempfile.TemporaryDirectory() as directory:
            policy_path = f"{directory}/tier-2-custom.toml"
            with open(policy_path, "w", encoding="utf-8") as handle:
                handle.write(
                    'tier = 2\nlabel = "custom"\nrequired_checks = ["demo_custom_check"]\nwarn_if = []\nblock_if = []\n'
                )
            result = preflight_task("run local script", policy_dir=directory)

        self.assertTrue(any(check.name == "demo_custom_check" for check in result.checks))

    def test_mcp_tools_call_returns_json_text(self) -> None:
        response = handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "rulence_classify",
                    "arguments": {"task": "debug the failing migration"},
                },
            }
        )

        self.assertIsNotNone(response)
        result = response["result"]
        content = result["content"][0]["text"]
        payload = json.loads(content)
        self.assertEqual(result["structuredContent"], payload)
        self.assertIn("tier", payload)
        self.assertIn("reasons", payload)

    def test_mcp_missing_required_arg_returns_invalid_params(self) -> None:
        response = handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "rulence_classify", "arguments": {}},
            }
        )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("task", response["error"]["message"])

    def test_mcp_unknown_tool_returns_invalid_params(self) -> None:
        response = handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "does_not_exist", "arguments": {}},
            }
        )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("unknown tool", response["error"]["message"])

    def test_mcp_unknown_method_returns_method_not_found(self) -> None:
        response = handle_request(
            {"jsonrpc": "2.0", "id": 3, "method": "does_not_exist", "params": {}}
        )

        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("method not found", response["error"]["message"])

    def test_reasoning_session_supports_revision_and_branch(self) -> None:
        session = start_session("compare two architecture options")
        session = add_thought(
            session,
            "Assume option A is faster because it avoids network calls.",
            thought_number=1,
            total_thoughts=3,
            next_thought_needed=True,
        )
        session = add_thought(
            session,
            "Revise thought 1: option B may be safer because it isolates failures.",
            thought_number=2,
            total_thoughts=3,
            next_thought_needed=True,
            is_revision=True,
            revises_thought=1,
        )
        session = add_thought(
            session,
            "Branch from thought 2: risk is lower if retries are constrained.",
            thought_number=3,
            total_thoughts=3,
            branch_from_thought=2,
            branch_id="safe-path",
        )

        summary = summarize_session(session)
        self.assertEqual(summary["revision_count"], 1)
        self.assertEqual(summary["branches"], ["safe-path"])
        self.assertEqual(summary["status"], "complete")

    def test_mcp_reasoning_tools_keep_session_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RULENCE_SESSION_DIR": directory}):
            start = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "rulence_start_thinking",
                        "arguments": {"task": "plan a database migration", "session_id": "test-session"},
                    },
                }
            )
            self.assertIsNotNone(start)

            think = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "rulence_think",
                        "arguments": {
                            "session_id": "test-session",
                            "thought": "Risk: migration can fail without rollback, so verify backup first.",
                            "thought_number": 1,
                            "total_thoughts": 1,
                        },
                    },
                }
            )
            self.assertIsNotNone(think)
            content = think["result"]["content"][0]["text"]
            payload = json.loads(content)
            self.assertEqual(payload["session_id"], "test-session")
            self.assertEqual(len(payload["steps"]), 1)

    def test_sequentialthinking_compat_tool_matches_core_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RULENCE_SESSION_DIR": directory}):
            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "sequentialthinking",
                        "arguments": {
                            "thought": "Start with the riskiest assumption.",
                            "nextThoughtNeeded": True,
                            "thoughtNumber": 1,
                            "totalThoughts": 3,
                        },
                    },
                }
            )

            self.assertIsNotNone(response)
            result = response["result"]
            payload = result["structuredContent"]
            self.assertEqual(payload["thoughtNumber"], 1)
            self.assertEqual(payload["totalThoughts"], 3)
            self.assertTrue(payload["nextThoughtNeeded"])
            self.assertIn("branches", payload)
            self.assertIn("thoughtHistoryLength", payload)
            self.assertIn("rulence", payload)
            self.assertIn("decompositionAvailable", payload["rulence"])
            self.assertIn("decomposition", payload["rulence"])

    def test_sequentialthinking_adjusts_total_thoughts_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RULENCE_SESSION_DIR": directory}):
            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "sequentialthinking",
                        "arguments": {
                            "thought": "Need one more thought after the original estimate.",
                            "nextThoughtNeeded": False,
                            "thoughtNumber": 5,
                            "totalThoughts": 3,
                        },
                    },
                }
            )

            self.assertIsNotNone(response)
            payload = response["result"]["structuredContent"]
            self.assertEqual(payload["thoughtNumber"], 5)
            self.assertEqual(payload["totalThoughts"], 5)

    def test_session_store_round_trips_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            session = add_thought(
                start_session("compare two plans", session_id="demo"),
                "Assume plan A is faster because it avoids network calls.",
            )
            store.save(session)

            loaded = store.load("demo")
            self.assertEqual(loaded.session_id, "demo")
            self.assertEqual(len(loaded.steps), 1)

    def test_policy_validation_accepts_default_policies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            write_default_policies(directory)
            results = validate_policy_dir(directory)

            self.assertTrue(results)
            self.assertTrue(all(not errors for errors in results.values()))
            self.assertTrue(any(path.endswith(".toml") for path in results))

    def test_policy_validation_handles_non_scalar_tier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/tier-bad.toml"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('tier = [1]\nlabel = "bad"\nrequired_checks = []\nwarn_if = []\nblock_if = []\n')

            errors = validate_policy_file(path)

        self.assertIn("tier must be an integer from 0 to 5", errors)

    def test_cli_policy_validate_returns_zero_for_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(main(["init", "--policy-dir", directory]), 0)
            self.assertEqual(main(["policy", "validate", "--policy-dir", directory]), 0)

    def test_cli_bad_inputs_return_clean_errors(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["policy", "install", "not-a-policy"])

        self.assertEqual(code, 2)
        self.assertIn("error:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["trace", "missing-session", "--html"])

        self.assertEqual(code, 2)
        self.assertIn("unknown session_id", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["decompose", ""])

        self.assertEqual(code, 2)
        self.assertIn("prompt cannot be empty", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_policy_cases_catch_expected_tiers_and_verdicts(self) -> None:
        results = run_policy_cases("examples/policy-cases.toml")

        self.assertTrue(results)
        self.assertTrue(all(result["pass"] for result in results))

    def test_migrations_policy_warns_when_rollback_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            install_builtin_policy("migrations", directory)
            result = preflight_task("migrate the production database", policy_dir=directory)

        self.assertEqual(result.verdict, "warn")
        self.assertTrue(any("rollback" in warning.lower() for warning in result.warnings))

    def test_migrations_policy_passes_when_rollback_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            install_builtin_policy("migrations", directory)
            result = preflight_task(
                "migrate the database",
                memory="rollback plan committed; backup verified",
                policy_dir=directory,
            )

        self.assertNotIn("rollback", " ".join(result.warnings).lower())

    def test_secrets_policy_blocks_logging_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            install_builtin_policy("secrets", directory)
            result = preflight_task("log all API credentials for debugging", policy_dir=directory)

        self.assertEqual(result.verdict, "block")

    def test_git_policy_blocks_force_push_to_main(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            install_builtin_policy("git", directory)
            result = preflight_task("force-push the main branch", policy_dir=directory)

        self.assertEqual(result.verdict, "block")

    def test_policy_install_creates_user_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = install_builtin_policy("aws", directory)

            self.assertTrue(path.exists())
            self.assertIn("aws", str(path))

    def test_policy_list_available_returns_all_five(self) -> None:
        names = {policy["name"] for policy in list_builtin_policies()}

        self.assertEqual(names, {"secrets", "git", "migrations", "aws", "payments"})

    def test_all_builtin_policies_validate_clean(self) -> None:
        for path in builtin_policies_dir().glob("*.toml"):
            self.assertEqual(validate_policy_file(path), [])

    def test_installed_policies_merge_by_tier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for name in ("secrets", "git", "migrations", "aws", "payments"):
                install_builtin_policy(name, directory)

            tier4 = load_policy(4, directory)
            tier5 = load_policy(5, directory)
            migration = preflight_task("migrate the database", policy_dir=directory)
            git = preflight_task("force-push the main branch", policy_dir=directory)
            secrets = preflight_task("paste token into public chat", policy_dir=directory)

        self.assertIn("requires(migrate, rollback)", tier4.constraints)
        self.assertIn("forbids(force-push, main)", tier4.constraints)
        self.assertIn("forbids(token, paste)", tier5.constraints)
        self.assertTrue(any("rollback" in warning.lower() for warning in migration.warnings))
        self.assertEqual(git.verdict, "block")
        self.assertEqual(secrets.verdict, "block")

    def test_local_memory_provider_retrieves_relevant_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/memory.md"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("Release freeze requires rollback plans.\n\nUnrelated note about colors.")

            items = LocalFileMemoryProvider(path).retrieve("database rollback freeze", limit=2)
            self.assertEqual(items[0].score, 2)
            self.assertIn("rollback", memory_items_to_context(items))

    def test_combined_memory_merges_by_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/internal.md"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("rollback backup migration\n\nunrelated")

            items = retrieve_combined(["local"], "migration rollback backup", paths={"local": path}, limit=2)

        self.assertGreaterEqual(len(items), 1)
        self.assertEqual(items[0].score, 3)

    def test_combined_memory_skips_failed_providers(self) -> None:
        items = retrieve_combined(["honcho"], "anything", urls={"honcho": "http://127.0.0.1:1"}, limit=5)

        self.assertEqual(items, [])

    def test_cli_preflight_can_use_local_memory_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/memory.md"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("Database migrations require rollback plans and backups.")

            code = main([
                "preflight",
                "plan database migration",
                "--memory-provider",
                "local",
                "--memory-path",
                path,
            ])
            self.assertEqual(code, 0)

    def test_claude_code_hook_denies_blocked_tool_call(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "delete production credentials"},
        }
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(stdout):
            code = main(["hook", "claude-code-pretooluse"])

        data = json.loads(stdout.getvalue())
        output = data["hookSpecificOutput"]
        self.assertEqual(code, 0)
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("Rulence blocked", output["permissionDecisionReason"])

    def test_token_estimator_reports_method(self) -> None:
        count, method = estimate_tokens("hello world")

        self.assertGreater(count, 0)
        self.assertTrue(method)

    def test_token_budget_does_not_report_unmeasured_savings(self) -> None:
        result = preflight_task("plan database migration", memory="Rollback plans exist.")

        self.assertIsNotNone(result.token_budget)
        self.assertNotIn("saved_estimate", result.token_budget.to_dict())

    def test_feedback_records_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/feedback.jsonl"
            record = record_feedback(
                task="delete production credentials",
                verdict="block",
                outcome="correct",
                notes="blocked as expected",
                path=path,
            )

            self.assertEqual(record["outcome"], "correct")
            with open(path, encoding="utf-8") as handle:
                line = json.loads(handle.readline())
            self.assertEqual(line["verdict"], "block")
            self.assertEqual(len(read_feedback(path)), 1)
            self.assertEqual(summarize_feedback(path)["by_outcome"], {"correct": 1})

    def test_final_readiness_warns_for_weak_complex_final_thought(self) -> None:
        session = start_session("plan a database migration")
        session = add_thought(
            session,
            "I think this is fine.",
            thought_number=1,
            total_thoughts=1,
            next_thought_needed=False,
        )

        final_checks = session.steps[-1].checks
        self.assertTrue(any(check.name == "final_readiness" and check.status == "warn" for check in final_checks))

    def test_trace_html_contains_verdict(self) -> None:
        from rulence.trace_html import render_trace_html

        session = start_session("delete production credentials")
        output = render_trace_html(session)

        self.assertIn("verdict-block", output)
        self.assertIn("delete production credentials", output)

    def test_trace_html_escapes_user_input(self) -> None:
        from rulence.trace_html import render_trace_html

        session = start_session("<script>alert(1)</script>")
        output = render_trace_html(session)

        self.assertNotIn("<script>alert", output)
        self.assertIn("&lt;script&gt;", output)

    def test_trace_html_renders_steps(self) -> None:
        from rulence.trace_html import render_trace_html

        session = start_session("plan migration")
        session = add_thought(session, "verify rollback exists", thought_number=1, total_thoughts=1)
        output = render_trace_html(session)

        self.assertIn("verify rollback exists", output)
        self.assertIn("Thought 1/1", output)


if __name__ == "__main__":
    unittest.main()
