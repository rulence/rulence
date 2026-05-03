"""Append-only JSONL audit log for runtime events.

This module stores runtime audit events: user prompts, hook events, tool
calls, tool results, final-response reviews, and governance decisions. It
is separate from :mod:`rulence.sessions`, which stores Rulence reasoning
and session traces.

Each event is one JSON object per line. Files live at::

    <root>/<session_id>/<corr_id>.jsonl

When a record arrives without a ``corr_id`` (e.g., a hook event before the
first ``UserPromptSubmit``), it lands in ``_no_turn.jsonl`` so it is still
recoverable.

This module never requires raw payloads to be persisted. Callers must
hash sensitive payloads before writing and set ``payload_redacted``
appropriately.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .events import RulenceAuditEvent

NO_TURN_FILE = "_no_turn"


def default_audit_dir() -> Path:
    configured = os.environ.get("RULENCE_AUDIT_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".rulence" / "audit"


def _safe_segment(value: str) -> str:
    return value.replace("/", "_").replace("\\", "_")


class AuditTraceStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root else default_audit_dir()

    def path_for(self, session_id: str, corr_id: str | None) -> Path:
        session = _safe_segment(session_id)
        turn = _safe_segment(corr_id) if corr_id else NO_TURN_FILE
        return self.root / session / f"{turn}.jsonl"

    def append(self, event: RulenceAuditEvent) -> Path:
        path = self.path_for(event.session_id, event.corr_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), sort_keys=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return path

    def read_turn(self, session_id: str, corr_id: str | None) -> list[dict[str, Any]]:
        path = self.path_for(session_id, corr_id)
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            records.append(json.loads(line))
        return records

    def read_session(self, session_id: str) -> list[dict[str, Any]]:
        session_dir = self.root / _safe_segment(session_id)
        if not session_dir.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(session_dir.glob("*.jsonl")):
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line:
                    continue
                records.append(json.loads(line))
        return records

    def list_sessions(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())
