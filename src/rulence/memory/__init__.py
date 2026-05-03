"""Rulence memory governance.

Public surface:

* The original M0/M3 memory providers and helpers are re-exported from
  ``rulence.memory.providers`` so existing callers continue to work
  unchanged (``from rulence.memory import retrieve_memory`` etc.).
* New M8 schema, router, and arbiter live in ``schema.py``, ``router.py``,
  and ``arbiter.py``.

Honcho is the canonical *internal* memory backend; MemPalace is the
canonical *external* memory backend. Rulence routes, redacts, attaches
provenance, and audits memory operations — it does not replace either
backend.
"""
from __future__ import annotations

from ..config import (  # re-export so existing tests can patch rulence.memory.load_memory_config
    HonchoConfig,
    MempalaceConfig,
    MemoryConfig,
    load_memory_config,
)
from .arbiter import MemoryArbiter, capabilities_for, make_handle
from .providers import (
    HonchoError,
    HonchoMemoryProvider,
    HttpMemoryProvider,
    LocalFileMemoryProvider,
    MemPalaceMemoryProvider,
    MempalaceMcpProvider,
    MemoryItem,
    MemoryProvider,
    MemoryProviderError,
    _dedup_by_text,  # noqa: F401  (legacy test import)
    memory_health,
    memory_items_to_context,
    mempalace_rooms,
    mempalace_wings,
    retrieve_combined,
    retrieve_memory,
)
from .router import (
    EXTERNAL_SCOPES,
    INTERNAL_SCOPES,
    SENSITIVE_SCOPES,
    MemoryRouter,
)
from .schema import (
    CANONICAL_BACKEND_ROLES,
    MemoryBackendRole,
    MemoryExpireRequest,
    MemoryProvenance,
    MemoryProviderCapabilities,
    MemoryReadRequest,
    MemoryReadResult,
    MemoryRouteDecision,
    MemoryUpdateRequest,
    MemoryWriteRequest,
    MemoryWriteResult,
)

__all__ = [
    "CANONICAL_BACKEND_ROLES",
    "EXTERNAL_SCOPES",
    "HonchoError",
    "HonchoMemoryProvider",
    "HttpMemoryProvider",
    "INTERNAL_SCOPES",
    "LocalFileMemoryProvider",
    "MemPalaceMemoryProvider",
    "MempalaceMcpProvider",
    "MemoryArbiter",
    "MemoryBackendRole",
    "MemoryExpireRequest",
    "MemoryItem",
    "MemoryProvenance",
    "MemoryProvider",
    "MemoryProviderCapabilities",
    "MemoryProviderError",
    "MemoryReadRequest",
    "MemoryReadResult",
    "MemoryRouteDecision",
    "MemoryRouter",
    "MemoryUpdateRequest",
    "MemoryWriteRequest",
    "MemoryWriteResult",
    "SENSITIVE_SCOPES",
    "capabilities_for",
    "load_memory_config",
    "make_handle",
    "memory_health",
    "memory_items_to_context",
    "mempalace_rooms",
    "mempalace_wings",
    "retrieve_combined",
    "retrieve_memory",
]
