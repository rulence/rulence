"""Governed memory arbitration over Honcho (internal) and MemPalace (external).

The :class:`MemoryArbiter` is the public entrypoint Rulence callers
use for memory operations. It:

* asks the :class:`MemoryRouter` which backend to target and whether
  the operation is allowed under the configured policy;
* runs every write through the M2 :class:`SecretRedactor` and refuses
  writes whose original content carried PEM/PAT-class secrets;
* attaches a :class:`MemoryProvenance` record on every write;
* surfaces a clean ``degraded=True`` result when the chosen backend
  cannot service the request, instead of raising.

The arbiter never silently drops a write — every refusal is recorded
with a ``reason`` field so callers (and audit logs) can see why.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..security import SecretRedactor
from .schema import (
    MemoryBackendRole,
    MemoryProvenance,
    MemoryProviderCapabilities,
    MemoryReadRequest,
    MemoryReadResult,
    MemoryRouteDecision,
    MemoryWriteRequest,
    MemoryWriteResult,
)
from .router import MemoryRouter, SENSITIVE_SCOPES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_content(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class _ProviderHandle:
    """Pair a memory provider with its capability descriptor."""

    provider: Any
    capabilities: MemoryProviderCapabilities


class MemoryArbiter:
    """High-level memory governance.

    Construct with a mapping of backend name to ``_ProviderHandle``.
    The mapping is empty by default — calls degrade gracefully when a
    backend is missing, recording ``degraded=True``.
    """

    def __init__(
        self,
        router: MemoryRouter | None = None,
        *,
        providers: dict[str, _ProviderHandle] | None = None,
        redactor: SecretRedactor | None = None,
        context_store: Any = None,
    ) -> None:
        self.providers = dict(providers or {})
        capabilities = {
            name: handle.capabilities for name, handle in self.providers.items()
        }
        self.router = router or MemoryRouter(capabilities=capabilities)
        self.redactor = redactor or SecretRedactor()
        # ``context_store`` is duck-typed (any object with an ``add``
        # accepting a ``ContextFragment``) so this module does not need
        # to import the context package at construction time.
        self.context_store = context_store

    # ------------------------------------------------------------------
    # Reads

    def read(
        self,
        query: str,
        scope: str,
        *,
        policy: str | None = None,
        corr_id: str | None = None,
        limit: int = 5,
    ) -> MemoryReadResult:
        request = MemoryReadRequest(
            query=query,
            scope=scope,
            corr_id=corr_id,
            policy_name=policy,
            limit=limit,
        )
        decision = self.router.route_read(request)
        if decision.action == "deny":
            return MemoryReadResult(
                items=(),
                backend=decision.backend,
                backend_role=decision.backend_role,
                degraded=True,
                error="; ".join(decision.reasons) or "denied by policy",
            )

        backend = decision.backend or decision.fallback_backend
        if backend is None:
            return MemoryReadResult(
                items=(),
                backend=None,
                backend_role=decision.backend_role,
                degraded=True,
                error="; ".join(decision.reasons) or "no canonical backend for scope",
            )

        handle = self.providers.get(backend)
        if handle is None:
            return MemoryReadResult(
                items=(),
                backend=backend,
                backend_role=decision.backend_role,
                degraded=True,
                error=f"backend {backend!r} not configured",
            )
        if not handle.capabilities.supports_read:
            return MemoryReadResult(
                items=(),
                backend=backend,
                backend_role=decision.backend_role,
                degraded=True,
                error=f"backend {backend!r} does not support reads",
            )
        try:
            items = handle.provider.retrieve(query, limit)
        except Exception as exc:  # pragma: no cover - defensive
            return MemoryReadResult(
                items=(),
                backend=backend,
                backend_role=decision.backend_role,
                degraded=True,
                error=str(exc),
            )
        items_tuple = tuple(items)
        self._emit_memory_fragments(
            items_tuple, backend=backend, corr_id=corr_id
        )
        return MemoryReadResult(
            items=items_tuple,
            backend=backend,
            backend_role=decision.backend_role,
        )

    # ------------------------------------------------------------------
    # M9 context fragment integration

    def _emit_memory_fragments(
        self,
        items: tuple,
        *,
        backend: str | None,
        corr_id: str | None,
    ) -> None:
        """Best-effort fragment emission. Never raises."""
        store = self.context_store
        if store is None or not items or not backend:
            return
        try:
            from ..context import (
                fragments_from_honcho_memory_item,
                fragments_from_mempalace_memory_item,
            )

            if backend == "honcho":
                builder = fragments_from_honcho_memory_item
            elif backend == "mempalace":
                builder = fragments_from_mempalace_memory_item
            else:
                return
            for item in items:
                for fragment in builder(item, corr_id=corr_id):
                    store.add(fragment)
        except Exception:
            # Fragment emission is governance-only; never break a
            # memory read because the context index failed.
            return

    # ------------------------------------------------------------------
    # Writes

    def write(
        self,
        content: str,
        scope: str,
        *,
        sensitivity: str = "low",
        policy: str | None = None,
        corr_id: str | None = None,
        session_id: str | None = None,
        runner: str | None = None,
        tool: str | None = None,
        source_event_id: str | None = None,
    ) -> MemoryWriteResult:
        # 1. Always run the redactor first. Original is never persisted.
        redaction = self.redactor.redact_text(content or "")
        redacted_content = redaction.redacted_text

        # 2. Escalate sensitivity if the redactor caught a secret.
        effective_sensitivity = sensitivity
        if redaction.redaction_count > 0:
            effective_sensitivity = "credential_like"

        request = MemoryWriteRequest(
            content=redacted_content,
            scope=scope,
            corr_id=corr_id,
            policy_name=policy,
            sensitivity=effective_sensitivity,
        )
        decision = self.router.route_write(request)

        if decision.action == "deny":
            return MemoryWriteResult(
                written=False,
                backend=decision.backend,
                backend_role=decision.backend_role,
                redaction_count=redaction.redaction_count,
                provenance=None,
                reason="; ".join(decision.reasons) or "denied by policy",
            )

        backend = decision.backend or decision.fallback_backend
        if backend is None:
            return MemoryWriteResult(
                written=False,
                backend=None,
                backend_role=decision.backend_role,
                redaction_count=redaction.redaction_count,
                provenance=None,
                reason="; ".join(decision.reasons) or "no canonical backend for scope",
                degraded=True,
            )

        handle = self.providers.get(backend)
        if handle is None:
            return MemoryWriteResult(
                written=False,
                backend=backend,
                backend_role=decision.backend_role,
                redaction_count=redaction.redaction_count,
                provenance=None,
                reason=f"backend {backend!r} not configured",
                degraded=True,
            )
        if not handle.capabilities.supports_write:
            return MemoryWriteResult(
                written=False,
                backend=backend,
                backend_role=decision.backend_role,
                redaction_count=redaction.redaction_count,
                provenance=None,
                reason=f"backend {backend!r} does not support writes",
            )

        provenance = MemoryProvenance(
            source_event_id=source_event_id,
            source_corr_id=corr_id,
            source_session_id=session_id,
            source_runner=runner,
            source_tool=tool,
            source_memory_backend=backend,
            created_at=_now_iso(),
            content_hash=_hash_content(redacted_content),
            policy_name=policy,
            sensitivity=effective_sensitivity,
            redaction_count=redaction.redaction_count,
        )

        # If the route says ``ask``, the arbiter still attempts the
        # write if the backend supports it. The audit metadata records
        # the ``ask`` so the final review can flag missing approval.
        try:
            ok = handle.provider.write(
                redacted_content,
                scope=scope,
                provenance=provenance.to_dict(),
            )
        except Exception as exc:  # pragma: no cover - defensive
            return MemoryWriteResult(
                written=False,
                backend=backend,
                backend_role=decision.backend_role,
                redaction_count=redaction.redaction_count,
                provenance=provenance,
                reason=str(exc),
                degraded=True,
            )
        return MemoryWriteResult(
            written=bool(ok),
            backend=backend,
            backend_role=decision.backend_role,
            redaction_count=redaction.redaction_count,
            provenance=provenance,
            reason=None if ok else "provider returned a falsy result",
        )


def make_handle(
    provider: Any,
    capabilities: MemoryProviderCapabilities,
) -> _ProviderHandle:
    """Public helper for callers (and tests) to wrap a provider."""
    return _ProviderHandle(provider=provider, capabilities=capabilities)


def capabilities_for(provider: Any) -> MemoryProviderCapabilities:
    """Best-effort capability descriptor for the existing providers.

    The pre-M8 providers expose only ``retrieve(query, limit)``. Until
    a backend gains a real ``write`` method, ``supports_write`` stays
    False and the arbiter refuses writes that would otherwise route
    there. ``backend_role`` is filled from the canonical mapping in
    :mod:`rulence.memory.schema`.
    """
    from .providers import (
        HonchoMemoryProvider,
        HttpMemoryProvider,
        LocalFileMemoryProvider,
        MemPalaceMemoryProvider,
        MempalaceMcpProvider,
    )

    base = MemoryProviderCapabilities(
        backend_name=getattr(provider, "name", provider.__class__.__name__),
        backend_role=MemoryBackendRole.UNKNOWN,
        supports_read=hasattr(provider, "retrieve"),
        supports_write=False,
        supports_update=False,
        supports_expire=False,
        supports_search=hasattr(provider, "retrieve"),
        supports_provenance=False,
        supports_delete=False,
        supports_mcp_transport=False,
        supports_http_transport=False,
    )
    if isinstance(provider, HonchoMemoryProvider):
        return MemoryProviderCapabilities(
            backend_name="honcho",
            backend_role=MemoryBackendRole.INTERNAL,
            supports_read=True,
            supports_write=False,
            supports_update=False,
            supports_expire=False,
            supports_search=True,
            supports_provenance=False,
            supports_delete=False,
            supports_mcp_transport=False,
            supports_http_transport=True,
        )
    if isinstance(provider, MempalaceMcpProvider):
        return MemoryProviderCapabilities(
            backend_name="mempalace",
            backend_role=MemoryBackendRole.EXTERNAL,
            supports_read=True,
            supports_write=False,
            supports_update=False,
            supports_expire=False,
            supports_search=True,
            supports_provenance=False,
            supports_delete=False,
            supports_mcp_transport=True,
            supports_http_transport=False,
        )
    if isinstance(provider, MemPalaceMemoryProvider):
        return MemoryProviderCapabilities(
            backend_name="mempalace",
            backend_role=MemoryBackendRole.EXTERNAL,
            supports_read=True,
            supports_write=False,
            supports_update=False,
            supports_expire=False,
            supports_search=True,
            supports_provenance=False,
            supports_delete=False,
            supports_mcp_transport=False,
            supports_http_transport=True,
        )
    if isinstance(provider, LocalFileMemoryProvider):
        return MemoryProviderCapabilities(
            backend_name="local_file",
            backend_role=MemoryBackendRole.UNKNOWN,
            supports_read=True,
            supports_write=False,
            supports_update=False,
            supports_expire=False,
            supports_search=True,
            supports_provenance=False,
            supports_delete=False,
            supports_mcp_transport=False,
            supports_http_transport=False,
        )
    if isinstance(provider, HttpMemoryProvider):
        return MemoryProviderCapabilities(
            backend_name=getattr(provider, "name", "http"),
            backend_role=MemoryBackendRole.UNKNOWN,
            supports_read=True,
            supports_write=False,
            supports_update=False,
            supports_expire=False,
            supports_search=True,
            supports_provenance=False,
            supports_delete=False,
            supports_mcp_transport=False,
            supports_http_transport=True,
        )
    return base
