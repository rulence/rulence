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

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "claude_hooks"


class McpFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.audit_dir = root / "audit"
        self.state_dir = root / "state"
        env = {
            "RULENCE_AUDIT_DIR": str(self.audit_dir),
            "RULENCE_STATE_DIR": str(self.state_dir),
        }
        self._env_patch = patch.dict(os.environ, env, clear=False)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _run(self, fixture_name: str) -> tuple[int, dict, dict]:
        path = FIXTURES / fixture_name
        payload = json.loads(path.read_text(encoding="utf-8"))
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(stdout):
            code = main(["hook", "claude-code"])
        return code, json.loads(stdout.getvalue()), payload

    def test_pretooluse_mcp_memory_create_entities_payload_parses(self) -> None:
        code, output, payload = self._run("pretooluse_mcp_memory_create_entities.json")
        self.assertEqual(code, 0)
        self.assertEqual(payload["tool_name"], "mcp__memory__create_entities")
        self.assertEqual(payload["hook_event_name"], "PreToolUse")
        # Output is one of the known PreToolUse shapes.
        self.assertTrue(
            "hookSpecificOutput" in output or output == {"suppressOutput": True}
        )
        rsid = CorrelationIdManager(self.state_dir).derive_session_id(payload["session_id"])
        records = AuditTraceStore(self.audit_dir).read_session(rsid)
        pre = [r for r in records if r["event_type"] == "PreToolUse"]
        self.assertEqual(len(pre), 1)
        self.assertEqual(pre[0]["tool_name"], "mcp__memory__create_entities")

    def test_posttooluse_mcp_memory_create_entities_payload_parses(self) -> None:
        code, output, payload = self._run("posttooluse_mcp_memory_create_entities.json")
        self.assertEqual(code, 0)
        self.assertEqual(payload["tool_name"], "mcp__memory__create_entities")
        self.assertEqual(payload["hook_event_name"], "PostToolUse")
        # PostToolUse always allows; output is suppress/allow.
        self.assertEqual(output, {"suppressOutput": True})
        rsid = CorrelationIdManager(self.state_dir).derive_session_id(payload["session_id"])
        records = AuditTraceStore(self.audit_dir).read_session(rsid)
        post = [r for r in records if r["event_type"] == "PostToolUse"]
        self.assertEqual(len(post), 1)
        self.assertEqual(post[0]["tool_name"], "mcp__memory__create_entities")


if __name__ == "__main__":
    unittest.main()
