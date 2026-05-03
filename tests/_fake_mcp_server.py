from __future__ import annotations

import json
import os
import sys
import time

from rulence._jsonrpc import read_message, write_message


TOOLS = [
    {"name": "mempalace_search"},
    {"name": "mempalace_list_wings"},
    {"name": "mempalace_list_rooms"},
    {"name": "mempalace_traverse"},
]


def main() -> int:
    mode = os.environ.get("RULENCE_FAKE_MCP_MODE", "normal")
    while True:
        message, framing = read_message(sys.stdin.buffer)
        if message is None:
            return 0
        method = message.get("method")
        request_id = message.get("id")
        if method == "notifications/initialized":
            continue
        if mode == "crash" and method == "tools/list":
            return 3
        if mode == "slow" and method == "tools/list":
            time.sleep(2)
        if method == "initialize":
            write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05"}}, framing)
        elif method == "tools/list":
            write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}, framing)
        elif method == "tools/call":
            if mode == "tool_error":
                write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "tool failed"}}, framing)
                continue
            params = message.get("params", {})
            name = params.get("name")
            result = call_tool(name, params.get("arguments", {}))
            write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": request_id, "result": result}, framing)
        else:
            write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "not found"}}, framing)


def call_tool(name: str, arguments: dict) -> dict:
    if name == "mempalace_search":
        rows = [
            {"text": f"rollback plan for {arguments.get('query')}", "score": 9, "wing": "ops", "room": "migrations"},
            {"text": "shared duplicate", "score": 3, "wing": "ops", "room": "notes"},
        ]
        return {"content": [{"type": "text", "text": json.dumps({"results": rows})}]}
    if name == "mempalace_list_wings":
        return {"content": [{"type": "text", "text": json.dumps(["ops", "personal"])}]}
    if name == "mempalace_list_rooms":
        return {"content": [{"type": "text", "text": json.dumps(["migrations", "notes"])}]}
    if name == "mempalace_traverse":
        return {"content": [{"type": "text", "text": "traversed memory"}]}
    return {"content": [{"type": "text", "text": "unknown"}]}


if __name__ == "__main__":
    raise SystemExit(main())
