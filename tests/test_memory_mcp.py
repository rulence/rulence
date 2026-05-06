from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from rulence.cli import main as cli_main
from rulence.config import HonchoConfig, MempalaceConfig, MemoryConfig, load_memory_config
from rulence.mcp_client import close_all_clients
from rulence.memory import MempalaceMcpProvider, MemoryItem, _dedup_by_text, memory_health, retrieve_combined
from rulence.mcp_server import handle_request


FAKE = Path(__file__).parent / "_fake_mcp_server.py"


def mempalace_config() -> MempalaceConfig:
    return MempalaceConfig(command=sys.executable, args=(str(FAKE),), timeout_seconds=1.0)


def mempalace_jsonl_config() -> MempalaceConfig:
    return MempalaceConfig(command=sys.executable, args=(str(FAKE),), timeout_seconds=1.0, framing="jsonl")


class MemoryMcpTests(unittest.TestCase):
    def tearDown(self) -> None:
        close_all_clients()

    def test_mempalace_provider_calls_search_tool(self) -> None:
        provider = MempalaceMcpProvider(mempalace_config())

        items = provider.retrieve("migration rollback", limit=2)

        self.assertEqual(items[0].source, "mempalace:ops/migrations")
        self.assertIn("migration rollback", items[0].text)

    def test_mempalace_provider_lists_wings_and_rooms(self) -> None:
        provider = MempalaceMcpProvider(mempalace_config())

        self.assertEqual(provider.list_wings(), ["ops", "personal"])
        self.assertEqual(provider.list_rooms("ops"), ["migrations", "notes"])

    def test_mempalace_health_returns_tool_count(self) -> None:
        provider = MempalaceMcpProvider(mempalace_config())

        health = provider.health()

        self.assertTrue(health["reachable"])
        self.assertGreaterEqual(health["tool_count"], 4)

    def test_mempalace_provider_supports_jsonl_framing(self) -> None:
        provider = MempalaceMcpProvider(mempalace_jsonl_config())

        health = provider.health()

        self.assertTrue(health["reachable"])
        self.assertGreaterEqual(health["tool_count"], 4)

    def test_memory_health_reports_missing_mempalace_without_crashing(self) -> None:
        config = MemoryConfig(mempalace=MempalaceConfig(command="/nonexistent/mempalace-mcp"))
        with patch("rulence.memory.load_memory_config", return_value=config):
            health = memory_health("mempalace")

        self.assertFalse(health["reachable"])
        self.assertIn("command not found", health["error"])

    def test_combined_retrieval_dedups_by_config_priority(self) -> None:
        items = [
            MemoryItem(source="honcho", text="rollback plan committed", score=10),
            MemoryItem(source="mempalace:ops/migrations", text="rollback plan committed", score=3),
        ]

        deduped = _dedup_by_text(items, ("mempalace", "honcho", "local"))

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].source, "mempalace:ops/migrations")

    def test_load_memory_config_reads_toml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.toml"
            path.write_text(
                """
[mempalace]
command = "python3"
args = ["server.py"]
timeout_seconds = 1.5
framing = "jsonl"

[honcho]
url = "http://127.0.0.1:9999"
api_key_env = "CUSTOM_HONCHO_KEY"

[local]
path = "~/rulence-checkpoints"

[priority]
order = ["honcho", "mempalace"]
""",
                encoding="utf-8",
            )

            config = load_memory_config(path)

        self.assertEqual(config.mempalace.command, "python3")
        self.assertEqual(config.mempalace.args, ("server.py",))
        self.assertEqual(config.mempalace.framing, "jsonl")
        self.assertEqual(config.honcho.api_key_env, "CUSTOM_HONCHO_KEY")
        self.assertEqual(config.local.path, "~/rulence-checkpoints")
        self.assertEqual(config.priority, ("honcho", "mempalace"))

    def test_mcp_memory_health_tool(self) -> None:
        response = handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "rulence_memory_health", "arguments": {"provider": "local"}},
            }
        )

        self.assertIsNotNone(response)
        text = response["result"]["content"][0]["text"]
        self.assertIn("reachable", text)

    def test_cli_memory_wings_reports_missing_mempalace_without_crashing(self) -> None:
        config = MemoryConfig(mempalace=MempalaceConfig(command="/nonexistent/mempalace-mcp"))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("rulence.memory.load_memory_config", return_value=config):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli_main(["memory", "wings", "--json"])

        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["reachable"])
        self.assertIn("command not found", payload["error"])
        self.assertEqual(payload["wings"], [])

    def test_cli_memory_rooms_reports_missing_mempalace_without_crashing(self) -> None:
        config = MemoryConfig(mempalace=MempalaceConfig(command="/nonexistent/mempalace-mcp"))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("rulence.memory.load_memory_config", return_value=config):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli_main(["memory", "rooms", "--wing", "ops", "--json"])

        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["reachable"])
        self.assertIn("command not found", payload["error"])
        self.assertEqual(payload["wing"], "ops")
        self.assertEqual(payload["rooms"], [])

    def test_cli_memory_query_exits_clean_when_provider_unreachable(self) -> None:
        config = MemoryConfig(mempalace=MempalaceConfig(command="/nonexistent/mempalace-mcp"))
        stderr = io.StringIO()
        with patch("rulence.memory.load_memory_config", return_value=config):
            with redirect_stderr(stderr):
                code = cli_main(["memory", "rollback", "--provider", "mempalace"])

        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertIn("memory provider 'mempalace' unreachable", stderr.getvalue())

    def test_cli_preflight_continues_when_memory_provider_unreachable(self) -> None:
        config = MemoryConfig(mempalace=MempalaceConfig(command="/nonexistent/mempalace-mcp"))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("rulence.memory.load_memory_config", return_value=config):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli_main(["preflight", "compare two architectures", "--memory-provider", "mempalace", "--json"])

        self.assertIn(code, (0, 2))
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertIn("memory provider 'mempalace' unreachable", stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertIn("verdict", payload)

    def test_retrieve_combined_keeps_working_for_local_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.md"
            path.write_text("rollback backup migration\n\nunrelated", encoding="utf-8")

            items = retrieve_combined(["local"], "rollback migration", paths={"local": str(path)}, limit=2)

        self.assertEqual(items[0].score, 2)


if __name__ == "__main__":
    unittest.main()
