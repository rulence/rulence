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


class _AuditFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.audit_dir = root / "audit"
        self.state_dir = root / "state"
        self.env = {
            "RULENCE_AUDIT_DIR": str(self.audit_dir),
            "RULENCE_STATE_DIR": str(self.state_dir),
        }
        self._env_patch = patch.dict(os.environ, self.env, clear=False)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def run_hook(self, payload: dict) -> tuple[int, dict]:
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(stdout):
            code = main(["hook", "claude-code"])
        return code, json.loads(stdout.getvalue())

    def all_records(self, claude_session_id: str) -> list[dict]:
        rsid = CorrelationIdManager(self.state_dir).derive_session_id(claude_session_id)
        return AuditTraceStore(self.audit_dir).read_session(rsid)


class ClaudeHookDispatchTests(_AuditFixture):
    def test_session_start_writes_audit_event(self) -> None:
        code, _ = self.run_hook(
            {"hook_event_name": "SessionStart", "session_id": "cc-disp-1"}
        )
        self.assertEqual(code, 0)
        records = self.all_records("cc-disp-1")
        self.assertTrue(any(r["event_type"] == "SessionStart" for r in records))

    def test_user_prompt_submit_writes_audit_event(self) -> None:
        self.run_hook({"hook_event_name": "SessionStart", "session_id": "cc-disp-2"})
        code, _ = self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "cc-disp-2",
                "prompt": "synthetic prompt; not a real secret",
            }
        )
        self.assertEqual(code, 0)
        records = self.all_records("cc-disp-2")
        ups = [r for r in records if r["event_type"] == "UserPromptSubmit"]
        self.assertEqual(len(ups), 1)
        self.assertIsNotNone(ups[0]["corr_id"])
        # The hash is recorded; no plaintext leaks.
        joined = json.dumps(records)
        self.assertNotIn("synthetic prompt", joined)

    def test_pretooluse_with_bash_writes_audit_event(self) -> None:
        self.run_hook({"hook_event_name": "SessionStart", "session_id": "cc-disp-3"})
        self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "cc-disp-3",
                "prompt": "list files",
            }
        )
        code, output = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "cc-disp-3",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
            }
        )
        self.assertEqual(code, 0)
        records = self.all_records("cc-disp-3")
        pre = [r for r in records if r["event_type"] == "PreToolUse"]
        self.assertEqual(len(pre), 1)
        self.assertEqual(pre[0]["tool_name"], "Bash")

    def test_pretooluse_with_read_writes_audit_event(self) -> None:
        self.run_hook({"hook_event_name": "SessionStart", "session_id": "cc-disp-4"})
        code, _ = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "cc-disp-4",
                "tool_name": "Read",
                "tool_input": {"file_path": "/etc/hostname"},
            }
        )
        self.assertEqual(code, 0)
        records = self.all_records("cc-disp-4")
        read_records = [r for r in records if r.get("tool_name") == "Read"]
        self.assertEqual(len(read_records), 1)

    def test_posttooluse_writes_audit_event(self) -> None:
        self.run_hook({"hook_event_name": "SessionStart", "session_id": "cc-disp-5"})
        self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "cc-disp-5",
                "prompt": "list files",
            }
        )
        code, _ = self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "cc-disp-5",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_response": {"ok": True},
            }
        )
        self.assertEqual(code, 0)
        records = self.all_records("cc-disp-5")
        self.assertTrue(any(r["event_type"] == "PostToolUse" for r in records))

    def test_stop_writes_audit_event(self) -> None:
        self.run_hook({"hook_event_name": "SessionStart", "session_id": "cc-disp-6"})
        code, _ = self.run_hook(
            {"hook_event_name": "Stop", "session_id": "cc-disp-6"}
        )
        self.assertEqual(code, 0)
        records = self.all_records("cc-disp-6")
        self.assertTrue(any(r["event_type"] == "Stop" for r in records))

    def test_session_end_writes_audit_event(self) -> None:
        self.run_hook({"hook_event_name": "SessionStart", "session_id": "cc-disp-7"})
        code, _ = self.run_hook(
            {"hook_event_name": "SessionEnd", "session_id": "cc-disp-7"}
        )
        self.assertEqual(code, 0)
        records = self.all_records("cc-disp-7")
        self.assertTrue(any(r["event_type"] == "SessionEnd" for r in records))

    def test_one_logical_turn_reuses_corr_id_across_events(self) -> None:
        self.run_hook({"hook_event_name": "SessionStart", "session_id": "cc-disp-8"})
        self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "cc-disp-8",
                "prompt": "do a thing",
            }
        )
        self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "cc-disp-8",
                "tool_name": "Bash",
                "tool_input": {"command": "echo hi"},
            }
        )
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "cc-disp-8",
                "tool_name": "Bash",
                "tool_input": {"command": "echo hi"},
                "tool_response": {"ok": True},
            }
        )
        self.run_hook(
            {"hook_event_name": "Stop", "session_id": "cc-disp-8"}
        )

        records = self.all_records("cc-disp-8")
        # SessionStart has no corr_id (none yet); the rest share one.
        non_start = [r for r in records if r["event_type"] != "SessionStart"]
        corr_ids = {r["corr_id"] for r in non_start if r.get("corr_id")}
        self.assertEqual(len(corr_ids), 1, f"corr_ids: {corr_ids}")

    def test_existing_pretooluse_block_behavior_preserved(self) -> None:
        code, output = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "cc-disp-9",
                "tool_name": "Bash",
                "tool_input": {"command": "delete production credentials"},
            }
        )
        self.assertEqual(code, 0)
        decision = output["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision, "deny")
        self.assertIn("Rulence blocked", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_unknown_event_audited_and_allowed(self) -> None:
        code, output = self.run_hook(
            {"hook_event_name": "SomethingNew", "session_id": "cc-disp-10"}
        )
        self.assertEqual(code, 0)
        self.assertEqual(output, {"suppressOutput": True})
        records = self.all_records("cc-disp-10")
        self.assertTrue(any(r["event_type"] == "SomethingNew" for r in records))


if __name__ == "__main__":
    unittest.main()
