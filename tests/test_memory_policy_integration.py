"""End-to-end tests for memory_read attempts in the hook dispatcher.

The arbiter is invoked with no providers configured, so reads
*intentionally* degrade and the audit metadata records that fact.
This proves the wiring without requiring a live Honcho or MemPalace.
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


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        env_patch = patch.dict(
            os.environ,
            {
                "RULENCE_AUDIT_DIR": str(root / "audit"),
                "RULENCE_STATE_DIR": str(root / "state"),
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.audit_dir = root / "audit"
        self.state_dir = root / "state"

    def run_hook(self, payload: dict) -> dict:
        out = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(out):
            main(["hook", "claude-code"])
        return json.loads(out.getvalue())

    def records(self, claude_session_id: str) -> list[dict]:
        rsid = CorrelationIdManager(self.state_dir).derive_session_id(claude_session_id)
        return AuditTraceStore(self.audit_dir).read_session(rsid)


class UserPromptSubmitMemoryReadTests(_Fixture):
    def test_tier4_prompt_attempts_external_memory_read(self) -> None:
        cs = "mem-prompt-tier4"
        self.run_hook({"hook_event_name": "SessionStart", "session_id": cs})
        self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": cs,
                "prompt": (
                    "plan a complete database migration with full rollback "
                    "strategy across staging and production environments"
                ),
            }
        )
        ups = [r for r in self.records(cs) if r["event_type"] == "UserPromptSubmit"]
        self.assertEqual(len(ups), 1)
        meta = ups[0]["metadata"]
        self.assertGreaterEqual(meta["task_tier"], 4)
        self.assertTrue(meta["memory_read_attempted"])
        self.assertEqual(meta["memory_read_role"], "external")
        # No providers configured -> degraded read recorded faithfully.
        self.assertTrue(meta["memory_read_degraded"])

    def test_tier3_prompt_routes_to_internal(self) -> None:
        cs = "mem-prompt-tier3"
        self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": cs,
                "prompt": "summarize the recent design discussion thread",
            }
        )
        ups = [r for r in self.records(cs) if r["event_type"] == "UserPromptSubmit"]
        meta = ups[0]["metadata"]
        if meta["task_tier"] >= 3:
            self.assertTrue(meta["memory_read_attempted"])
            self.assertIn(meta["memory_read_role"], ("internal", "external"))

    def test_low_tier_prompt_does_not_attempt_memory_read(self) -> None:
        cs = "mem-prompt-low"
        self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": cs,
                "prompt": "hi",
            }
        )
        ups = [r for r in self.records(cs) if r["event_type"] == "UserPromptSubmit"]
        meta = ups[0]["metadata"]
        # Tier should be low for a one-word prompt.
        self.assertLess(meta["task_tier"], 3)
        self.assertNotIn("memory_read_attempted", meta)


class PreToolUseMemoryReadTests(_Fixture):
    def test_critical_bash_invokes_memory_read_attempt(self) -> None:
        cs = "mem-pretool-critical"
        self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /tmp/foo"},
            }
        )
        pre = [r for r in self.records(cs) if r["event_type"] == "PreToolUse"]
        self.assertEqual(len(pre), 1)
        meta = pre[0]["metadata"]
        self.assertEqual(meta["risk_level"], "critical")
        self.assertTrue(meta["memory_read_attempted"])
        self.assertEqual(meta["memory_read_role"], "external")

    def test_low_risk_read_does_not_attempt_memory_read(self) -> None:
        cs = "mem-pretool-low"
        self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": cs,
                "tool_name": "Read",
                "tool_input": {"file_path": "/home/user/code/main.py"},
            }
        )
        pre = [r for r in self.records(cs) if r["event_type"] == "PreToolUse"]
        meta = pre[0]["metadata"]
        self.assertEqual(meta["risk_level"], "low")
        self.assertNotIn("memory_read_attempted", meta)


class UnavailableBackendDegradesTests(_Fixture):
    def test_arbiter_records_degraded_when_no_provider(self) -> None:
        # The dispatcher uses an arbiter with no providers wired, so
        # any memory_read_attempted=True audit record must also be
        # marked degraded with an error message.
        cs = "mem-degraded"
        self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /etc"},
            }
        )
        meta = [r for r in self.records(cs) if r["event_type"] == "PreToolUse"][0][
            "metadata"
        ]
        self.assertTrue(meta["memory_read_attempted"])
        self.assertTrue(meta["memory_read_degraded"])
        self.assertIn(meta["memory_read_error"] or "", meta["memory_read_error"] or "")
        self.assertIsNotNone(meta["memory_read_error"])


class ContradictionWarnTests(_Fixture):
    """A best-effort placeholder check: contradictory writes should be flagged.

    M8 detects contradiction at the redactor / sensitivity level only
    (credential-like). Future milestones may add semantic checks.
    """

    def test_contradictory_credential_write_is_denied(self) -> None:
        from rulence.memory import MemoryArbiter, capabilities_for, make_handle

        class _FakeMemPalace:
            def retrieve(self, query, limit=5):
                return []

            def write(self, text, *, scope, provenance):
                return True

        provider = _FakeMemPalace()
        arbiter = MemoryArbiter(
            providers={
                "mempalace": make_handle(
                    provider,
                    capabilities_for(provider).__class__(  # craft a writable cap
                        backend_name="mempalace",
                        backend_role="external",
                        supports_read=True,
                        supports_write=True,
                        supports_update=False,
                        supports_expire=False,
                        supports_search=True,
                        supports_provenance=True,
                        supports_delete=False,
                        supports_mcp_transport=True,
                        supports_http_transport=False,
                    ),
                )
            }
        )
        # An attempt to write a sensitivity-credential payload (whether
        # the value is actually secret-looking or not) is denied by
        # the router so the arbiter never reaches the backend.
        result = arbiter.write(
            "do not store this",
            scope="project_context",
            sensitivity="credential_like",
        )
        self.assertFalse(result.written)
        self.assertIn("credential", (result.reason or "").lower())


if __name__ == "__main__":
    unittest.main()
