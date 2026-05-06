"""Local file-based agent context bus.

The :class:`AgentContextBus` is a thin governance layer that lets one
agent hand a task off to another by sharing the latest
:class:`TaskState` plus a :class:`ContextSnapshot`. Storage is local
and file-based:

    ~/.rulence/tasks/<task_id>/state.json
    ~/.rulence/tasks/<task_id>/events.jsonl

This is not a distributed bus, not a server, and not a vector
database. Honcho remains the canonical internal memory backend;
MemPalace remains the canonical external memory backend. The bus
records *which agent did what to which task*, persists the task state
JSON, and references existing :class:`ContextSnapshot` ids stored in
:class:`ContextStore`.

Concurrency: the in-process lock guards JSONL appends and JSON state
writes so concurrent calls inside one process do not interleave.
Cross-process ordering is not guaranteed.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .assembler import ContextAssembler
from .conflicts import ConflictReport, detect_conflicts
from .fragment import ContextFragment
from .resume import ActiveCheckpointStore, default_active_checkpoint_dir
from .snapshot import ContextSnapshot
from .store import ContextStore
from .task_state import TaskState

DEFAULT_STATE_FILE = "state.json"
DEFAULT_EVENTS_FILE = "events.jsonl"


def default_tasks_dir() -> Path:
    configured = os.environ.get("RULENCE_TASKS_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".rulence" / "tasks"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_segment(value: str) -> str:
    return value.replace("/", "_").replace("\\", "_")


class TaskNotFoundError(LookupError):
    """Raised when a task id has no state on disk."""


class AgentContextBus:
    def __init__(
        self,
        directory: str | Path | None = None,
        *,
        store: ContextStore | None = None,
        assembler: ContextAssembler | None = None,
        checkpoint_store: ActiveCheckpointStore | None = None,
    ) -> None:
        explicit_tasks_dir = directory is not None or bool(os.environ.get("RULENCE_TASKS_DIR"))
        self.directory = (
            Path(directory).expanduser() if directory else default_tasks_dir()
        )
        self.store = store
        self.assembler = assembler or ContextAssembler(store=store)
        if checkpoint_store is not None:
            self.checkpoint_store = checkpoint_store
        elif os.environ.get("RULENCE_ACTIVE_CHECKPOINT_DIR") or os.environ.get("RULENCE_LOCAL_MEMORY_PATH"):
            self.checkpoint_store = ActiveCheckpointStore(default_active_checkpoint_dir())
        elif explicit_tasks_dir:
            self.checkpoint_store = ActiveCheckpointStore(self.directory.parent / "active-checkpoints")
        else:
            self.checkpoint_store = ActiveCheckpointStore()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Paths

    def task_dir(self, task_id: str) -> Path:
        return self.directory / _safe_segment(task_id)

    def state_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / DEFAULT_STATE_FILE

    def events_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / DEFAULT_EVENTS_FILE

    # ------------------------------------------------------------------
    # State CRUD

    def save_state(self, state: TaskState) -> Path:
        path = self.state_path(state.task_id)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(state.to_dict(), sort_keys=True, indent=2)
            path.write_text(payload, encoding="utf-8")
        self.publish(
            state.task_id,
            event_type="task_state_saved",
            data={
                "content_hash": state.content_hash,
                "current_snapshot_id": state.current_snapshot_id,
                "status": state.status,
            },
        )
        return path

    def load_state(self, task_id: str) -> TaskState:
        path = self.state_path(task_id)
        if not path.exists():
            raise TaskNotFoundError(f"no task state on disk for {task_id!r}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return TaskState.from_dict(data)

    def exists(self, task_id: str) -> bool:
        return self.state_path(task_id).exists()

    def list_tasks(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(
            entry.name
            for entry in self.directory.iterdir()
            if entry.is_dir() and (entry / DEFAULT_STATE_FILE).exists()
        )

    def update_state(
        self,
        task_id: str,
        patch: dict[str, Any],
        *,
        agent: str | None = None,
    ) -> TaskState:
        state = self.load_state(task_id)
        new_state = state.replace(**patch)
        self.save_state(new_state)
        self.publish(
            task_id,
            event_type="task_state_updated",
            agent=agent,
            data={"keys": sorted(patch.keys())},
        )
        return new_state

    # ------------------------------------------------------------------
    # Events

    def publish(
        self,
        task_id: str,
        *,
        event_type: str,
        agent: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "event_id": "evt_" + uuid.uuid4().hex,
            "task_id": task_id,
            "event_type": event_type,
            "agent": agent,
            "timestamp": _now_iso(),
            "data": dict(data or {}),
        }
        path = self.events_path(task_id)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        self._write_active_checkpoint(task_id, event_type=event_type, event=record)
        return record

    def _write_active_checkpoint(
        self,
        task_id: str,
        *,
        event_type: str,
        event: dict[str, Any],
    ) -> None:
        """Best-effort active checkpoint update for cold-start resume.

        Checkpoints are a fast local resume anchor; failures must not
        break the governance event stream. Honcho/MemPalace remain the
        canonical memory backends.
        """
        try:
            state = self.load_state(task_id)
        except (TaskNotFoundError, OSError, json.JSONDecodeError, ValueError):
            return
        try:
            self.checkpoint_store.write_for_task(
                state,
                event_type=event_type,
                event=event,
            )
        except OSError:
            return

    def read_events(self, task_id: str) -> list[dict[str, Any]]:
        path = self.events_path(task_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
        return events

    # ------------------------------------------------------------------
    # Snapshots and handoff

    def create_snapshot(
        self,
        task_id: str,
        *,
        agent: str | None = None,
        max_tokens: int = 4000,
        compress: bool = True,
        extra_fragments: Iterable[ContextFragment] = (),
    ) -> ContextSnapshot:
        state = self.load_state(task_id)
        result = self.assembler.assemble(
            state.user_goal,
            corr_id=task_id,
            max_tokens=max_tokens,
            policy_tier=int(state.metadata.get("policy_tier", 1) or 1),
            current_constraints=state.active_constraints,
            extra_fragments=tuple(extra_fragments),
            compress=compress,
            task_id=task_id,
        )
        snapshot = result.snapshot
        memory_backends = tuple(
            sorted(
                set(state.memory_backends_used)
                | set(snapshot.memory_backends_used)
            )
        )
        new_state = state.replace(
            current_snapshot_id=snapshot.snapshot_id,
            memory_backends_used=memory_backends,
        )
        self.save_state(new_state)
        self.publish(
            task_id,
            event_type="snapshot_created",
            agent=agent,
            data={
                "snapshot_id": snapshot.snapshot_id,
                "fragment_count": len(snapshot.fragment_ids),
                "memory_backends_used": list(snapshot.memory_backends_used),
                "compressed": result.compressed is not None,
                "validation_status": (
                    result.compressed.validation_status if result.compressed else None
                ),
            },
        )
        return snapshot

    def handoff(
        self,
        *,
        from_agent: str,
        to_agent: str,
        task_id: str,
        snapshot_id: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        if not self.exists(task_id):
            raise TaskNotFoundError(f"no task state on disk for {task_id!r}")
        state = self.load_state(task_id)
        chosen_snapshot = snapshot_id or state.current_snapshot_id
        if chosen_snapshot is None:
            raise ValueError(
                "no snapshot available; call create_snapshot first or pass snapshot_id"
            )
        record = self.publish(
            task_id,
            event_type="handoff",
            agent=from_agent,
            data={
                "from_agent": from_agent,
                "to_agent": to_agent,
                "snapshot_id": chosen_snapshot,
                "task_content_hash": state.content_hash,
                "note": note,
            },
        )
        return {
            "task_id": task_id,
            "snapshot_id": chosen_snapshot,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "event_id": record["event_id"],
            "timestamp": record["timestamp"],
        }

    # ------------------------------------------------------------------
    # Conflict detection

    def detect_conflicts(
        self,
        task_id: str,
        *,
        fragments: Iterable[ContextFragment] = (),
        final_response: str | None = None,
    ) -> ConflictReport:
        state = self.load_state(task_id)
        # If no fragments are passed and the bus was wired with a store,
        # pull fragments tagged with the task id (treated as corr_id).
        candidate_fragments = list(fragments)
        if not candidate_fragments and self.store is not None:
            candidate_fragments = list(self.store.by_corr_id(task_id))
        report = detect_conflicts(
            task_state=state,
            fragments=candidate_fragments,
            final_response=final_response,
        )
        if report.has_conflicts:
            self.publish(
                task_id,
                event_type="conflict_detected",
                data={
                    "conflict_count": len(report.conflicts),
                    "kinds": sorted({c.kind for c in report.conflicts}),
                },
            )
        return report
