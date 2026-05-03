"""Unified governance decision model.

A :class:`GovernanceDecision` is the value type produced by either the
fast path (``rulence.tool_risk``) or the full preflight pipeline. It
collapses risk metadata, evidence, and the runtime ``action`` into one
JSON-serializable record so downstream consumers (the audit store, hook
dispatchers, future runtime adapters) do not have to know whether a
decision came from the fast path or the heavy path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Allowed action values, in order of escalation.
ACTIONS = ("allow", "warn", "ask", "deny")
SEVERITIES = ("low", "medium", "high", "critical")


@dataclass(frozen=True)
class GovernanceDecision:
    action: str
    severity: str
    evidence: tuple[str, ...]
    policy_refs: tuple[str, ...]
    risk_level: str
    risk_reasons: tuple[str, ...]
    required_followups: tuple[str, ...]
    audit_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(
                f"GovernanceDecision.action must be one of {ACTIONS}; got {self.action!r}"
            )
        if self.severity not in SEVERITIES:
            raise ValueError(
                f"GovernanceDecision.severity must be one of {SEVERITIES}; got {self.severity!r}"
            )
        if self.risk_level not in SEVERITIES:
            raise ValueError(
                f"GovernanceDecision.risk_level must be one of {SEVERITIES}; got {self.risk_level!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "severity": self.severity,
            "evidence": list(self.evidence),
            "policy_refs": list(self.policy_refs),
            "risk_level": self.risk_level,
            "risk_reasons": list(self.risk_reasons),
            "required_followups": list(self.required_followups),
            "audit_metadata": dict(self.audit_metadata),
        }

    # ------------------------------------------------------------------
    # Convenience constructors

    @classmethod
    def allow_fast_path(
        cls,
        *,
        risk_level: str = "low",
        risk_reasons: tuple[str, ...] = (),
        audit_metadata: dict[str, Any] | None = None,
    ) -> "GovernanceDecision":
        return cls(
            action="allow",
            severity=risk_level,
            evidence=(),
            policy_refs=(),
            risk_level=risk_level,
            risk_reasons=risk_reasons,
            required_followups=(),
            audit_metadata={"fast_path": True, **(audit_metadata or {})},
        )

    @classmethod
    def from_preflight(
        cls,
        verdict: str,
        *,
        evidence: tuple[str, ...],
        policy_refs: tuple[str, ...],
        risk_level: str,
        risk_reasons: tuple[str, ...] = (),
        required_followups: tuple[str, ...] = (),
        severity: str | None = None,
        audit_metadata: dict[str, Any] | None = None,
    ) -> "GovernanceDecision":
        action_map = {"approve": "allow", "warn": "ask", "block": "deny"}
        action = action_map.get(verdict, "warn")
        return cls(
            action=action,
            severity=severity or risk_level,
            evidence=evidence,
            policy_refs=policy_refs,
            risk_level=risk_level,
            risk_reasons=risk_reasons,
            required_followups=required_followups,
            audit_metadata={"fast_path": False, **(audit_metadata or {})},
        )
