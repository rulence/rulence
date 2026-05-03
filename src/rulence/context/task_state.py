"""Local task state shared across supported agents.

A :class:`TaskState` is the live, mutable handoff record for one task.
It collects the user goal, the set of active constraints, accumulated
decisions, open questions, blockers, and a pointer at the latest
:class:`ContextSnapshot` that materialized the task's governed context.

This is *not* a replacement for Honcho or MemPalace. Those remain the
canonical memory backends. ``TaskState`` is a governance receipt that
agents read and update so a downstream agent can pick up where the
previous one left off without reconstructing context blindly.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from ..logic import Constraint

TASK_STATE_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_task_id() -> str:
    return "task_" + uuid.uuid4().hex


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True)
class Decision:
    text: str
    decided_by: str | None = None
    decided_at: str | None = None
    source_fragment_id: str | None = None
    rationale: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "source_fragment_id": self.source_fragment_id,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Decision":
        return cls(
            text=str(data.get("text", "")),
            decided_by=_opt_str(data.get("decided_by")),
            decided_at=_opt_str(data.get("decided_at")),
            source_fragment_id=_opt_str(data.get("source_fragment_id")),
            rationale=_opt_str(data.get("rationale")),
        )


@dataclass(frozen=True)
class Blocker:
    text: str
    raised_by: str | None = None
    raised_at: str | None = None
    resolved: bool = False
    resolution: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "raised_by": self.raised_by,
            "raised_at": self.raised_at,
            "resolved": self.resolved,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Blocker":
        return cls(
            text=str(data.get("text", "")),
            raised_by=_opt_str(data.get("raised_by")),
            raised_at=_opt_str(data.get("raised_at")),
            resolved=bool(data.get("resolved", False)),
            resolution=_opt_str(data.get("resolution")),
        )


@dataclass(frozen=True)
class TaskState:
    task_id: str
    user_goal: str
    active_constraints: tuple[Constraint, ...] = ()
    decisions: tuple[Decision, ...] = ()
    open_questions: tuple[str, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    current_snapshot_id: str | None = None
    memory_backends_used: tuple[str, ...] = ()
    version: int = TASK_STATE_VERSION
    updated_at: str = ""
    content_hash: str = ""
    status: str = "open"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "user_goal": self.user_goal,
            "active_constraints": [c.to_dict() for c in self.active_constraints],
            "decisions": [d.to_dict() for d in self.decisions],
            "open_questions": list(self.open_questions),
            "blockers": [b.to_dict() for b in self.blockers],
            "current_snapshot_id": self.current_snapshot_id,
            "memory_backends_used": list(self.memory_backends_used),
            "version": self.version,
            "updated_at": self.updated_at,
            "content_hash": self.content_hash,
            "status": self.status,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskState":
        if "task_id" not in data:
            raise ValueError("TaskState.from_dict requires 'task_id'")
        constraints = tuple(
            Constraint.from_dict(item)
            for item in data.get("active_constraints") or ()
        )
        decisions = tuple(
            Decision.from_dict(item) for item in data.get("decisions") or ()
        )
        blockers = tuple(
            Blocker.from_dict(item) for item in data.get("blockers") or ()
        )
        memory_backends = tuple(
            str(b) for b in data.get("memory_backends_used") or ()
        )
        open_questions = tuple(
            str(q) for q in data.get("open_questions") or ()
        )
        return cls(
            task_id=str(data["task_id"]),
            user_goal=str(data.get("user_goal", "")),
            active_constraints=constraints,
            decisions=decisions,
            open_questions=open_questions,
            blockers=blockers,
            current_snapshot_id=_opt_str(data.get("current_snapshot_id")),
            memory_backends_used=memory_backends,
            version=int(data.get("version", TASK_STATE_VERSION) or TASK_STATE_VERSION),
            updated_at=str(data.get("updated_at") or _now_iso()),
            content_hash=str(
                data.get("content_hash") or compute_task_state_hash_dict(data)
            ),
            status=str(data.get("status", "open") or "open"),
            metadata=dict(data.get("metadata") or {}),
        )

    @classmethod
    def new(
        cls,
        *,
        user_goal: str,
        task_id: str | None = None,
        active_constraints: Iterable[Constraint] = (),
        decisions: Iterable[Decision] = (),
        open_questions: Iterable[str] = (),
        blockers: Iterable[Blocker] = (),
        current_snapshot_id: str | None = None,
        memory_backends_used: Iterable[str] = (),
        status: str = "open",
        metadata: dict[str, Any] | None = None,
    ) -> "TaskState":
        constraints = tuple(active_constraints)
        decision_list = tuple(decisions)
        blocker_list = tuple(blockers)
        question_list = tuple(open_questions)
        backends = tuple(memory_backends_used)
        meta = dict(metadata or {})
        partial = {
            "task_id": task_id or new_task_id(),
            "user_goal": user_goal,
            "active_constraints": [c.to_dict() for c in constraints],
            "decisions": [d.to_dict() for d in decision_list],
            "open_questions": list(question_list),
            "blockers": [b.to_dict() for b in blocker_list],
            "current_snapshot_id": current_snapshot_id,
            "memory_backends_used": list(backends),
            "version": TASK_STATE_VERSION,
            "status": status,
            "metadata": meta,
        }
        return cls(
            task_id=partial["task_id"],
            user_goal=user_goal,
            active_constraints=constraints,
            decisions=decision_list,
            open_questions=question_list,
            blockers=blocker_list,
            current_snapshot_id=current_snapshot_id,
            memory_backends_used=backends,
            version=TASK_STATE_VERSION,
            updated_at=_now_iso(),
            content_hash=compute_task_state_hash_dict(partial),
            status=status,
            metadata=meta,
        )

    def replace(self, **changes: Any) -> "TaskState":
        """Return a new TaskState with the given fields replaced.

        ``updated_at`` and ``content_hash`` are recomputed from the
        merged payload so callers cannot accidentally save a stale hash.
        """
        merged = self.to_dict()
        merged.update(changes)
        # Re-coerce typed fields by going through ``from_dict``.
        if "active_constraints" in changes:
            merged["active_constraints"] = [
                c.to_dict() if hasattr(c, "to_dict") else dict(c)
                for c in changes["active_constraints"]
            ]
        if "decisions" in changes:
            merged["decisions"] = [
                d.to_dict() if hasattr(d, "to_dict") else dict(d)
                for d in changes["decisions"]
            ]
        if "blockers" in changes:
            merged["blockers"] = [
                b.to_dict() if hasattr(b, "to_dict") else dict(b)
                for b in changes["blockers"]
            ]
        if "open_questions" in changes:
            merged["open_questions"] = list(changes["open_questions"])
        if "memory_backends_used" in changes:
            merged["memory_backends_used"] = list(changes["memory_backends_used"])
        merged["updated_at"] = _now_iso()
        merged["content_hash"] = compute_task_state_hash_dict(merged)
        return TaskState.from_dict(merged)


def compute_task_state_hash_dict(data: dict[str, Any]) -> str:
    """Hash a task state dict ignoring volatile fields."""
    payload = {
        "task_id": data.get("task_id"),
        "user_goal": data.get("user_goal"),
        "active_constraints": data.get("active_constraints") or [],
        "decisions": data.get("decisions") or [],
        "open_questions": data.get("open_questions") or [],
        "blockers": data.get("blockers") or [],
        "current_snapshot_id": data.get("current_snapshot_id"),
        "memory_backends_used": sorted(data.get("memory_backends_used") or []),
        "status": data.get("status"),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
