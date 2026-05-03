from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MAX_TRANSCRIPT_BYTES = 1 * 1024 * 1024


@dataclass(frozen=True)
class TranscriptTurn:
    index: int
    role: str
    content: str

    def to_dict(self) -> dict:
        return {"index": self.index, "role": self.role, "content": self.content}


def parse_transcript_text(text: str) -> tuple[TranscriptTurn, ...]:
    if not text or not text.strip():
        return ()

    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        turns = _try_parse_jsonl(text)
        if turns is not None:
            return turns

    return (TranscriptTurn(index=0, role="user", content=text.strip()),)


def parse_transcript_path(path: str | Path) -> tuple[TranscriptTurn, ...]:
    resolved = Path(path).expanduser()
    if not resolved.exists():
        raise FileNotFoundError(f"transcript file not found: {resolved}")
    size = resolved.stat().st_size
    if size > MAX_TRANSCRIPT_BYTES:
        raise ValueError(
            f"transcript {resolved} is {size} bytes, exceeds MAX_TRANSCRIPT_BYTES ({MAX_TRANSCRIPT_BYTES})"
        )
    return parse_transcript_text(resolved.read_text(encoding="utf-8", errors="replace"))


def transcript_to_text(turns: tuple[TranscriptTurn, ...]) -> str:
    return "\n\n".join(f"[{turn.role}] {turn.content}" for turn in turns)


def _try_parse_jsonl(text: str) -> tuple[TranscriptTurn, ...] | None:
    turns: list[TranscriptTurn] = []
    saw_valid_line = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return None
        turn = _turn_from_payload(payload, len(turns))
        if turn is None:
            saw_valid_line = True
            continue
        turns.append(turn)
        saw_valid_line = True

    if not saw_valid_line:
        return None
    return tuple(turns)


def _turn_from_payload(payload: object, next_index: int) -> TranscriptTurn | None:
    if not isinstance(payload, dict):
        return None

    nested = payload.get("message")
    if isinstance(nested, dict) and ("role" in nested or "content" in nested):
        return _turn_from_payload(nested, next_index)

    role = payload.get("role") or payload.get("type")
    content = _extract_content(payload.get("content"))
    if not content:
        text = payload.get("text")
        if isinstance(text, str):
            content = text.strip()
    if not role or not content:
        return None
    if role not in ("user", "assistant", "system", "tool"):
        return None

    return TranscriptTurn(index=next_index, role=str(role), content=content)


def _extract_content(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            elif isinstance(block, str) and block.strip():
                parts.append(block.strip())
        return "\n".join(parts)
    return ""
