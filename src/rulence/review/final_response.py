"""Deterministic final-response review.

Runs at Stop time and inspects the assistant's last message against:

* the audit trace recorded for the current ``corr_id``;
* constraints parsed from the user prompt and transcript via
  :func:`rulence.logic.parse_constraints`;
* the secret redactor;
* claims of action that have no corresponding tool trace.

The reviewer is intentionally pattern-based and side-effect free. It
does **not** call an LLM, does not summarize, and does not loop. The
Stop-hook integration is responsible for honoring ``stop_hook_active``
to prevent any continuation loop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..logic import Constraint, parse_constraints
from ..security import SecretRedactor


@dataclass(frozen=True)
class FinalReviewResult:
    status: str  # "pass" | "warn" | "fail"
    suggested_action: str  # "allow" | "revise" | "ask_user" | "block"
    evidence: tuple[str, ...]
    violated_constraints: tuple[str, ...]
    missing_required_actions: tuple[str, ...]
    secret_findings: tuple[str, ...]
    trace_mismatches: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "suggested_action": self.suggested_action,
            "evidence": list(self.evidence),
            "violated_constraints": list(self.violated_constraints),
            "missing_required_actions": list(self.missing_required_actions),
            "secret_findings": list(self.secret_findings),
            "trace_mismatches": list(self.trace_mismatches),
        }


# ---------------------------------------------------------------------------
# Action-claim detection
#
# Each pattern represents a class of claim the assistant might make.
# Bash-class claims (test/deploy/migration/install) all collapse onto
# "any Bash event in the trace" because the audit record only stores
# tool_name and a hashed payload — we cannot deterministically tell what
# a given Bash invocation was for.

_BASH_CLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:i|we)\s+(?:just\s+)?ran\s+(?:the\s+)?tests?\b", re.IGNORECASE), "claimed running tests"),
    (re.compile(r"\btests?\s+(?:have\s+)?(?:passed|run|executed)\b", re.IGNORECASE), "claimed tests passed/ran"),
    (re.compile(r"\b(?:i|we)\s+(?:just\s+)?deployed\b", re.IGNORECASE), "claimed deployment"),
    (re.compile(r"\bdeployment\s+(?:is\s+)?complete\b", re.IGNORECASE), "claimed deployment complete"),
    (re.compile(r"\b(?:i|we)\s+ran\s+(?:the\s+)?migrations?\b", re.IGNORECASE), "claimed running migration"),
    (re.compile(r"\b(?:i|we)\s+(?:just\s+)?installed\s+(?:the\s+)?(?:package|dependency|library|module)\b", re.IGNORECASE), "claimed install"),
)

_EDIT_CLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:i|we)\s+(?:updated|edited|modified|wrote|created)\s+(?:the\s+)?(?:file|files|code|config)\b", re.IGNORECASE), "claimed file edit"),
    (re.compile(r"\bI\s+made\s+(?:changes|the\s+changes)\s+to\s+", re.IGNORECASE), "claimed file edit"),
)

_MEMORY_SEARCH_CLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:i|we)\s+(?:searched|queried|looked\s+up)\s+(?:the\s+)?memory\b", re.IGNORECASE), "claimed memory search"),
)

# Words that indicate the assistant is asking the user for confirmation.
_ASK_ACKNOWLEDGEMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:should\s+i|may\s+i|do\s+you\s+want|let\s+me\s+know|ok\s+to\s+proceed|please\s+confirm|need\s+(?:your\s+)?(?:approval|confirmation))\b", re.IGNORECASE),
    re.compile(r"\b(?:waiting\s+for|requesting)\s+(?:your\s+)?(?:approval|confirmation|permission)\b", re.IGNORECASE),
)


def _has_bash_trace(audit_records: Iterable[dict[str, Any]]) -> bool:
    return any(
        r.get("event_type") == "PreToolUse" and r.get("tool_name") == "Bash"
        for r in audit_records
    )


def _has_edit_or_write_trace(audit_records: Iterable[dict[str, Any]]) -> bool:
    return any(
        r.get("event_type") == "PreToolUse" and r.get("tool_name") in ("Edit", "Write")
        for r in audit_records
    )


def _has_memory_search_trace(audit_records: Iterable[dict[str, Any]]) -> bool:
    for r in audit_records:
        tool = r.get("tool_name") or ""
        if (
            r.get("event_type") == "PreToolUse"
            and tool.startswith("mcp__")
            and "search" in tool.lower()
        ):
            return True
    return False


def _action_claim_findings(
    final_response: str,
    audit_records: Iterable[dict[str, Any]],
) -> list[str]:
    records = list(audit_records)
    findings: list[str] = []

    if not _has_bash_trace(records):
        for pattern, label in _BASH_CLAIM_PATTERNS:
            if pattern.search(final_response):
                findings.append(f"{label}; no Bash tool trace recorded")

    if not _has_edit_or_write_trace(records):
        for pattern, label in _EDIT_CLAIM_PATTERNS:
            if pattern.search(final_response):
                findings.append(f"{label}; no Edit/Write tool trace recorded")

    if not _has_memory_search_trace(records):
        for pattern, label in _MEMORY_SEARCH_CLAIM_PATTERNS:
            if pattern.search(final_response):
                findings.append(f"{label}; no MCP search tool trace recorded")

    return findings


def _constraint_findings(
    final_response: str, constraints: tuple[Constraint, ...]
) -> tuple[list[str], list[str]]:
    final_lower = final_response.lower()
    violated: list[str] = []
    missing: list[str] = []
    for constraint in constraints:
        condition = constraint.condition.strip().lower()
        target = constraint.target.strip().lower()
        if not condition or not target:
            continue
        if constraint.kind == "forbids":
            if condition in final_lower and target in final_lower:
                violated.append(
                    f"forbids({constraint.condition}, {constraint.target}); "
                    "final mentions both"
                )
        elif constraint.kind == "requires":
            if condition in final_lower and target not in final_lower:
                missing.append(
                    f"requires({constraint.condition}, {constraint.target}); "
                    f"final mentions {constraint.condition} without {constraint.target}"
                )
    return violated, missing


def _unresolved_ask_finding(
    final_response: str, audit_records: Iterable[dict[str, Any]]
) -> list[str]:
    asks = [r for r in audit_records if r.get("decision") == "ask"]
    if not asks:
        return []
    if any(p.search(final_response) for p in _ASK_ACKNOWLEDGEMENT_PATTERNS):
        return []
    return [
        f"prior governance decision asked for confirmation ({len(asks)} ask event(s)); "
        "final response does not request user confirmation"
    ]


class FinalResponseReviewer:
    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        self.redactor = redactor or SecretRedactor()

    def review(
        self,
        *,
        final_response: str,
        audit_records: list[dict[str, Any]] | None = None,
        user_prompt: str = "",
        constraints: tuple[Constraint, ...] | list[Constraint] | None = None,
        transcript: str = "",
        policy_name: str | None = None,
    ) -> FinalReviewResult:
        text = final_response or ""
        records = list(audit_records or [])

        # 1. Secret leakage
        redaction = self.redactor.redact_text(text)
        secret_findings: list[str] = []
        if redaction.redaction_count > 0:
            unique_types = sorted(set(redaction.redaction_types))
            secret_findings.append(
                f"final response contains {redaction.redaction_count} secret-like "
                f"value(s): {', '.join(unique_types)}"
            )

        # 2/3. Forbids / Requires constraints
        if constraints is None:
            collected = list(parse_constraints(user_prompt))
            collected.extend(parse_constraints(transcript))
            constraints_tuple: tuple[Constraint, ...] = tuple(collected)
        else:
            constraints_tuple = tuple(constraints)
        violated, missing = _constraint_findings(text, constraints_tuple)

        # 4. Action claims without trace
        trace_mismatches = _action_claim_findings(text, records)

        # 5. Unresolved ask
        unresolved = _unresolved_ask_finding(text, records)

        # Status
        if secret_findings or violated:
            status = "fail"
            suggested_action = "block" if secret_findings else "revise"
        elif trace_mismatches or missing or unresolved:
            status = "warn"
            suggested_action = "ask_user" if unresolved else "revise"
        else:
            status = "pass"
            suggested_action = "allow"

        evidence: list[str] = []
        evidence.extend(secret_findings)
        evidence.extend(violated)
        evidence.extend(missing)
        evidence.extend(trace_mismatches)
        evidence.extend(unresolved)

        return FinalReviewResult(
            status=status,
            suggested_action=suggested_action,
            evidence=tuple(evidence),
            violated_constraints=tuple(violated),
            missing_required_actions=tuple(missing) + tuple(unresolved),
            secret_findings=tuple(secret_findings),
            trace_mismatches=tuple(trace_mismatches),
        )
