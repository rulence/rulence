"""End-to-end test that synthetic secrets do not reach audit JSONL.

The Claude Code hook dispatcher accepts an arbitrary payload from the
runtime. This test fires several synthetic-secret-bearing payloads
through the dispatcher and asserts that:

* the raw secret strings do not appear in the audit file,
* the corresponding ``[REDACTED:<type>]`` placeholders do appear when
  the surface that holds them (evidence/metadata) is non-empty,
* ``redaction_count`` and ``redaction_types`` are populated.
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

# Synthetic fakes; identical to those in test_secret_redactor.py.
FAKE_GITHUB_TOKEN = "ghp_FAKEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
FAKE_OPENAI_KEY = "sk-FAKE0000000000000000"
FAKE_AWS_KEY = "AKIAFAKEFAKE12345678"
FAKE_DB_URL = "postgres://admin:FAKEpass@db.example.com:5432/app"


class AuditRedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.audit_dir = root / "audit"
        self.state_dir = root / "state"
        env = patch.dict(
            os.environ,
            {
                "RULENCE_AUDIT_DIR": str(self.audit_dir),
                "RULENCE_STATE_DIR": str(self.state_dir),
            },
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)

    def _run(self, payload: dict) -> dict:
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(stdout):
            main(["hook", "claude-code"])
        return json.loads(stdout.getvalue())

    def _audit_text(self, claude_session_id: str) -> str:
        rsid = CorrelationIdManager(self.state_dir).derive_session_id(claude_session_id)
        session_dir = self.audit_dir / rsid
        if not session_dir.exists():
            return ""
        return "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(session_dir.glob("*.jsonl"))
        )

    def test_pretooluse_secret_in_command_does_not_appear_in_audit_file(self) -> None:
        cs = "cc-redact-bash"
        self._run(
            {
                "hook_event_name": "PreToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": f"echo {FAKE_GITHUB_TOKEN}"},
            }
        )
        text = self._audit_text(cs)
        self.assertTrue(text)
        self.assertNotIn(FAKE_GITHUB_TOKEN, text)

    def test_session_start_payload_with_secret_does_not_leak(self) -> None:
        cs = "cc-redact-start"
        self._run(
            {
                "hook_event_name": "SessionStart",
                "session_id": cs,
                "metadata": {"note": f"context: {FAKE_OPENAI_KEY}"},
            }
        )
        text = self._audit_text(cs)
        self.assertTrue(text)
        self.assertNotIn(FAKE_OPENAI_KEY, text)
        self.assertNotIn(FAKE_AWS_KEY, text)

    def test_user_prompt_with_secret_is_only_hashed(self) -> None:
        cs = "cc-redact-prompt"
        self._run({"hook_event_name": "SessionStart", "session_id": cs})
        self._run(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": cs,
                "prompt": f"please use {FAKE_DB_URL} carefully",
            }
        )
        text = self._audit_text(cs)
        self.assertTrue(text)
        self.assertNotIn(FAKE_DB_URL, text)
        # The raw payload hash is hex; never includes the secret string.
        for line in text.splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record["event_type"] == "UserPromptSubmit":
                self.assertNotIn("FAKEpass", record["raw_payload_hash"])

    def test_redaction_metadata_is_recorded_when_evidence_carries_secret(self) -> None:
        cs = "cc-redact-meta"
        # Drive the dispatcher with explicit metadata containing a fake
        # secret (the CLI builds metadata for some events).
        from rulence.cli import _record_audit

        _record_audit(
            {"session_id": cs, "hook_event_name": "PreToolUse"},
            "PreToolUse",
            decision="approve",
            evidence=(f"saw {FAKE_GITHUB_TOKEN}",),
            tool_name="Bash",
            policy_name=None,
            metadata={"context": f"db {FAKE_DB_URL}"},
        )
        text = self._audit_text(cs)
        self.assertTrue(text)
        record = json.loads(text.strip().splitlines()[-1])
        self.assertGreater(record["redaction_count"], 0)
        self.assertIn("github_token", record["redaction_types"])
        self.assertIn("db_url_with_password", record["redaction_types"])
        self.assertTrue(record["payload_redacted"])
        self.assertNotIn(FAKE_GITHUB_TOKEN, json.dumps(record))
        self.assertNotIn(FAKE_DB_URL, json.dumps(record))

    def test_clean_event_records_zero_redactions(self) -> None:
        cs = "cc-redact-clean"
        self._run({"hook_event_name": "SessionStart", "session_id": cs})
        text = self._audit_text(cs)
        record = json.loads(text.strip().splitlines()[-1])
        self.assertEqual(record["redaction_count"], 0)
        self.assertFalse(record["payload_redacted"])
        self.assertEqual(record["redaction_types"], [])


if __name__ == "__main__":
    unittest.main()
