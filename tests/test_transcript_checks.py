from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rulence.checks import run_check
from rulence.classifier import classify_task
from rulence.policies import (
    CHECK_TRANSCRIPT_CONTRADICTION,
    CHECK_TRANSCRIPT_DRIFT,
    CHECK_TRANSCRIPT_STALENESS,
    DEFAULT_POLICIES,
)
from rulence.preflight import preflight_task
from rulence.transcript import parse_transcript_text


def _jsonl(*turns: tuple[str, str]) -> str:
    return "\n".join(json.dumps({"role": role, "content": content}) for role, content in turns)


class TranscriptDriftCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DEFAULT_POLICIES[3]
        self.classification = classify_task("deploy on friday")

    def test_passes_when_transcript_is_empty(self) -> None:
        result = run_check(
            CHECK_TRANSCRIPT_DRIFT,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=(),
        )

        self.assertEqual(result.status, "pass")
        self.assertIn("no transcript", result.detail)

    def test_warns_on_keyword_drift_from_first_user_turn(self) -> None:
        turns = parse_transcript_text("plan a database migration with rollback")

        result = run_check(
            CHECK_TRANSCRIPT_DRIFT,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "warn")
        self.assertIn("drift", result.detail.lower())

    def test_passes_when_current_task_shares_keywords_with_first_turn(self) -> None:
        turns = parse_transcript_text("plan the deployment for friday")

        result = run_check(
            CHECK_TRANSCRIPT_DRIFT,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "pass")


class TranscriptContradictionCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DEFAULT_POLICIES[3]
        self.classification = classify_task("deploy on friday")

    def test_passes_when_transcript_is_empty(self) -> None:
        result = run_check(
            CHECK_TRANSCRIPT_CONTRADICTION,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=(),
        )

        self.assertEqual(result.status, "pass")

    def test_blocks_when_task_violates_transcript_forbid(self) -> None:
        turns = parse_transcript_text("don't deploy on friday")

        result = run_check(
            CHECK_TRANSCRIPT_CONTRADICTION,
            "deploy on friday this week",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "block")
        self.assertTrue(any("forbids" in piece for piece in result.evidence))

    def test_warns_when_transcript_requires_unmet(self) -> None:
        turns = parse_transcript_text("never migrate without backup")

        result = run_check(
            CHECK_TRANSCRIPT_CONTRADICTION,
            "migrate the production database now",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "warn")
        self.assertTrue(any("backup" in piece for piece in result.evidence))


class TranscriptStalenessCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DEFAULT_POLICIES[3]
        self.classification = classify_task("deploy on friday")

    def test_passes_when_transcript_is_empty(self) -> None:
        result = run_check(
            CHECK_TRANSCRIPT_STALENESS,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=(),
        )

        self.assertEqual(result.status, "pass")

    def test_warns_when_transcript_turns_are_unrelated_to_current_task(self) -> None:
        turns = parse_transcript_text(
            _jsonl(
                ("user", "plan a migration"),
                ("assistant", "what database?"),
                ("user", "let me pivot to a deployment"),
            )
        )

        result = run_check(
            CHECK_TRANSCRIPT_STALENESS,
            "deploy the app on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "warn")
        self.assertGreaterEqual(len(result.evidence), 1)

    def test_passes_when_every_turn_shares_keywords(self) -> None:
        turns = parse_transcript_text(
            _jsonl(
                ("user", "plan to deploy on friday"),
                ("assistant", "deploy plan looks ready for friday"),
            )
        )

        result = run_check(
            CHECK_TRANSCRIPT_STALENESS,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "pass")


class TranscriptCliTests(unittest.TestCase):
    def _policy_dir(self, base: str, tier: int, check: str) -> str:
        path = Path(base) / f"tier-{tier}-tx.toml"
        path.write_text(
            f'tier = {tier}\nlabel = "tx"\n'
            f'required_checks = ["{check}"]\nwarn_if = []\nblock_if = []\n',
            encoding="utf-8",
        )
        return base

    def test_cli_preflight_with_transcript_path_blocks_when_forbid_matches(self) -> None:
        from io import StringIO
        from contextlib import redirect_stdout
        from rulence.cli import main

        task = "deploy the app on friday this week, please"
        from rulence.classifier import classify_task
        tier = classify_task(task).tier

        with tempfile.TemporaryDirectory() as directory:
            policy_dir = self._policy_dir(directory, tier, CHECK_TRANSCRIPT_CONTRADICTION)
            transcript = Path(directory) / "transcript.jsonl"
            transcript.write_text(
                json.dumps({"role": "user", "content": "don't deploy on friday"}) + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(
                    ["preflight", task, "--policy-dir", policy_dir, "--transcript", str(transcript), "--json"]
                )

        self.assertEqual(code, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["verdict"], "block")

    def test_cli_preflight_rejects_both_transcript_flags(self) -> None:
        from io import StringIO
        from contextlib import redirect_stderr
        from rulence.cli import main

        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "transcript.jsonl"
            transcript.write_text("{}", encoding="utf-8")
            stderr = StringIO()
            with redirect_stderr(stderr):
                code = main(
                    ["preflight", "x", "--transcript", str(transcript), "--transcript-stdin"]
                )

        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", stderr.getvalue())


class PreflightTranscriptIntegrationTests(unittest.TestCase):
    def _policy_dir(self, base: str, required_check: str, tier: int) -> str:
        path = Path(base) / f"tier-{tier}-transcript.toml"
        path.write_text(
            f'tier = {tier}\nlabel = "transcript"\n'
            f'required_checks = ["{required_check}"]\n'
            "warn_if = []\nblock_if = []\n",
            encoding="utf-8",
        )
        return base

    def test_preflight_blocks_when_transcript_forbids_current_action(self) -> None:
        task = "deploy the app on friday this week, please"
        tier = classify_task(task).tier
        with tempfile.TemporaryDirectory() as directory:
            policy_dir = self._policy_dir(directory, CHECK_TRANSCRIPT_CONTRADICTION, tier)
            transcript = "don't deploy on friday"

            result = preflight_task(task, policy_dir=policy_dir, transcript=transcript)

            self.assertEqual(result.verdict, "block")
            self.assertTrue(
                any(
                    check.name == CHECK_TRANSCRIPT_CONTRADICTION and check.status == "block"
                    for check in result.checks
                )
            )

    def test_preflight_no_transcript_lets_check_pass_silently(self) -> None:
        task = "deploy the app on friday this week, please"
        tier = classify_task(task).tier
        with tempfile.TemporaryDirectory() as directory:
            policy_dir = self._policy_dir(directory, CHECK_TRANSCRIPT_DRIFT, tier)

            result = preflight_task(task, policy_dir=policy_dir)

            drift_check = next(check for check in result.checks if check.name == CHECK_TRANSCRIPT_DRIFT)
            self.assertEqual(drift_check.status, "pass")
            self.assertIn("no transcript", drift_check.detail)


class McpTranscriptTests(unittest.TestCase):
    def _policy_dir(self, base: str, tier: int, check: str) -> str:
        path = Path(base) / f"tier-{tier}-tx.toml"
        path.write_text(
            f'tier = {tier}\nlabel = "tx"\n'
            f'required_checks = ["{check}"]\nwarn_if = []\nblock_if = []\n',
            encoding="utf-8",
        )
        return base

    def test_mcp_preflight_blocks_with_inline_transcript(self) -> None:
        from rulence.mcp_server import handle_request

        task = "deploy the app on friday this week"
        from rulence.classifier import classify_task
        tier = classify_task(task).tier

        with tempfile.TemporaryDirectory() as directory:
            policy_dir = self._policy_dir(directory, tier, CHECK_TRANSCRIPT_CONTRADICTION)

            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "rulence_preflight",
                        "arguments": {
                            "task": task,
                            "policy_dir": policy_dir,
                            "transcript": "don't deploy on friday",
                        },
                    },
                }
            )

        payload = response["result"]["structuredContent"]
        self.assertEqual(payload["verdict"], "block")

    def test_mcp_preflight_reads_transcript_path_server_side(self) -> None:
        from rulence.mcp_server import handle_request

        task = "deploy the app on friday this week"
        from rulence.classifier import classify_task
        tier = classify_task(task).tier

        with tempfile.TemporaryDirectory() as directory:
            policy_dir = self._policy_dir(directory, tier, CHECK_TRANSCRIPT_CONTRADICTION)
            transcript_path = Path(directory) / "transcript.jsonl"
            transcript_path.write_text(
                json.dumps({"role": "user", "content": "don't deploy on friday"}) + "\n",
                encoding="utf-8",
            )

            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "rulence_preflight",
                        "arguments": {
                            "task": task,
                            "policy_dir": policy_dir,
                            "transcript_path": str(transcript_path),
                        },
                    },
                }
            )

        payload = response["result"]["structuredContent"]
        self.assertEqual(payload["verdict"], "block")

    def test_mcp_preflight_silently_falls_back_when_transcript_path_missing(self) -> None:
        from rulence.mcp_server import handle_request

        task = "deploy the app on friday this week"
        from rulence.classifier import classify_task
        tier = classify_task(task).tier

        with tempfile.TemporaryDirectory() as directory:
            policy_dir = self._policy_dir(directory, tier, CHECK_TRANSCRIPT_CONTRADICTION)

            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "rulence_preflight",
                        "arguments": {
                            "task": task,
                            "policy_dir": policy_dir,
                            "transcript_path": "/no/such/transcript.jsonl",
                        },
                    },
                }
            )

        payload = response["result"]["structuredContent"]
        check = next(c for c in payload["checks"] if c["name"] == CHECK_TRANSCRIPT_CONTRADICTION)
        self.assertEqual(check["status"], "pass")
        self.assertIn("no transcript", check["detail"])


if __name__ == "__main__":
    unittest.main()
