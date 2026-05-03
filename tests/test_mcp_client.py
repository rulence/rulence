from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from rulence.mcp_client import (
    McpClient,
    McpServerHandle,
    McpServerNotFound,
    McpTimeout,
    McpToolError,
    close_all_clients,
    get_client,
)


FAKE = Path(__file__).parent / "_fake_mcp_server.py"


def handle(**kwargs) -> McpServerHandle:
    env = os.environ.copy()
    env.update(kwargs.pop("env", {}))
    return McpServerHandle(
        name=kwargs.pop("name", "fake"),
        command=sys.executable,
        args=(str(FAKE),),
        env=env,
        timeout_seconds=kwargs.pop("timeout_seconds", 1.0),
    )


class McpClientTests(unittest.TestCase):
    def tearDown(self) -> None:
        close_all_clients()

    def test_client_spawns_subprocess_on_first_call(self) -> None:
        client = McpClient(handle())
        tools = client.list_tools()
        try:
            self.assertTrue(any(tool["name"] == "mempalace_search" for tool in tools))
            self.assertIsNotNone(client._process)
        finally:
            client.close()

    def test_client_caches_tool_list(self) -> None:
        client = McpClient(handle())
        try:
            first = client.list_tools()
            second = client.list_tools()
        finally:
            client.close()

        self.assertIs(first, second)

    def test_client_raises_when_command_missing(self) -> None:
        client = McpClient(McpServerHandle(name="missing", command="/nonexistent/rulence-mcp"))

        with self.assertRaises(McpServerNotFound):
            client.list_tools()

    def test_client_respects_timeout(self) -> None:
        client = McpClient(handle(env={"RULENCE_FAKE_MCP_MODE": "slow"}, timeout_seconds=0.2))
        try:
            with self.assertRaises(McpTimeout):
                client.list_tools()
        finally:
            client.close()

    def test_client_propagates_tool_error(self) -> None:
        client = McpClient(handle(env={"RULENCE_FAKE_MCP_MODE": "tool_error"}))
        try:
            with self.assertRaises(McpToolError):
                client.call_tool("mempalace_search", {"query": "rollback"})
        finally:
            client.close()

    def test_client_cleanup_on_context_exit(self) -> None:
        with McpClient(handle()) as client:
            client.list_tools()
            process = client._process

        self.assertIsNotNone(process)
        self.assertIsNotNone(process.poll())

    def test_get_client_caches_by_handle_name(self) -> None:
        server = handle(name="mempalace")

        first = get_client(server)
        second = get_client(server)

        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
