from __future__ import annotations

import atexit
import os
import selectors
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

from ._jsonrpc import read_message, write_message


class McpError(Exception):
    pass


class McpServerNotFound(McpError):
    pass


class McpServerCrashed(McpError):
    pass


class McpToolError(McpError):
    pass


class McpTimeout(McpError):
    pass


class McpProtocolError(McpError):
    pass


@dataclass(frozen=True)
class McpServerHandle:
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    timeout_seconds: float = 5.0
    framing: str = "headers"


_CLIENTS: dict[str, "McpClient"] = {}
_CLIENTS_LOCK = threading.Lock()


class McpClient:
    def __init__(self, handle: McpServerHandle) -> None:
        self.handle = handle
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._tools: list[dict] | None = None
        self._lock = threading.Lock()
        self._pid = os.getpid()

    def __enter__(self) -> "McpClient":
        self._ensure_started()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def list_tools(self) -> list[dict]:
        if self._tools is not None:
            return self._tools
        response = self._request("tools/list", {})
        tools = response.get("tools", [])
        if not isinstance(tools, list):
            raise McpProtocolError(f"{self.handle.name}: tools/list returned malformed tools")
        self._tools = tools
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._request("tools/call", {"name": name, "arguments": arguments})

    def is_healthy(self) -> bool:
        try:
            self.list_tools()
            return True
        except McpError:
            return False

    def close(self) -> None:
        process = self._process
        self._process = None
        self._tools = None
        if not process:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()

    def _ensure_started(self) -> None:
        if os.getpid() != self._pid:
            self.close()
            self._pid = os.getpid()
        if self._process and self._process.poll() is None:
            return

        env = os.environ.copy()
        if self.handle.env:
            env.update(self.handle.env)
        try:
            self._process = subprocess.Popen(
                [self.handle.command, *self.handle.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise McpServerNotFound(f"{self.handle.name}: command not found: {self.handle.command}") from exc
        except OSError as exc:
            raise McpServerNotFound(f"{self.handle.name}: failed to start {self.handle.command}: {exc}") from exc

        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "rulence", "version": "0.3.0"},
                },
                initialize=True,
            )
            self._notify("notifications/initialized")
        except Exception:
            self.close()
            raise

    def _request(self, method: str, params: dict, initialize: bool = False) -> dict:
        if not initialize:
            self._ensure_started()
        with self._lock:
            process = self._process
            if not process or not process.stdin or not process.stdout:
                raise McpServerCrashed(f"{self.handle.name}: subprocess is not running")
            if process.poll() is not None:
                raise McpServerCrashed(f"{self.handle.name}: subprocess exited with code {process.returncode}")

            self._request_id += 1
            request_id = self._request_id
            write_message(
                process.stdin,
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                self.handle.framing,
            )
            response = self._read_response(process, method)
            if response.get("id") != request_id:
                raise McpProtocolError(f"{self.handle.name}: mismatched response id for {method}")
            if "error" in response:
                error = response["error"]
                message = error.get("message", error) if isinstance(error, dict) else error
                raise McpToolError(f"{self.handle.name}: {method} failed: {message}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise McpProtocolError(f"{self.handle.name}: {method} returned non-object result")
            return result

    def _notify(self, method: str) -> None:
        process = self._process
        if not process or not process.stdin:
            raise McpServerCrashed(f"{self.handle.name}: subprocess is not running")
        write_message(process.stdin, {"jsonrpc": "2.0", "method": method, "params": {}}, self.handle.framing)

    def _read_response(self, process: subprocess.Popen, method: str) -> dict[str, Any]:
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            ready = selector.select(self.handle.timeout_seconds)
        finally:
            selector.close()
        if not ready:
            raise McpTimeout(f"{self.handle.name}: {method} timed out after {self.handle.timeout_seconds:.1f}s")
        if process.poll() is not None:
            raise McpServerCrashed(f"{self.handle.name}: subprocess exited with code {process.returncode}")
        try:
            message, _ = read_message(process.stdout)
        except Exception as exc:
            raise McpProtocolError(f"{self.handle.name}: invalid JSON-RPC response: {exc}") from exc
        if message is None:
            raise McpServerCrashed(f"{self.handle.name}: subprocess closed stdout")
        return message


def get_client(handle: McpServerHandle) -> McpClient:
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(handle.name)
        if client is None or client.handle != handle or os.getpid() != client._pid:
            if client is not None:
                client.close()
            client = McpClient(handle)
            _CLIENTS[handle.name] = client
        return client


def close_all_clients() -> None:
    with _CLIENTS_LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in clients:
        client.close()


atexit.register(close_all_clients)
