"""Rulence runtime audit primitives.

This package owns runtime audit events emitted from agent runtime
integrations (e.g., the Claude Code hook). It is **not** the place to add
reasoning traces — those still live in :mod:`rulence.sessions`.

Public surface is intentionally small:

* :class:`RulenceAuditEvent` — a JSON-serializable audit record.
* :class:`CorrelationIdManager` — maps Claude Code session and turn
  boundaries onto Rulence ``session_id`` and ``corr_id`` values.
* :class:`AuditTraceStore` — append-only JSONL writer and reader.

The audit event schema is versioned via
:data:`rulence.audit.events.SCHEMA_VERSION`. Bump it on breaking changes.
"""
from __future__ import annotations

from .correlation import CorrelationContext, CorrelationIdManager, default_state_dir
from .events import SCHEMA_VERSION, RulenceAuditEvent
from .store import AuditTraceStore, default_audit_dir

__all__ = [
    "AuditTraceStore",
    "CorrelationContext",
    "CorrelationIdManager",
    "RulenceAuditEvent",
    "SCHEMA_VERSION",
    "default_audit_dir",
    "default_state_dir",
]
