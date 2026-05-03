"""Context snapshot: a frozen list of fragments that informed a task.

A snapshot is the governance receipt for "what context did the agent
see?". It points at fragment ids stored in :class:`ContextStore`. The
fragments themselves are not duplicated; replay materializes them by
looking up the ids in the store.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .fragment import ContextFragment


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_content_hash(fragment_ids: Iterable[str]) -> str:
    """Stable hash of the snapshot's fragment list."""
    joined = "\n".join(sorted(fragment_ids))
    return "sha256:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ContextSnapshot:
    snapshot_id: str
    task_id: str | None
    corr_id: str | None
    created_at: str
    fragment_ids: tuple[str, ...]
    memory_backends_used: tuple[str, ...]
    policy_name: str | None
    token_estimate: int
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "task_id": self.task_id,
            "corr_id": self.corr_id,
            "created_at": self.created_at,
            "fragment_ids": list(self.fragment_ids),
            "memory_backends_used": list(self.memory_backends_used),
            "policy_name": self.policy_name,
            "token_estimate": self.token_estimate,
            "content_hash": self.content_hash,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextSnapshot":
        if "snapshot_id" not in data or "fragment_ids" not in data:
            raise ValueError(
                "ContextSnapshot.from_dict requires 'snapshot_id' and 'fragment_ids'"
            )
        fragment_ids = tuple(str(fid) for fid in data.get("fragment_ids") or ())
        memory_backends = tuple(
            str(b) for b in data.get("memory_backends_used") or ()
        )
        content_hash = str(
            data.get("content_hash") or snapshot_content_hash(fragment_ids)
        )
        return cls(
            snapshot_id=str(data["snapshot_id"]),
            task_id=_opt_str(data.get("task_id")),
            corr_id=_opt_str(data.get("corr_id")),
            created_at=str(data.get("created_at") or _now_iso()),
            fragment_ids=fragment_ids,
            memory_backends_used=memory_backends,
            policy_name=_opt_str(data.get("policy_name")),
            token_estimate=int(data.get("token_estimate", 0) or 0),
            content_hash=content_hash,
            metadata=dict(data.get("metadata") or {}),
        )

    @classmethod
    def build(
        cls,
        fragments: Iterable[ContextFragment],
        *,
        snapshot_id: str | None = None,
        task_id: str | None = None,
        corr_id: str | None = None,
        policy_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ContextSnapshot":
        fragment_list = tuple(fragments)
        fragment_ids = tuple(f.fragment_id for f in fragment_list)
        memory_backends = tuple(
            sorted({f.memory_backend for f in fragment_list if f.memory_backend})
        )
        token_total = sum(int(f.token_estimate or 0) for f in fragment_list)
        return cls(
            snapshot_id=snapshot_id or "snap_" + uuid.uuid4().hex,
            task_id=_opt_str(task_id),
            corr_id=_opt_str(corr_id),
            created_at=_now_iso(),
            fragment_ids=fragment_ids,
            memory_backends_used=memory_backends,
            policy_name=_opt_str(policy_name),
            token_estimate=token_total,
            content_hash=snapshot_content_hash(fragment_ids),
            metadata=dict(metadata or {}),
        )


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
