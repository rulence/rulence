"""Rulence reasoning session persistence.

This module stores Rulence reasoning traces and session state. It is not
the runtime audit log. Runtime event audit records live in
:mod:`rulence.audit.store`.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import ReasoningSession


def default_session_dir() -> Path:
    configured = os.environ.get("RULENCE_SESSION_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".rulence" / "sessions"


class SessionStore:
    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory).expanduser() if directory else default_session_dir()

    def path_for(self, session_id: str) -> Path:
        safe_id = session_id.replace("/", "_").replace("\\", "_")
        return self.directory / f"{safe_id}.json"

    def load(self, session_id: str) -> ReasoningSession:
        path = self.path_for(session_id)
        return ReasoningSession.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, session: ReasoningSession) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(session.session_id)
        payload = json.dumps(session.to_dict(), indent=2, sort_keys=True).encode("utf-8")
        fd, tmp_path = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=str(self.directory))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise
        return path

    def exists(self, session_id: str) -> bool:
        return self.path_for(session_id).exists()

    def list_ids(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(path.stem for path in self.directory.glob("*.json"))
