"""End-to-end PostToolUse hook tests for tool-result review.

The dispatcher must always write a PostToolUse audit record. The
record must carry the review status and token estimates, and must
never contain the raw synthetic secret.
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

# Synthetic fakes (see test_secret_redactor.py for shape rationale).
FAKE_GITHUB_TOKEN = "ghp_FAKEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
FAKE_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE\n"
    "-----END PRIVATE KEY-----"
)


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

    def all_records(self, claude_session_id: str) -> list[dict]:
        rsid = CorrelationIdManager(self.state_dir).derive_session_id(claude_session_id)
        return AuditTraceStore(self.audit_dir).read_session(rsid)

    def audit_text(self, claude_session_id: str) -> str:
        rsid = CorrelationIdManager(self.state_dir).derive_session_id(claude_session_id)
        session_dir = self.audit_dir / rsid
        if not session_dir.exists():
            return ""
        return "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(session_dir.glob("*.jsonl"))
        )


class PostToolUseAuditAlwaysWrittenTests(_DispatchFixture):
    def test_safe_output_writes_audit_with_safe_status(self) -> None:
        cs = "post-safe"
        out = self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "echo hi"},
                "tool_response": "hi\n",
            }
        )
        self.assertEqual(out, {"suppressOutput": True})
        records = self.all_records(cs)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["event_type"], "PostToolUse")
        self.assertEqual(record["decision"], "safe")
        self.assertTrue(record["metadata"]["safe_for_model_context"])
        self.assertEqual(record["metadata"]["redaction_count"], 0)
        self.assertIn("original_token_estimate", record["metadata"])
        self.assertIn("filtered_token_estimate", record["metadata"])

    def test_audit_record_contains_token_estimates(self) -> None:
        cs = "post-tokens"
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_response": "a\nb\nc",
            }
        )
        record = self.all_records(cs)[0]
        meta = record["metadata"]
        self.assertGreaterEqual(meta["original_token_estimate"], 1)
        self.assertGreaterEqual(meta["filtered_token_estimate"], 1)


class PostToolUseSecretRedactionTests(_DispatchFixture):
    def test_secret_in_tool_response_is_recorded_as_redacted(self) -> None:
        cs = "post-secret"
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "echo $TOKEN"},
                "tool_response": f"echoed {FAKE_GITHUB_TOKEN}",
            }
        )
        record = self.all_records(cs)[0]
        self.assertEqual(record["decision"], "warn")
        self.assertGreaterEqual(record["metadata"]["redaction_count"], 1)
        self.assertIn("github_token", record["metadata"]["redaction_types"])
        self.assertFalse(record["metadata"]["safe_for_model_context"])

    def test_raw_fake_secret_does_not_appear_in_audit_file(self) -> None:
        cs = "post-secret-clean"
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "echo $TOKEN"},
                "tool_response": f"got token {FAKE_GITHUB_TOKEN}",
            }
        )
        text = self.audit_text(cs)
        self.assertTrue(text)
        self.assertNotIn(FAKE_GITHUB_TOKEN, text)

    def test_pem_block_in_output_is_marked_unsafe(self) -> None:
        cs = "post-pem"
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": cs,
                "tool_name": "Read",
                "tool_input": {"file_path": "/keys/id"},
                "tool_response": FAKE_PEM,
            }
        )
        record = self.all_records(cs)[0]
        self.assertEqual(record["decision"], "unsafe")
        self.assertEqual(record["metadata"]["suggested_action"], "block")
        self.assertFalse(record["metadata"]["safe_for_model_context"])

    def test_audit_file_does_not_contain_pem_payload(self) -> None:
        cs = "post-pem-clean"
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": cs,
                "tool_name": "Read",
                "tool_input": {"file_path": "/keys/id"},
                "tool_response": FAKE_PEM,
            }
        )
        text = self.audit_text(cs)
        self.assertNotIn("BEGIN PRIVATE KEY", text)
        self.assertNotIn("END PRIVATE KEY", text)


class PostToolUseLargeOutputTests(_DispatchFixture):
    def test_large_output_is_warned_or_compacted(self) -> None:
        cs = "post-large"
        big = "\n".join(f"log line {i}" for i in range(2500))
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "yes"},
                "tool_response": big,
            }
        )
        record = self.all_records(cs)[0]
        self.assertIn(record["decision"], ("warn", "unsafe"))
        self.assertIn(
            "summary_omitted_sections",
            record["metadata"],
            f"expected compaction metadata; got {record['metadata']!r}",
        )
        self.assertGreater(
            record["metadata"]["original_token_estimate"],
            record["metadata"]["filtered_token_estimate"],
        )


class PostToolUseBinaryOutputTests(_DispatchFixture):
    def test_binary_like_output_is_unsafe(self) -> None:
        cs = "post-binary"
        binary = "".join(chr(i % 256) for i in range(2000))
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": cs,
                "tool_name": "Read",
                "tool_input": {"file_path": "/bin/x"},
                "tool_response": binary,
            }
        )
        record = self.all_records(cs)[0]
        self.assertEqual(record["decision"], "unsafe")
        self.assertFalse(record["metadata"]["safe_for_model_context"])


class PostToolUseTestFailureTests(_DispatchFixture):
    def test_test_failure_evidence_is_recorded(self) -> None:
        cs = "post-failure"
        body = (
            "PASS test_a\nPASS test_b\n"
            "FAILED tests/test_thing.py::test_x AssertionError: expected 1 got 2\n"
        )
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "pytest"},
                "tool_response": body,
            }
        )
        record = self.all_records(cs)[0]
        self.assertIn("contains test failure markers", record["evidence"])


if __name__ == "__main__":
    unittest.main()
