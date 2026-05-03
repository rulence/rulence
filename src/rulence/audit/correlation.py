"""Correlation IDs for hook events.

A Claude Code session typically emits a sequence of hook events:
``SessionStart``, ``UserPromptSubmit``, one or more ``PreToolUse`` /
``PostToolUse`` pairs, optionally a ``Stop``, and finally ``SessionEnd``.
The :class:`CorrelationIdManager` derives a stable Rulence
``session_id`` from the Claude Code session id and a fresh ``corr_id``
per logical user turn (each ``UserPromptSubmit``).

State lives in ``~/.rulence/state/<session_id>.json`` and is intentionally
small. We never persist raw prompts; only a SHA-256 of the prompt string
when one is provided. State files are written atomically via
``tempfile.mkstemp`` + :func:`os.replace`.

If a Claude Code event arrives without a ``session_id`` field — for
example during local hook tests — the manager falls back to a stable
hash of the process id and time so each event still gets a deterministic
session_id and a synthesized corr_id.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class CorrelationContext:
    rulence_session_id: str
    corr_id: str | None
    parent_event_id: str | None = None


def default_state_dir() -> Path:
    configured = os.environ.get("RULENCE_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".rulence" / "state"


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_corr_id() -> str:
    return "ct_" + uuid.uuid4().hex[:16]


def _fallback_session_seed() -> str:
    return f"unknown-{os.getpid()}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


class CorrelationIdManager:
    def __init__(self, state_dir: str | Path | None = None) -> None:
        self.state_dir = (
            Path(state_dir).expanduser() if state_dir else default_state_dir()
        )

    # ------------------------------------------------------------------
    # Public surface

    def derive_session_id(self, claude_session_id: str | None) -> str:
        seed = (claude_session_id or "").strip() or _fallback_session_seed()
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        return f"rs_{digest}"

    def session_started(self, claude_session_id: str | None) -> CorrelationContext:
        rsid = self.derive_session_id(claude_session_id)
        existing = self._read_state(rsid) or {}
        state = {
            **existing,
            "rulence_session_id": rsid,
            "claude_session_id": claude_session_id or "",
            "corr_id": existing.get("corr_id"),
            "started_at": existing.get("started_at") or _now_iso(),
        }
        self._write_state(rsid, state)
        return CorrelationContext(
            rulence_session_id=rsid,
            corr_id=state.get("corr_id"),
        )

    def user_prompt(
        self, claude_session_id: str | None, prompt_hash: str | None = None
    ) -> CorrelationContext:
        rsid = self.derive_session_id(claude_session_id)
        existing = self._read_state(rsid) or {
            "rulence_session_id": rsid,
            "claude_session_id": claude_session_id or "",
            "started_at": _now_iso(),
        }
        corr_id = _new_corr_id()
        existing["corr_id"] = corr_id
        if prompt_hash:
            existing["last_prompt_hash"] = prompt_hash
        existing["last_turn_started_at"] = _now_iso()
        self._write_state(rsid, existing)
        return CorrelationContext(rulence_session_id=rsid, corr_id=corr_id)

    def current(self, claude_session_id: str | None) -> CorrelationContext:
        rsid = self.derive_session_id(claude_session_id)
        state = self._read_state(rsid)
        if state and state.get("corr_id"):
            return CorrelationContext(
                rulence_session_id=rsid, corr_id=state.get("corr_id")
            )
        # No prompt has arrived yet (or state is missing); synthesize a
        # corr_id so audit records are still grouped, and remember it.
        new_state = state or {
            "rulence_session_id": rsid,
            "claude_session_id": claude_session_id or "",
            "started_at": _now_iso(),
        }
        corr_id = _new_corr_id()
        new_state["corr_id"] = corr_id
        new_state["fallback_corr_id"] = True
        self._write_state(rsid, new_state)
        return CorrelationContext(rulence_session_id=rsid, corr_id=corr_id)

    def session_ended(self, claude_session_id: str | None) -> CorrelationContext:
        rsid = self.derive_session_id(claude_session_id)
        state = self._read_state(rsid) or {
            "rulence_session_id": rsid,
            "claude_session_id": claude_session_id or "",
            "started_at": _now_iso(),
        }
        state["ended_at"] = _now_iso()
        self._write_state(rsid, state)
        return CorrelationContext(
            rulence_session_id=rsid, corr_id=state.get("corr_id")
        )

    # ------------------------------------------------------------------
    # State persistence

    def _path_for(self, rsid: str) -> Path:
        safe = rsid.replace("/", "_").replace("\\", "_")
        return self.state_dir / f"{safe}.json"

    def _read_state(self, rsid: str) -> dict | None:
        path = self._path_for(rsid)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_state(self, rsid: str, state: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(rsid)
        fd, tmp = tempfile.mkstemp(
            prefix=f"{path.name}.", suffix=".tmp", dir=str(self.state_dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
