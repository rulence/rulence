"""Context fragment and source reference dataclasses.

A :class:`ContextFragment` is a single, JSON-serializable piece of
context: the text of a user instruction, a tool input, a tool result, a
memory item, a parsed constraint, etc. Every fragment carries a
:class:`SourceRef` describing where it came from so a downstream
governance audit can replay the chain back to a runtime audit event,
session, or memory backend.

Fragments are immutable. ``redacted_text`` is what may be displayed or
indexed; ``text`` mirrors it because the audit/redaction pipeline runs
upstream and we never persist a separate raw copy.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Known fragment kinds. The store accepts any string but downstream
# tooling (snapshots, dashboards) expects these labels.
KNOWN_FRAGMENT_KINDS: frozenset[str] = frozenset(
    {
        "user_instruction",
        "system_policy",
        "tool_input",
        "tool_result",
        "honcho_memory",
        "mempalace_memory",
        "code_fact",
        "decision",
        "constraint",
        "assumption",
        "open_question",
        "summary",
        "final_response",
        "audit_observation",
    }
)


def hash_text(text: str) -> str:
    """Stable sha256 hash of ``text`` for fragment content addressing."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_fragment_id() -> str:
    return "frag_" + uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SourceRef:
    """Where a fragment came from.

    All fields are optional except ``source_type`` because different
    sources populate different coordinates: an audit-derived fragment
    has ``event_id``/``corr_id``/``session_id`` but no ``url``; a
    MemPalace fragment has ``memory_backend`` and ``url``; a tool input
    has ``tool_name`` and ``runner``.
    """

    source_type: str
    source_id: str | None = None
    runner: str | None = None
    path: str | None = None
    url: str | None = None
    tool_name: str | None = None
    event_id: str | None = None
    corr_id: str | None = None
    session_id: str | None = None
    memory_backend: str | None = None
    version: str | None = None
    content_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "runner": self.runner,
            "path": self.path,
            "url": self.url,
            "tool_name": self.tool_name,
            "event_id": self.event_id,
            "corr_id": self.corr_id,
            "session_id": self.session_id,
            "memory_backend": self.memory_backend,
            "version": self.version,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRef":
        if "source_type" not in data:
            raise ValueError("SourceRef.from_dict requires 'source_type'")
        return cls(
            source_type=str(data["source_type"]),
            source_id=_opt_str(data.get("source_id")),
            runner=_opt_str(data.get("runner")),
            path=_opt_str(data.get("path")),
            url=_opt_str(data.get("url")),
            tool_name=_opt_str(data.get("tool_name")),
            event_id=_opt_str(data.get("event_id")),
            corr_id=_opt_str(data.get("corr_id")),
            session_id=_opt_str(data.get("session_id")),
            memory_backend=_opt_str(data.get("memory_backend")),
            version=_opt_str(data.get("version")),
            content_hash=_opt_str(data.get("content_hash")),
        )


@dataclass(frozen=True)
class ContextFragment:
    fragment_id: str
    kind: str
    text: str
    redacted_text: str
    source: SourceRef
    memory_backend: str | None
    created_at: str
    observed_at: str | None
    expires_at: str | None
    confidence: float
    sensitivity: str
    provenance: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    token_estimate: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fragment_id": self.fragment_id,
            "kind": self.kind,
            "text": self.text,
            "redacted_text": self.redacted_text,
            "source": self.source.to_dict(),
            "memory_backend": self.memory_backend,
            "created_at": self.created_at,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "confidence": self.confidence,
            "sensitivity": self.sensitivity,
            "provenance": dict(self.provenance),
            "content_hash": self.content_hash,
            "metadata": dict(self.metadata),
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextFragment":
        for required in ("fragment_id", "kind", "text", "source"):
            if required not in data:
                raise ValueError(
                    f"ContextFragment.from_dict requires {required!r}"
                )
        source_data = data["source"]
        if not isinstance(source_data, dict):
            raise ValueError("ContextFragment 'source' must be a mapping")
        text = str(data["text"])
        redacted_text = str(data.get("redacted_text", text))
        content_hash = str(data.get("content_hash") or hash_text(redacted_text))
        return cls(
            fragment_id=str(data["fragment_id"]),
            kind=str(data["kind"]),
            text=text,
            redacted_text=redacted_text,
            source=SourceRef.from_dict(source_data),
            memory_backend=_opt_str(data.get("memory_backend")),
            created_at=str(data.get("created_at") or _now_iso()),
            observed_at=_opt_str(data.get("observed_at")),
            expires_at=_opt_str(data.get("expires_at")),
            confidence=float(data.get("confidence", 1.0)),
            sensitivity=str(data.get("sensitivity", "low")),
            provenance=dict(data.get("provenance") or {}),
            content_hash=content_hash,
            metadata=dict(data.get("metadata") or {}),
            token_estimate=int(data.get("token_estimate", 0) or 0),
        )

    @classmethod
    def build(
        cls,
        *,
        kind: str,
        text: str,
        source: SourceRef,
        redacted_text: str | None = None,
        fragment_id: str | None = None,
        memory_backend: str | None = None,
        created_at: str | None = None,
        observed_at: str | None = None,
        expires_at: str | None = None,
        confidence: float = 1.0,
        sensitivity: str = "low",
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        token_estimate: int | None = None,
    ) -> "ContextFragment":
        """Construct a fragment, filling in derived fields.

        ``content_hash`` is derived from ``redacted_text`` (or ``text``
        if ``redacted_text`` is not supplied) so it is stable across
        equivalent inputs and never depends on the random
        ``fragment_id``.
        """
        if not isinstance(kind, str) or not kind:
            raise ValueError("ContextFragment.kind must be a non-empty string")
        if not isinstance(text, str):
            raise ValueError("ContextFragment.text must be a string")
        redacted = redacted_text if redacted_text is not None else text
        token_count = (
            int(token_estimate)
            if token_estimate is not None
            else _estimate_tokens_safe(redacted)
        )
        return cls(
            fragment_id=fragment_id or new_fragment_id(),
            kind=kind,
            text=text,
            redacted_text=redacted,
            source=source,
            memory_backend=_opt_str(memory_backend),
            created_at=created_at or _now_iso(),
            observed_at=_opt_str(observed_at),
            expires_at=_opt_str(expires_at),
            confidence=float(confidence),
            sensitivity=sensitivity,
            provenance=dict(provenance or {}),
            content_hash=hash_text(redacted),
            metadata=dict(metadata or {}),
            token_estimate=token_count,
        )


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _estimate_tokens_safe(text: str) -> int:
    """Estimate tokens without forcing a heavy import path on cold start."""
    try:
        from ..token_budget import estimate_tokens

        count, _ = estimate_tokens(text or "")
        return int(count)
    except Exception:
        # Cheap fallback: ~4 characters per token.
        return max(0, len(text or "") // 4)
