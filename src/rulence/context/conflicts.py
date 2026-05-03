"""Heuristic conflict detection across task state and context fragments.

The detectors here are deliberately small. They flag only conflicts a
deterministic local pass can recognize without a model:

* a final response or status that marks the task complete while a
  blocker is still unresolved;
* contradictory tool decisions (npm vs bun, yarn vs pnpm, etc.);
* an old memory item that contradicts the latest decision (token
  conflict between Honcho/MemPalace memory and a recorded decision);
* a final response that contradicts an active forbids constraint;
* Honcho internal memory and MemPalace external memory disagreeing on
  the same factual key (path, tool choice, version).

Returned :class:`ConflictReport` lists each conflict with an explicit
``kind`` so dashboards and tests can branch on it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .fragment import ContextFragment
from .task_state import Blocker, TaskState


# Pairs of mutually exclusive tool/runtime choices. The order does not
# matter; each pair is checked symmetrically.
TOOL_CHOICE_PAIRS: tuple[tuple[str, str], ...] = (
    ("npm", "bun"),
    ("npm", "pnpm"),
    ("npm", "yarn"),
    ("yarn", "bun"),
    ("yarn", "pnpm"),
    ("pnpm", "bun"),
    ("pip", "uv"),
    ("pip", "poetry"),
    ("poetry", "uv"),
    ("docker", "podman"),
    ("postgres", "mysql"),
    ("python", "node"),
)


# Keywords that suggest a "task is complete" claim.
COMPLETE_PATTERNS = (
    re.compile(r"\b(done|complete|completed|finished|shipped|ready to ship)\b", re.IGNORECASE),
    re.compile(r"\ball\s+set\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class Conflict:
    kind: str
    detail: str
    evidence: tuple[str, ...] = ()
    fragment_ids: tuple[str, ...] = ()
    severity: str = "warn"  # "info" | "warn" | "block"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "evidence": list(self.evidence),
            "fragment_ids": list(self.fragment_ids),
            "severity": self.severity,
        }


@dataclass(frozen=True)
class ConflictReport:
    conflicts: tuple[Conflict, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflicts": [c.to_dict() for c in self.conflicts],
            "metadata": dict(self.metadata),
        }


def detect_conflicts(
    *,
    task_state: TaskState | None = None,
    fragments: Iterable[ContextFragment] = (),
    final_response: str | None = None,
) -> ConflictReport:
    fragments = tuple(fragments)
    conflicts: list[Conflict] = []

    if task_state is not None:
        conflicts.extend(_completion_with_open_blocker(task_state, final_response))

    conflicts.extend(_tool_choice_conflicts(fragments, task_state))
    conflicts.extend(_memory_vs_decision_conflicts(fragments, task_state))
    conflicts.extend(_final_vs_constraint_conflicts(final_response, fragments, task_state))
    conflicts.extend(_internal_vs_external_memory_conflicts(fragments))

    return ConflictReport(conflicts=tuple(conflicts))


# ----------------------------------------------------------------------
# Detectors


def _completion_with_open_blocker(
    state: TaskState, final_response: str | None
) -> list[Conflict]:
    open_blockers = [b for b in state.blockers if not b.resolved]
    if not open_blockers:
        return []
    claims_complete = state.status in {"complete", "done", "shipped"}
    if not claims_complete and final_response:
        normalized = final_response or ""
        if any(pattern.search(normalized) for pattern in COMPLETE_PATTERNS):
            claims_complete = True
    if not claims_complete:
        return []
    return [
        Conflict(
            kind="completion_with_open_blocker",
            detail=(
                "task marked complete while one or more blockers are unresolved"
            ),
            evidence=tuple(b.text for b in open_blockers),
            severity="block",
        )
    ]


def _tool_choice_conflicts(
    fragments: tuple[ContextFragment, ...],
    state: TaskState | None,
) -> list[Conflict]:
    haystacks: list[tuple[str, str, tuple[str, ...]]] = []
    for fragment in fragments:
        if fragment.kind not in {"decision", "tool_input", "tool_result"}:
            continue
        text = (fragment.redacted_text or fragment.text or "").lower()
        if not text:
            continue
        haystacks.append(
            (fragment.fragment_id, text, (fragment.fragment_id,))
        )
    if state is not None:
        for index, decision in enumerate(state.decisions):
            haystacks.append(
                (f"decision[{index}]", decision.text.lower(), ())
            )

    conflicts: list[Conflict] = []
    for left, right in TOOL_CHOICE_PAIRS:
        left_hits = [h for h in haystacks if _word(left) in h[1]]
        right_hits = [h for h in haystacks if _word(right) in h[1]]
        if left_hits and right_hits:
            evidence = tuple(
                f"{tag}: {text[:80]}"
                for tag, text, _ in (left_hits + right_hits)[:4]
            )
            fragment_ids = tuple(
                fid
                for _, _, ids in (left_hits + right_hits)
                for fid in ids
            )
            conflicts.append(
                Conflict(
                    kind="tool_choice_conflict",
                    detail=f"contradictory tool choice: {left!r} vs {right!r}",
                    evidence=evidence,
                    fragment_ids=fragment_ids,
                    severity="warn",
                )
            )
    return conflicts


def _memory_vs_decision_conflicts(
    fragments: tuple[ContextFragment, ...],
    state: TaskState | None,
) -> list[Conflict]:
    if state is None or not state.decisions:
        return []
    conflicts: list[Conflict] = []
    decision_norms = [
        (decision, _normalize(decision.text)) for decision in state.decisions
    ]
    memory_fragments = [
        f for f in fragments if f.kind in {"honcho_memory", "mempalace_memory"}
    ]
    for fragment in memory_fragments:
        text = _normalize(fragment.redacted_text or fragment.text or "")
        for decision, decision_text in decision_norms:
            if not decision_text:
                continue
            if _explicit_negation(text, decision_text):
                conflicts.append(
                    Conflict(
                        kind="memory_vs_decision",
                        detail=(
                            f"memory backend {fragment.memory_backend!r} "
                            f"contradicts decision: {decision.text!r}"
                        ),
                        evidence=(text[:120], decision.text[:120]),
                        fragment_ids=(fragment.fragment_id,),
                        severity="warn",
                    )
                )
    return conflicts


def _final_vs_constraint_conflicts(
    final_response: str | None,
    fragments: tuple[ContextFragment, ...],
    state: TaskState | None,
) -> list[Conflict]:
    if not final_response:
        return []
    conflicts: list[Conflict] = []
    constraint_pairs: list[tuple[str, str]] = []
    if state is not None:
        for constraint in state.active_constraints:
            if constraint.kind != "forbids":
                continue
            constraint_pairs.append(
                (constraint.condition.lower(), constraint.target.lower())
            )
    for fragment in fragments:
        if fragment.kind != "constraint":
            continue
        meta = fragment.metadata or {}
        if str(meta.get("constraint_kind", "")).lower() != "forbids":
            continue
        condition = str(meta.get("constraint_condition", "")).lower()
        target = str(meta.get("constraint_target", "")).lower()
        if condition and target:
            constraint_pairs.append((condition, target))
    text = final_response.lower()
    for condition, target in constraint_pairs:
        if condition and target and condition in text and target in text:
            conflicts.append(
                Conflict(
                    kind="final_vs_constraint",
                    detail=(
                        f"final response references both {condition!r} and "
                        f"{target!r}, violating an active forbids"
                    ),
                    evidence=(condition, target),
                    severity="block",
                )
            )
    return conflicts


def _internal_vs_external_memory_conflicts(
    fragments: tuple[ContextFragment, ...],
) -> list[Conflict]:
    honcho_texts = [
        (f.fragment_id, _normalize(f.redacted_text or f.text or ""))
        for f in fragments
        if f.kind == "honcho_memory"
    ]
    mempalace_texts = [
        (f.fragment_id, _normalize(f.redacted_text or f.text or ""))
        for f in fragments
        if f.kind == "mempalace_memory"
    ]
    if not honcho_texts or not mempalace_texts:
        return []
    conflicts: list[Conflict] = []
    for honcho_id, honcho_text in honcho_texts:
        for mempalace_id, mempalace_text in mempalace_texts:
            for left, right in TOOL_CHOICE_PAIRS:
                left_word = _word(left)
                right_word = _word(right)
                if (
                    (left_word in honcho_text and right_word in mempalace_text)
                    or (right_word in honcho_text and left_word in mempalace_text)
                ):
                    conflicts.append(
                        Conflict(
                            kind="internal_vs_external_memory",
                            detail=(
                                "Honcho internal memory and MemPalace external "
                                f"memory disagree on tool choice: {left!r} vs {right!r}"
                            ),
                            evidence=(
                                f"honcho: {honcho_text[:120]}",
                                f"mempalace: {mempalace_text[:120]}",
                            ),
                            fragment_ids=(honcho_id, mempalace_id),
                            severity="warn",
                        )
                    )
    return conflicts


# ----------------------------------------------------------------------
# Helpers


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _word(token: str) -> str:
    """Match the token surrounded by non-word boundaries to avoid partial hits.

    We use a simple sentinel pattern rather than ``re.search`` so the
    caller can keep using fast substring checks against ``haystack``.
    """
    return token.lower()


_NEGATION_PATTERNS = (
    re.compile(r"\b(do not|don't|never|avoid|deprecated|stop using|no longer)\b"),
)


def _explicit_negation(memory_text: str, decision_text: str) -> bool:
    if not decision_text:
        return False
    if decision_text not in memory_text:
        return False
    # Only call it a contradiction when the memory line negates the
    # decision text. ``memory_text`` already includes ``decision_text``
    # somewhere; check for a negation cue near the decision substring.
    index = memory_text.find(decision_text)
    window = memory_text[max(0, index - 60):index]
    return any(pattern.search(window) for pattern in _NEGATION_PATTERNS)
