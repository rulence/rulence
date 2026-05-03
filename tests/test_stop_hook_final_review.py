"""Stop hook integration tests for the final-response review path."""
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

FAKE_GITHUB_TOKEN = "ghp_FAKEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _write_transcript(directory: Path, *turns: tuple[str, str]) -> Path:
    path = directory / "transcript.jsonl"
    lines = [
        json.dumps({"role": role, "content": content}) for role, content in turns
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _DispatchFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.audit_dir = self.root / "audit"
        self.state_dir = self.root / "state"
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
            path.read_text(encoding="utf-8")
            for path in sorted(session_dir.glob("*.jsonl"))
        )


class StopHookAlwaysWritesAudit(_DispatchFixture):
    def test_safe_final_writes_pass_audit(self) -> None:
        cs = "stop-safe"
        self.run_hook({"hook_event_name": "SessionStart", "session_id": cs})
        self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": cs,
                "prompt": "list the files",
            }
        )
        transcript = _write_transcript(
            self.root,
            ("user", "list the files"),
            ("assistant", "Here are the files. Let me know if you'd like more."),
        )
        out = self.run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": cs,
                "transcript_path": str(transcript),
                "stop_hook_active": False,
            }
        )
        self.assertEqual(out, {"suppressOutput": True})
        records = [r for r in self.all_records(cs) if r["event_type"] == "Stop"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["decision"], "pass")
        self.assertEqual(record["metadata"]["review_status"], "pass")


class StopHookDetectsSecretLeakage(_DispatchFixture):
    def test_secret_in_final_returns_block_when_loop_inactive(self) -> None:
        cs = "stop-secret"
        transcript = _write_transcript(
            self.root,
            ("user", "remind me of the deploy token"),
            ("assistant", f"Here you go: {FAKE_GITHUB_TOKEN}"),
        )
        out = self.run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": cs,
                "transcript_path": str(transcript),
                "stop_hook_active": False,
            }
        )
        self.assertEqual(out["decision"], "block")
        self.assertIn("Rulence final-response review failed", out["reason"])
        records = [r for r in self.all_records(cs) if r["event_type"] == "Stop"]
        self.assertEqual(records[-1]["decision"], "fail")

    def test_audit_file_does_not_contain_raw_secret(self) -> None:
        cs = "stop-secret-clean"
        transcript = _write_transcript(
            self.root,
            ("user", "show me the token"),
            ("assistant", f"Here it is: {FAKE_GITHUB_TOKEN}"),
        )
        self.run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": cs,
                "transcript_path": str(transcript),
                "stop_hook_active": False,
            }
        )
        text = self.audit_text(cs)
        self.assertTrue(text)
        self.assertNotIn(FAKE_GITHUB_TOKEN, text)


class StopHookConstraintEnforcement(_DispatchFixture):
    def test_forbids_violation_returns_block(self) -> None:
        cs = "stop-forbids"
        transcript = _write_transcript(
            self.root,
            ("user", "forbids: deploy -> friday\nplease release the new build"),
            ("assistant", "I deployed the new build on Friday."),
        )
        out = self.run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": cs,
                "transcript_path": str(transcript),
                "stop_hook_active": False,
            }
        )
        self.assertEqual(out["decision"], "block")
        records = [r for r in self.all_records(cs) if r["event_type"] == "Stop"]
        self.assertEqual(records[-1]["decision"], "fail")
        self.assertTrue(records[-1]["metadata"]["violated_constraints"])

    def test_requires_omission_warns_without_blocking(self) -> None:
        cs = "stop-requires"
        transcript = _write_transcript(
            self.root,
            ("user", "requires: deploy -> tests"),
            ("assistant", "I deployed the new build."),
        )
        out = self.run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": cs,
                "transcript_path": str(transcript),
                "stop_hook_active": False,
            }
        )
        # warn -> no block, audit only.
        self.assertEqual(out, {"suppressOutput": True})
        records = [r for r in self.all_records(cs) if r["event_type"] == "Stop"]
        self.assertEqual(records[-1]["decision"], "warn")
        self.assertTrue(records[-1]["metadata"]["missing_required_actions"])


class StopHookActionClaimWithoutTrace(_DispatchFixture):
    def test_claims_test_run_without_bash_trace_warns(self) -> None:
        cs = "stop-claims-test"
        # No PreToolUse Bash event in this session.
        self.run_hook({"hook_event_name": "SessionStart", "session_id": cs})
        self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": cs,
                "prompt": "summarize the project",
            }
        )
        transcript = _write_transcript(
            self.root,
            ("user", "summarize the project"),
            ("assistant", "I ran the tests and everything passed."),
        )
        out = self.run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": cs,
                "transcript_path": str(transcript),
                "stop_hook_active": False,
            }
        )
        self.assertEqual(out, {"suppressOutput": True})
        records = [r for r in self.all_records(cs) if r["event_type"] == "Stop"]
        self.assertEqual(records[-1]["decision"], "warn")
        self.assertTrue(records[-1]["metadata"]["trace_mismatches"])

    def test_claims_file_edit_without_edit_trace_warns(self) -> None:
        cs = "stop-claims-edit"
        self.run_hook({"hook_event_name": "SessionStart", "session_id": cs})
        self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": cs,
                "prompt": "explain the code",
            }
        )
        transcript = _write_transcript(
            self.root,
            ("user", "explain the code"),
            ("assistant", "I updated the config file with the new setting."),
        )
        self.run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": cs,
                "transcript_path": str(transcript),
                "stop_hook_active": False,
            }
        )
        records = [r for r in self.all_records(cs) if r["event_type"] == "Stop"]
        self.assertEqual(records[-1]["decision"], "warn")
        self.assertTrue(records[-1]["metadata"]["trace_mismatches"])


class StopHookLoopProtection(_DispatchFixture):
    def test_stop_hook_active_does_not_block_again(self) -> None:
        cs = "stop-loop"
        transcript = _write_transcript(
            self.root,
            ("user", "give me the deploy token"),
            ("assistant", f"Token: {FAKE_GITHUB_TOKEN}"),
        )
        out = self.run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": cs,
                "transcript_path": str(transcript),
                "stop_hook_active": True,
            }
        )
        # Even though the review still records a "fail", the dispatcher
        # MUST NOT request another continuation when stop_hook_active is
        # True. Otherwise we'd loop.
        self.assertEqual(out, {"suppressOutput": True})
        records = [r for r in self.all_records(cs) if r["event_type"] == "Stop"]
        self.assertEqual(records[-1]["decision"], "fail")
        self.assertTrue(records[-1]["metadata"]["stop_hook_active"])


class AuditRecordIncludesReviewMetadata(_DispatchFixture):
    def test_audit_records_carry_review_metadata(self) -> None:
        cs = "stop-meta"
        transcript = _write_transcript(
            self.root,
            ("user", "ok"),
            ("assistant", "All set."),
        )
        self.run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": cs,
                "transcript_path": str(transcript),
                "stop_hook_active": False,
            }
        )
        record = [r for r in self.all_records(cs) if r["event_type"] == "Stop"][-1]
        meta = record["metadata"]
        for field in (
            "review_status",
            "suggested_action",
            "violated_constraints",
            "missing_required_actions",
            "trace_mismatches",
            "secret_finding_count",
            "audit_records_inspected",
            "stop_hook_active",
        ):
            self.assertIn(field, meta, f"missing field {field}")


if __name__ == "__main__":
    unittest.main()
