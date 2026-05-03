"""Deterministic memory routing.

Decide whether a memory operation should target Honcho (internal) or
MemPalace (external) based on the declared *scope* and the request
sensitivity. The router does not perform I/O — it returns a
:class:`MemoryRouteDecision` value for the arbiter to act on.
"""
from __future__ import annotations

from typing import Iterable

from .schema import (
    CANONICAL_BACKEND_ROLES,
    MemoryBackendRole,
    MemoryProviderCapabilities,
    MemoryReadRequest,
    MemoryRouteDecision,
    MemoryWriteRequest,
)

# Scope vocabularies. Anything not listed here routes through "either"
# semantics — the router defaults to internal for reads and warns for
# writes so the arbiter can flag the call.
INTERNAL_SCOPES: frozenset[str] = frozenset(
    {
        "user_preferences",
        "user_preference",
        "agent_state",
        "agent_behavioral_memory",
        "session_context",
        "personal",
    }
)
EXTERNAL_SCOPES: frozenset[str] = frozenset(
    {
        "project_context",
        "architecture_decisions",
        "architecture_decision",
        "cross_session_code_context",
        "code_context",
        "workspace",
        "team_decision",
    }
)
SENSITIVE_SCOPES: frozenset[str] = frozenset(
    {
        "credentials",
        "secret",
        "secrets",
        "api_key",
        "api_keys",
        "tokens",
        "private_key",
    }
)


class MemoryRouter:
    """Map (scope, sensitivity) onto a backend, honoring capabilities."""

    def __init__(
        self,
        capabilities: dict[str, MemoryProviderCapabilities] | None = None,
        *,
        internal_backend: str = "honcho",
        external_backend: str = "mempalace",
    ) -> None:
        self.capabilities = dict(capabilities or {})
        self.internal_backend = internal_backend.lower()
        self.external_backend = external_backend.lower()

    # ------------------------------------------------------------------

    def route_read(self, request: MemoryReadRequest) -> MemoryRouteDecision:
        scope = (request.scope or "").lower()
        backend, role = self._scope_to_backend(scope)
        cap = self.capabilities.get(backend) if backend else None

        if backend and cap is not None and not cap.supports_read:
            return MemoryRouteDecision(
                action="warn",
                backend=None,
                backend_role=role,
                reasons=(f"backend {backend!r} does not support reads",),
            )

        if backend is None:
            return MemoryRouteDecision(
                action="warn",
                backend=None,
                backend_role=MemoryBackendRole.UNKNOWN,
                reasons=(f"unknown scope {scope!r}; no canonical backend",),
                fallback_backend=self.internal_backend,
            )

        return MemoryRouteDecision(
            action="allow",
            backend=backend,
            backend_role=role,
            reasons=(),
        )

    # ------------------------------------------------------------------

    def route_write(self, request: MemoryWriteRequest) -> MemoryRouteDecision:
        scope = (request.scope or "").lower()
        sensitivity = (request.sensitivity or "low").lower()

        # Hard deny for credential-like content. Memory backends should
        # never store raw credentials, even redacted.
        if sensitivity == "credential_like" or scope in SENSITIVE_SCOPES:
            return MemoryRouteDecision(
                action="deny",
                backend=None,
                backend_role=MemoryBackendRole.UNKNOWN,
                reasons=(
                    f"credential-like content (sensitivity={sensitivity!r}, "
                    f"scope={scope!r}) cannot be persisted to memory",
                ),
            )

        backend, role = self._scope_to_backend(scope)
        cap = self.capabilities.get(backend) if backend else None

        if backend and cap is not None and not cap.supports_write:
            return MemoryRouteDecision(
                action="deny",
                backend=backend,
                backend_role=role,
                reasons=(f"backend {backend!r} does not support writes",),
            )

        if backend is None:
            return MemoryRouteDecision(
                action="warn",
                backend=None,
                backend_role=MemoryBackendRole.UNKNOWN,
                reasons=(f"unknown scope {scope!r}; no canonical backend",),
                fallback_backend=self.external_backend,
            )

        action = "allow"
        if sensitivity == "high":
            action = "ask"
        return MemoryRouteDecision(
            action=action,
            backend=backend,
            backend_role=role,
            reasons=(),
        )

    # ------------------------------------------------------------------

    def _scope_to_backend(self, scope: str) -> tuple[str | None, str]:
        if scope in INTERNAL_SCOPES:
            return self.internal_backend, MemoryBackendRole.INTERNAL
        if scope in EXTERNAL_SCOPES:
            return self.external_backend, MemoryBackendRole.EXTERNAL
        return None, MemoryBackendRole.UNKNOWN

    def known_scopes(self) -> Iterable[str]:
        return tuple(sorted(set(INTERNAL_SCOPES) | set(EXTERNAL_SCOPES)))
