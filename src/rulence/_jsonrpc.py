from __future__ import annotations

import json
from typing import Any, BinaryIO


FramedMessage = tuple[dict[str, Any] | None, str]


def read_message(stream: BinaryIO) -> FramedMessage:
    first = stream.readline()
    if not first:
        return None, "headers"
    if first.lstrip().startswith(b"{"):
        return json.loads(first.decode("utf-8")), "jsonl"

    headers: dict[str, str] = {}
    line = first
    while True:
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("ascii").partition(":")
        headers[key.lower()] = value.strip()
        line = stream.readline()
        if not line:
            return None, "headers"

    length = int(headers.get("content-length", "0"))
    if length <= 0:
        raise ValueError("invalid or missing Content-Length header")
    raw = stream.read(length)
    return json.loads(raw.decode("utf-8")), "headers"


def write_message(stream: BinaryIO, message: dict[str, Any], framing: str = "headers") -> None:
    raw = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if framing == "jsonl":
        stream.write(raw + b"\n")
        stream.flush()
        return
    stream.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii"))
    stream.write(raw)
    stream.flush()
