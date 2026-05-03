"""End-to-end PreToolUse coverage under the universal '*' matcher.

The universal matcher means every Claude Code tool call now reaches
the dispatcher. These tests exercise representative tool names —
Read, Glob, Grep, WebFetch, MCP servers, and the legacy
Bash/Edit/Write trio — and verify that:

* every PreToolUse call writes an audit record;
* low-risk classifications skip the heavy preflight;
* high/critical classifications produce ask/deny;
* existing Bash deny behavior still works.

All payloads here are synthetic; no real secrets, no real shell.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from rulence.audit import AuditTraceStore, CorrelationIdManager
from rulence.cli import main


class _DispatchFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.audit_dir = root / "audit"
        self.state_dir = root / "state"
        env_patch = patch.dict(
            os.environ,
            {
                "RULENCE_AUDIT_DIR": str(self.audit_dir),
                "RULENCE_STATE_DIR": str(self.state_dir),
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def run_hook(self, payload: dict) -> dict:
        out = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(out):
            main(["hook", "claude-code"])
        return json.loads(out.getvalue())

    def records(self, claude_session_id: str) -> list[dict]:
        rsid = CorrelationIdManager(self.state_dir).derive_session_id(claude_session_id)
        return AuditTraceStore(self.audit_dir).read_session(rsid)


class UniversalToolObservationTests(_DispatchFixture):
    """Every tool call reaches the dispatcher and produces an audit record."""

    def test_read_tool_is_observed(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "u-read",
                "tool_name": "Read",
                "tool_input": {"file_path": "/home/user/code/main.py"},
            }
        )
        self.assertEqual(out, {"suppressOutput": True})
        records = self.records("u-read")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tool_name"], "Read")
        self.assertTrue(records[0]["metadata"]["fast_path"])
        self.assertEqual(records[0]["metadata"]["risk_level"], "low")

    def test_glob_tool_is_observed(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "u-glob",
                "tool_name": "Glob",
                "tool_input": {"pattern": "**/*.py"},
            }
        )
        self.assertEqual(out, {"suppressOutput": True})
        records = self.records("u-glob")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tool_name"], "Glob")

    def test_grep_tool_is_observed(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "u-grep",
                "tool_name": "Grep",
                "tool_input": {"pattern": "TODO"},
            }
        )
        self.assertEqual(out, {"suppressOutput": True})
        records = self.records("u-grep")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tool_name"], "Grep")

    def test_webfetch_external_runs_full_preflight(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "u-webfetch",
                "tool_name": "WebFetch",
                "tool_input": {"url": "https://example.com/foo"},
            }
        )
        # External WebFetch is medium → preflight runs. The default
        # policy may warn on memory_missing (-> ask), or approve outright
        # (-> suppressOutput); both are acceptable outcomes here.
        self.assertTrue(
            out == {"suppressOutput": True}
            or out["hookSpecificOutput"]["permissionDecision"] in ("ask", "deny"),
            f"unexpected output: {out}",
        )
        records = self.records("u-webfetch")
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["metadata"]["fast_path"])
        self.assertEqual(records[0]["metadata"]["risk_level"], "medium")

    def test_mcp_tool_name_is_observed(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "u-mcp",
                "tool_name": "mcp__honcho__search",
                "tool_input": {"query": "rollback"},
            }
        )
        records = self.records("u-mcp")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tool_name"], "mcp__honcho__search")
        # Memory read = medium; full preflight runs but typically approves
        # synthetic payloads.
        self.assertFalse(records[0]["metadata"]["fast_path"])
        self.assertEqual(records[0]["metadata"]["risk_level"], "medium")


class UniversalRiskEnforcementTests(_DispatchFixture):
    """Risky calls produce ask/deny via deterministic rules."""

    def test_bash_rm_rf_is_denied(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "u-rmrf",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /tmp/foo"},
            }
        )
        decision = out["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision, "deny")
        records = self.records("u-rmrf")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["decision"], "deny")
        self.assertEqual(record["metadata"]["risk_level"], "critical")
        self.assertIn("checks_run", record["metadata"])
        self.assertIn("required_followups", record["metadata"])

    def test_bash_git_push_force_is_ask_or_deny(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "u-pushf",
                "tool_name": "Bash",
                "tool_input": {"command": "git push --force origin main"},
            }
        )
        decision = out["hookSpecificOutput"]["permissionDecision"]
        self.assertIn(decision, ("ask", "deny"))
        records = self.records("u-pushf")
        self.assertEqual(records[0]["metadata"]["risk_level"], "critical")

    def test_read_dotenv_is_ask_or_deny(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "u-env",
                "tool_name": "Read",
                "tool_input": {"file_path": "/home/user/.env"},
            }
        )
        decision = out["hookSpecificOutput"]["permissionDecision"]
        self.assertIn(decision, ("ask", "deny"))
        records = self.records("u-env")
        self.assertEqual(records[0]["metadata"]["risk_level"], "high")

    def test_safe_read_is_allow(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "u-saferead",
                "tool_name": "Read",
                "tool_input": {"file_path": "/home/user/code/util.py"},
            }
        )
        self.assertEqual(out, {"suppressOutput": True})

    def test_safe_glob_is_allow(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "u-safeglob",
                "tool_name": "Glob",
                "tool_input": {"pattern": "**/*.md"},
            }
        )
        self.assertEqual(out, {"suppressOutput": True})


class FastPathPreservationUnderUniversalTests(_DispatchFixture):
    """Low-risk calls under '*' must not run the heavy preflight."""

    def test_low_risk_does_not_invoke_preflight(self) -> None:
        with patch("rulence.cli.preflight_task") as preflight_mock:
            preflight_mock.side_effect = AssertionError(
                "preflight_task must not run on the low-risk fast path"
            )
            self.run_hook(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "u-fast",
                    "tool_name": "Read",
                    "tool_input": {"file_path": "/home/user/code/main.py"},
                }
            )
        preflight_mock.assert_not_called()


class ExistingBashEditWriteRegressionTests(_DispatchFixture):
    """The original M0/M1 PreToolUse behavior is preserved end-to-end."""

    def test_destructive_credential_task_is_denied(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "regress-creds",
                "tool_name": "Bash",
                "tool_input": {"command": "delete production credentials"},
            }
        )
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_benign_bash_passes_through(self) -> None:
        out = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "regress-benign",
                "tool_name": "Bash",
                "tool_input": {"command": "list the files in this directory"},
            }
        )
        # Unrecognized Bash -> medium -> preflight runs. The default
        # tier-2 policy approves benign tasks, but may warn when no
        # memory was supplied (-> ask). Either is acceptable; deny is
        # not.
        decision = (
            None
            if out == {"suppressOutput": True}
            else out["hookSpecificOutput"]["permissionDecision"]
        )
        self.assertNotEqual(decision, "deny", f"unexpected deny: {out}")

    def test_audit_record_has_required_fields_for_deny(self) -> None:
        self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "regress-fields",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /"},
            }
        )
        records = self.records("regress-fields")
        record = records[0]
        # Spec-required audit fields for ask/deny.
        self.assertEqual(record["decision"], "deny")
        self.assertIsNotNone(record["policy_name"])
        self.assertIn("checks_run", record["metadata"])
        self.assertIn("required_followups", record["metadata"])
        self.assertIn("risk_level", record["metadata"])
        self.assertIn("risk_reasons", record["metadata"])
        # Evidence carries either preflight blocks or classifier reasons.
        self.assertGreater(len(record["evidence"]), 0)


if __name__ == "__main__":
    unittest.main()
