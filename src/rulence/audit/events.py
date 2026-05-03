"""Audit event dataclass for Rulence runtime traces.

A :class:`RulenceAuditEvent` is a single, JSON-serializable record describing
something that happened at runtime: a hook fired, a tool was preflighted, a
session ended, etc. The shape is fixed so downstream consumers (verifiers,
log shippers, dashboards) can rely on it.

This milestone (M1) ships a placeholder ``redaction_count`` of 0 and never
stores raw payloads — only a SHA-256 hash. Real redaction lands in a
later milestone.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RulenceAuditEvent:
    schema_version: int
    event_id: str
    parent_event_id: str | None
    timestamp: str
    event_type: str
    session_id: str
    corr_id: str | None
    runner: str
    decision: str | None
    evidence: tuple[str, ...]
    policy_name: str | None
    tool_name: str | None
    redaction_count: int
    token_estimate: int | None
    payload_redacted: bool
    raw_payload_hash: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "parent_event_id": self.parent_event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "session_id": self.session_id,
            "corr_id": self.corr_id,
            "runner": self.runner,
            "decision": self.decision,
            "evidence": list(self.evidence),
            "policy_name": self.policy_name,
            "tool_name": self.tool_name,
            "redaction_count": self.redaction_count,
            "token_estimate": self.token_estimate,
            "payload_redacted": self.payload_redacted,
            "raw_payload_hash": self.raw_payload_hash,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def now(
        cls,
        *,
        event_type: str,
        session_id: str,
        runner: str,
        corr_id: str | None = None,
        parent_event_id: str | None = None,
        decision: str | None = None,
        evidence: tuple[str, ...] = (),
        policy_name: str | None = None,
        tool_name: str | None = None,
        redaction_count: int = 0,
        token_estimate: int | None = None,
        payload_redacted: bool = False,
        raw_payload_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "RulenceAuditEvent":
        return cls(
            schema_version=SCHEMA_VERSION,
            event_id=str(uuid.uuid4()),
            parent_event_id=parent_event_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            session_id=session_id,
            corr_id=corr_id,
            runner=runner,
            decision=decision,
            evidence=tuple(evidence),
            policy_name=policy_name,
            tool_name=tool_name,
            redaction_count=redaction_count,
            token_estimate=token_estimate,
            payload_redacted=payload_redacted,
            raw_payload_hash=raw_payload_hash,
            metadata=dict(metadata or {}),
        )
