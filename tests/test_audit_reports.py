"""End-to-end tests for the rulence audit CLI subcommands.

Drives the dispatcher with synthetic payloads and then runs the
``rulence audit`` reporting commands against the resulting JSONL.
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


def _write_transcript(directory: Path, *turns: tuple[str, str]) -> Path:
    path = directory / "transcript.jsonl"
    lines = [json.dumps({"role": role, "content": content}) for role, content in turns]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _Fixture(unittest.TestCase):
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

    def run_cli(self, args: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(args)
        return code, out.getvalue()

    def session_id_for(self, claude_session: str) -> str:
        return CorrelationIdManager(self.state_dir).derive_session_id(claude_session)


class AuditEventTokenIntegrationTests(_Fixture):
    """Token estimates live alongside other metadata after each event."""

    def test_user_prompt_records_input_estimate(self) -> None:
        cs = "ar-prompt"
        self.run_hook({"hook_event_name": "SessionStart", "session_id": cs})
        self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": cs,
                "prompt": "please summarize the codebase architecture",
            }
        )
        records = AuditTraceStore(self.audit_dir).read_session(self.session_id_for(cs))
        ups = [r for r in records if r["event_type"] == "UserPromptSubmit"]
        self.assertEqual(len(ups), 1)
        meta = ups[0]["metadata"]
        self.assertGreater(meta["input_estimate"], 0)
        self.assertEqual(meta["output_estimate"], 0)
        self.assertGreater(ups[0]["token_estimate"], 0)

    def test_pretooluse_records_input_estimate(self) -> None:
        cs = "ar-pretool"
        self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},
            }
        )
        records = AuditTraceStore(self.audit_dir).read_session(self.session_id_for(cs))
        pre = [r for r in records if r["event_type"] == "PreToolUse"]
        self.assertEqual(len(pre), 1)
        self.assertGreater(pre[0]["metadata"]["input_estimate"], 0)

    def test_posttooluse_records_output_estimate(self) -> None:
        cs = "ar-post"
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_response": "a\nb\nc",
            }
        )
        records = AuditTraceStore(self.audit_dir).read_session(self.session_id_for(cs))
        post = [r for r in records if r["event_type"] == "PostToolUse"]
        self.assertEqual(len(post), 1)
        meta = post[0]["metadata"]
        self.assertEqual(meta["input_estimate"], 0)
        self.assertGreater(meta["output_estimate"], 0)

    def test_stop_records_output_estimate(self) -> None:
        cs = "ar-stop"
        transcript = _write_transcript(
            self.root,
            ("user", "what's up"),
            ("assistant", "All caught up, nothing to do."),
        )
        self.run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": cs,
                "transcript_path": str(transcript),
                "stop_hook_active": False,
            }
        )
        records = AuditTraceStore(self.audit_dir).read_session(self.session_id_for(cs))
        stop = [r for r in records if r["event_type"] == "Stop"]
        self.assertEqual(len(stop), 1)
        self.assertGreater(stop[0]["metadata"]["output_estimate"], 0)


class AuditCliCommandsTests(_Fixture):
    def _make_session(self, cs: str) -> None:
        self.run_hook({"hook_event_name": "SessionStart", "session_id": cs})
        self.run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": cs,
                "prompt": "deploy the staging build",
            }
        )
        self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},
            }
        )
        self.run_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": cs,
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},
                "tool_response": "a\nb\nc\nd\ne\n",
            }
        )
        self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": cs,
                "tool_name": "mcp__honcho__search",
                "tool_input": {"query": "rollback notes"},
            }
        )

    def test_audit_list_includes_session(self) -> None:
        cs = "cli-list-1"
        self._make_session(cs)
        rsid = self.session_id_for(cs)
        code, out = self.run_cli(
            ["audit", "list", "--root", str(self.audit_dir), "--json"]
        )
        self.assertEqual(code, 0)
        self.assertIn(rsid, json.loads(out)["sessions"])

    def test_audit_show_returns_records_for_session(self) -> None:
        cs = "cli-show-1"
        self._make_session(cs)
        rsid = self.session_id_for(cs)
        code, out = self.run_cli(
            [
                "audit",
                "show",
                "--session",
                rsid,
                "--root",
                str(self.audit_dir),
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        records = json.loads(out)
        event_types = {r["event_type"] for r in records}
        self.assertIn("PreToolUse", event_types)
        self.assertIn("PostToolUse", event_types)

    def test_audit_tokens_returns_rollup(self) -> None:
        cs = "cli-tokens-1"
        self._make_session(cs)
        rsid = self.session_id_for(cs)
        code, out = self.run_cli(
            [
                "audit",
                "tokens",
                "--session",
                rsid,
                "--root",
                str(self.audit_dir),
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        rollup = json.loads(out)
        self.assertGreater(rollup["total_estimated"], 0)
        self.assertIn("UserPromptSubmit", rollup["by_event_type"])
        self.assertIn("PreToolUse", rollup["by_event_type"])
        self.assertIn("PostToolUse", rollup["by_event_type"])
        self.assertEqual(rollup["by_memory_backend"]["honcho"], rollup["by_memory_backend"]["honcho"])
        self.assertIn("honcho", rollup["by_memory_backend"])

    def test_audit_report_summarizes_session(self) -> None:
        cs = "cli-report-1"
        self._make_session(cs)
        # Add a Stop with a clean final.
        transcript = _write_transcript(
            self.root,
            ("user", "deploy the staging build"),
            ("assistant", "Done. Let me know if you need anything else."),
        )
        self.run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": cs,
                "transcript_path": str(transcript),
                "stop_hook_active": False,
            }
        )
        rsid = self.session_id_for(cs)
        code, out = self.run_cli(
            [
                "audit",
                "report",
                "--session",
                rsid,
                "--root",
                str(self.audit_dir),
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["session_id"], rsid)
        self.assertGreaterEqual(report["pretooluse_count"], 2)
        self.assertGreaterEqual(report["posttooluse_count"], 1)
        self.assertEqual(report["final_review_status"], "pass")
        self.assertGreater(report["tokens"]["total_estimated"], 0)
        self.assertIn("Bash", report["tool_call_counts"])
        self.assertIn("mcp__honcho__search", report["tool_call_counts"])

    def test_audit_show_filtered_by_corr_id(self) -> None:
        cs = "cli-corr-1"
        self._make_session(cs)
        rsid = self.session_id_for(cs)
        records = AuditTraceStore(self.audit_dir).read_session(rsid)
        corr_ids = {r["corr_id"] for r in records if r["corr_id"]}
        self.assertTrue(corr_ids)
        target = next(iter(corr_ids))
        code, out = self.run_cli(
            [
                "audit",
                "show",
                "--session",
                rsid,
                "--corr",
                target,
                "--root",
                str(self.audit_dir),
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        filtered = json.loads(out)
        for r in filtered:
            self.assertEqual(r["corr_id"], target)


if __name__ == "__main__":
    unittest.main()
