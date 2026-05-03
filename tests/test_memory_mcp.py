from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rulence.config import HonchoConfig, MempalaceConfig, MemoryConfig, load_memory_config
from rulence.mcp_client import close_all_clients
from rulence.memory import MempalaceMcpProvider, MemoryItem, _dedup_by_text, memory_health, retrieve_combined
from rulence.mcp_server import handle_request


FAKE = Path(__file__).parent / "_fake_mcp_server.py"


def mempalace_config() -> MempalaceConfig:
    return MempalaceConfig(command=sys.executable, args=(str(FAKE),), timeout_seconds=1.0)


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

[honcho]
url = "http://127.0.0.1:9999"
api_key_env = "CUSTOM_HONCHO_KEY"

[priority]
order = ["honcho", "mempalace"]
""",
                encoding="utf-8",
            )

            config = load_memory_config(path)

        self.assertEqual(config.mempalace.command, "python3")
        self.assertEqual(config.mempalace.args, ("server.py",))
        self.assertEqual(config.honcho.api_key_env, "CUSTOM_HONCHO_KEY")
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

    def test_retrieve_combined_keeps_working_for_local_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.md"
            path.write_text("rollback backup migration\n\nunrelated", encoding="utf-8")

            items = retrieve_combined(["local"], "rollback migration", paths={"local": str(path)}, limit=2)

        self.assertEqual(items[0].score, 2)


if __name__ == "__main__":
    unittest.main()
