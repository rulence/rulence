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
from rulence.tool_risk import ToolRiskClassifier, parse_mcp_tool_name

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "claude_hooks"


class ParseMcpToolNameTests(unittest.TestCase):
    def test_parses_server_and_tool(self) -> None:
        self.assertEqual(parse_mcp_tool_name("mcp__honcho__search"), ("honcho", "search"))
        self.assertEqual(parse_mcp_tool_name("mcp__mempalace__store"), ("mempalace", "store"))
        self.assertEqual(
            parse_mcp_tool_name("mcp__github__create_issue"), ("github", "create_issue")
        )

    def test_returns_none_for_non_mcp_names(self) -> None:
        self.assertEqual(parse_mcp_tool_name("Bash"), (None, None))
        self.assertEqual(parse_mcp_tool_name("mcp__only_one"), (None, None))
        self.assertEqual(parse_mcp_tool_name("mcp____missing_server"), (None, None))
        self.assertEqual(parse_mcp_tool_name(""), (None, None))


class McpRiskClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.classifier = ToolRiskClassifier()

    def test_honcho_search_is_memory_read_medium(self) -> None:
        result = self.classifier.classify(
            tool_name="mcp__honcho__search",
            tool_input={"query": "rollback notes"},
        )
        self.assertEqual(result.risk_level, "medium")
        self.assertTrue(result.should_run_full_preflight)
        joined_reasons = " ".join(result.risk_reasons)
        self.assertIn("memory_read", joined_reasons)
        self.assertIn("honcho", joined_reasons)

    def test_mempalace_store_is_memory_write_high(self) -> None:
        result = self.classifier.classify(
            tool_name="mcp__mempalace__store",
            tool_input={"wing": "x", "room": "y", "memory": {}},
        )
        self.assertEqual(result.risk_level, "high")
        self.assertTrue(result.requires_memory_check)
        joined_reasons = " ".join(result.risk_reasons)
        self.assertIn("memory_write", joined_reasons)
        self.assertIn("mempalace", joined_reasons)

    def test_memory_create_entities_is_high(self) -> None:
        # The memory MCP server's create_entities tool is a write.
        result = self.classifier.classify(
            tool_name="mcp__memory__create_entities",
            tool_input={"entities": []},
        )
        self.assertEqual(result.risk_level, "high")
        self.assertIn("memory_write", " ".join(result.risk_reasons))

    def test_github_create_issue_is_external_write_medium(self) -> None:
        result = self.classifier.classify(
            tool_name="mcp__github__create_issue",
            tool_input={"owner": "x", "repo": "y", "title": "z"},
        )
        self.assertEqual(result.risk_level, "medium")
        self.assertIn("external_write", " ".join(result.risk_reasons))

    def test_github_search_is_low(self) -> None:
        result = self.classifier.classify(
            tool_name="mcp__github__search_code",
            tool_input={"q": "TODO"},
        )
        self.assertEqual(result.risk_level, "low")

    def test_unknown_mcp_server_is_medium(self) -> None:
        result = self.classifier.classify(
            tool_name="mcp__some_unknown__do_thing",
            tool_input={},
        )
        self.assertEqual(result.risk_level, "medium")
        self.assertIn("unknown_server", " ".join(result.risk_reasons))

    def test_malformed_mcp_name_is_medium(self) -> None:
        result = self.classifier.classify(
            tool_name="mcp__only_one",
            tool_input={},
        )
        self.assertEqual(result.risk_level, "medium")
        self.assertIn("malformed_name", " ".join(result.risk_reasons))


class McpFixtureRoundTripTests(unittest.TestCase):
    """End-to-end: the bundled MCP fixtures dispatch and produce audit events."""

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

    def _run(self, fixture_name: str) -> tuple[dict, dict]:
        path = FIXTURES / fixture_name
        payload = json.loads(path.read_text(encoding="utf-8"))
        out = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(out):
            main(["hook", "claude-code"])
        return payload, json.loads(out.getvalue())

    def _records(self, claude_session_id: str) -> list[dict]:
        rsid = CorrelationIdManager(self.state_dir).derive_session_id(claude_session_id)
        return AuditTraceStore(self.audit_dir).read_session(rsid)

    def test_honcho_search_fixture_produces_audit(self) -> None:
        payload, _ = self._run("pretooluse_mcp_honcho_search.json")
        records = self._records(payload["session_id"])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["tool_name"], "mcp__honcho__search")
        # Memory reads are medium; full preflight runs, no fast path.
        self.assertFalse(record["metadata"]["fast_path"])
        self.assertEqual(record["metadata"]["risk_level"], "medium")

    def test_mempalace_store_fixture_runs_full_preflight(self) -> None:
        payload, _ = self._run("pretooluse_mcp_mempalace_store.json")
        records = self._records(payload["session_id"])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["tool_name"], "mcp__mempalace__store")
        self.assertFalse(record["metadata"]["fast_path"])
        self.assertEqual(record["metadata"]["risk_level"], "high")

    def test_github_create_issue_fixture_runs_full_preflight(self) -> None:
        payload, _ = self._run("pretooluse_mcp_github_create_issue.json")
        records = self._records(payload["session_id"])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["tool_name"], "mcp__github__create_issue")
        self.assertEqual(record["metadata"]["risk_level"], "medium")


if __name__ == "__main__":
    unittest.main()
