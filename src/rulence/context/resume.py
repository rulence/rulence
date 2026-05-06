"""Active checkpoint writer and startup resume prompt helpers.

This module is intentionally file-backed and deterministic. Honcho and
MemPalace remain the canonical memory backends; active checkpoint files
are the fast cold-start anchor Hermes/Rulence can read before spending
minutes reconstructing the previous session.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .task_state import TaskState

LATEST_FILE = "_latest.json"
ACTIVE_STATUSES = {"open", "active", "active_needs_followup", "blocked"}
PAUSED_STATUSES = {"paused", "pause"}
DONE_STATUSES = {"done", "complete", "completed", "closed"}


def default_active_checkpoint_dir() -> Path:
    configured = (
        os.environ.get("RULENCE_ACTIVE_CHECKPOINT_DIR")
        or os.environ.get("RULENCE_LOCAL_MEMORY_PATH")
    )
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".hermes" / "active-checkpoints"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_segment(value: str) -> str:
    safe = value.replace("/", "_").replace("\\", "_").strip()
    return safe or "task"


def _normalize_status(status: str | None) -> str:
    raw = (status or "open").strip().lower()
    if raw in DONE_STATUSES:
        return "done"
    if raw in PAUSED_STATUSES:
        return "paused"
    if raw == "blocked":
        return "blocked"
    return "active"


def _unresolved_blockers(state: TaskState) -> list[str]:
    return [blocker.text for blocker in state.blockers if not blocker.resolved]


def _latest_decision(state: TaskState) -> str | None:
    if not state.decisions:
        return None
    return state.decisions[-1].text


def _next_recommended_step(state: TaskState, event_type: str | None) -> str:
    meta = dict(state.metadata or {})
    explicit = meta.get("next_recommended_step") or meta.get("next_step")
    if explicit:
        return str(explicit)
    if state.open_questions:
        return "Resolve open question: " + state.open_questions[0]
    blockers = _unresolved_blockers(state)
    if blockers:
        return "Resolve blocker: " + blockers[0]
    if event_type == "snapshot_created":
        return "Use the current context snapshot to continue governed work."
    if event_type == "handoff":
        return "Continue from the recorded agent handoff."
    return "Continue governed work from the latest checkpoint."


def _artifacts(state: TaskState) -> list[str]:
    meta = dict(state.metadata or {})
    values: list[str] = []
    for key in ("artifacts", "changed_paths", "reports"):
        raw = meta.get(key)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, Iterable):
            values.extend(str(item) for item in raw)
    return values


class ActiveCheckpointStore:
    """Read/write active task checkpoint JSON files."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory).expanduser() if directory else default_active_checkpoint_dir()
        self._lock = threading.Lock()

    def path_for(self, task_id: str) -> Path:
        return self.directory / f"{_safe_segment(task_id)}.json"

    @property
    def latest_path(self) -> Path:
        return self.directory / LATEST_FILE

    def write_for_task(
        self,
        state: TaskState,
        *,
        event_type: str | None = None,
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = dict(event or {})
        status = _normalize_status(state.status)
        updated_at = str(event.get("timestamp") or state.updated_at or _now_iso())
        last_step = str(event_type or event.get("event_type") or "task_state_saved")
        checkpoint = {
            "id": state.task_id,
            "task_id": state.task_id,
            "project": str(state.metadata.get("project") or state.metadata.get("project_name") or state.task_id),
            "status": status,
            "source_status": state.status,
            "last_user_intent": state.user_goal,
            "last_completed_step": last_step,
            "latest_decision": _latest_decision(state),
            "next_recommended_step": _next_recommended_step(state, last_step),
            "artifacts": _artifacts(state),
            "blockers": _unresolved_blockers(state),
            "open_questions": list(state.open_questions),
            "current_snapshot_id": state.current_snapshot_id,
            "memory_backends_used": list(state.memory_backends_used),
            "metadata": dict(state.metadata or {}),
            "updated_at": updated_at,
        }
        checkpoint["resume_question"] = self._build_resume_question(checkpoint)
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(checkpoint, indent=2, sort_keys=True)
            self.path_for(state.task_id).write_text(payload + "\n", encoding="utf-8")
            self.latest_path.write_text(payload + "\n", encoding="utf-8")
        return checkpoint

    def load(self, task_id: str) -> dict[str, Any]:
        path = self.path_for(task_id)
        if not path.exists():
            raise FileNotFoundError(f"no active checkpoint for {task_id!r}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        checkpoints: list[dict[str, Any]] = []
        for path in self.directory.glob("*.json"):
            if path.name == LATEST_FILE:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                checkpoints.append(data)
        checkpoints.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return checkpoints

    def active(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self.list()
            if str(item.get("status") or "").lower() in ACTIVE_STATUSES
        ]

    def newest_active(self) -> dict[str, Any] | None:
        active = self.active()
        return active[0] if active else None

    def resume_summary(self) -> dict[str, Any]:
        checkpoint = self.newest_active()
        if not checkpoint:
            return {
                "has_active_checkpoint": False,
                "checkpoint": None,
                "prompt": "No active Rulence checkpoint found.",
                "checkpoint_dir": str(self.directory),
            }
        return {
            "has_active_checkpoint": True,
            "checkpoint": checkpoint,
            "prompt": self._build_resume_prompt(checkpoint),
            "checkpoint_dir": str(self.directory),
        }

    def _build_resume_question(self, checkpoint: dict[str, Any]) -> str:
        project = checkpoint.get("project") or checkpoint.get("task_id")
        next_step = checkpoint.get("next_recommended_step") or "continue the work"
        return f"Serge, we were working on {project}. Next step: {next_step}. Continue, pause, or mark done?"

    def _build_resume_prompt(self, checkpoint: dict[str, Any]) -> str:
        if checkpoint.get("resume_prompt"):
            return str(checkpoint["resume_prompt"])
        intent = checkpoint.get("last_user_intent") or checkpoint.get("project") or checkpoint.get("task_id")
        last_step = checkpoint.get("last_completed_step") or "checkpoint saved"
        next_step = checkpoint.get("next_recommended_step") or "continue governed work"
        blockers = checkpoint.get("blockers") or []
        blocker_text = "; ".join(str(item) for item in blockers) if blockers else "none recorded"
        return (
            f"Hey Serge — last active work was: {intent}. "
            f"Last step: {last_step}. "
            f"Next: {next_step}. "
            f"Blockers: {blocker_text}. "
            "Continue, pause, or mark done?"
        )
