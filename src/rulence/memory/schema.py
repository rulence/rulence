"""Memory governance schema (M8).

The schema models routed memory operations against the two canonical
memory backends:

* **Honcho** — internal memory (user preferences, agent state).
* **MemPalace** — external memory (project context, durable code/
  architecture facts).

Rulence does not replace these systems. The dataclasses here describe
the *shape* of governed reads, writes, updates, and expirations; the
:class:`rulence.memory.MemoryRouter` decides which backend handles a
given request, and the :class:`rulence.memory.MemoryArbiter` redacts,
attaches provenance, and audits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class MemoryBackendRole:
    INTERNAL = "internal"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


# Canonical mapping between backend names and roles. Lower-case names.
CANONICAL_BACKEND_ROLES: dict[str, str] = {
    "honcho": MemoryBackendRole.INTERNAL,
    "mempalace": MemoryBackendRole.EXTERNAL,
}


@dataclass(frozen=True)
class MemoryProviderCapabilities:
    backend_name: str
    backend_role: str
    supports_read: bool
    supports_write: bool
    supports_update: bool
    supports_expire: bool
    supports_search: bool
    supports_provenance: bool
    supports_delete: bool
    supports_mcp_transport: bool
    supports_http_transport: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_name": self.backend_name,
            "backend_role": self.backend_role,
            "supports_read": self.supports_read,
            "supports_write": self.supports_write,
            "supports_update": self.supports_update,
            "supports_expire": self.supports_expire,
            "supports_search": self.supports_search,
            "supports_provenance": self.supports_provenance,
            "supports_delete": self.supports_delete,
            "supports_mcp_transport": self.supports_mcp_transport,
            "supports_http_transport": self.supports_http_transport,
        }


@dataclass(frozen=True)
class MemoryProvenance:
    source_event_id: str | None
    source_corr_id: str | None
    source_session_id: str | None
    source_runner: str | None
    source_tool: str | None
    source_memory_backend: str | None
    created_at: str
    content_hash: str
    policy_name: str | None
    sensitivity: str
    redaction_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_event_id": self.source_event_id,
            "source_corr_id": self.source_corr_id,
            "source_session_id": self.source_session_id,
            "source_runner": self.source_runner,
            "source_tool": self.source_tool,
            "source_memory_backend": self.source_memory_backend,
            "created_at": self.created_at,
            "content_hash": self.content_hash,
            "policy_name": self.policy_name,
            "sensitivity": self.sensitivity,
            "redaction_count": self.redaction_count,
        }


@dataclass(frozen=True)
class MemoryReadRequest:
    query: str
    scope: str
    corr_id: str | None = None
    policy_name: str | None = None
    limit: int = 5


@dataclass(frozen=True)
class MemoryReadResult:
    items: tuple[Any, ...]
    backend: str | None
    backend_role: str
    degraded: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        item_dicts = []
        for item in self.items:
            if hasattr(item, "to_dict"):
                item_dicts.append(item.to_dict())
            else:
                item_dicts.append(item)
        return {
            "items": item_dicts,
            "backend": self.backend,
            "backend_role": self.backend_role,
            "degraded": self.degraded,
            "error": self.error,
        }


@dataclass(frozen=True)
class MemoryWriteRequest:
    content: str
    scope: str
    corr_id: str | None = None
    policy_name: str | None = None
    sensitivity: str = "low"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryWriteResult:
    written: bool
    backend: str | None
    backend_role: str
    redaction_count: int
    provenance: MemoryProvenance | None
    reason: str | None = None
    degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "written": self.written,
            "backend": self.backend,
            "backend_role": self.backend_role,
            "redaction_count": self.redaction_count,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "reason": self.reason,
            "degraded": self.degraded,
        }


@dataclass(frozen=True)
class MemoryUpdateRequest:
    memory_id: str
    patch: dict[str, Any]
    corr_id: str | None = None
    policy_name: str | None = None


@dataclass(frozen=True)
class MemoryExpireRequest:
    memory_id: str
    reason: str
    corr_id: str | None = None
    policy_name: str | None = None


@dataclass(frozen=True)
class MemoryRouteDecision:
    action: str  # "allow" | "warn" | "ask" | "deny"
    backend: str | None
    backend_role: str
    reasons: tuple[str, ...] = ()
    fallback_backend: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "backend": self.backend,
            "backend_role": self.backend_role,
            "reasons": list(self.reasons),
            "fallback_backend": self.fallback_backend,
        }
