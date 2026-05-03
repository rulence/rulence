from __future__ import annotations

import json
from typing import Any, BinaryIO


FramedMessage = tuple[dict[str, Any] | None, str]

MAX_MESSAGE_BYTES = 16 * 1024 * 1024


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

    raw_length = headers.get("content-length", "")
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise ValueError(f"invalid Content-Length header: {raw_length!r}") from exc
    if length <= 0:
        raise ValueError("invalid or missing Content-Length header")
    if length > MAX_MESSAGE_BYTES:
        raise ValueError(
            f"Content-Length {length} exceeds MAX_MESSAGE_BYTES ({MAX_MESSAGE_BYTES})"
        )
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
