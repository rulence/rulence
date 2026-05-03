"""Deterministic relevance scoring for context fragments.

This module assigns a score to each :class:`ContextFragment` so the
:class:`ContextAssembler` can rank candidates before budgeting. There
are no embeddings, no semantic similarity models, no LLM calls — only
deterministic signals derived from the fragment, the task, and the
caller-supplied governance state.

The score is a sum of weighted signals plus a small contradiction
penalty. Each signal explains itself in :class:`RelevanceScore.signals`
so a downstream audit can tell why a fragment ranked where it did.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from ..logic import Constraint
from .fragment import ContextFragment

# Per-kind base weights. Higher means "more likely to be needed".
KIND_WEIGHTS: dict[str, float] = {
    "user_instruction": 1.0,
    "constraint": 1.5,
    "decision": 1.2,
    "open_question": 1.0,
    "system_policy": 1.0,
    "code_fact": 0.8,
    "honcho_memory": 0.7,
    "mempalace_memory": 0.9,
    "tool_input": 0.5,
    "tool_result": 0.6,
    "summary": 0.6,
    "final_response": 0.6,
    "audit_observation": 0.3,
    "assumption": 0.4,
}

BACKEND_ROLE_WEIGHTS: dict[str, float] = {
    # Internal memory (Honcho) is user-scoped state; external memory
    # (MemPalace) is project-scoped state. A high-tier engineering
    # task tends to lean on project context, so MemPalace gets a
    # slightly higher base weight. Both backends still rank below
    # active constraints.
    "honcho": 0.4,
    "mempalace": 0.6,
}

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_'-]+")
_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:\\|\.{0,2}/|/)?(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"|[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8}"
)
_CAPITALIZED = re.compile(r"\b[A-Z][A-Za-z0-9_]+\b")


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_PATTERN.findall(text or "")}


def _entities(text: str) -> set[str]:
    """Cheap entity extraction: capitalized identifiers and path-like tokens."""
    if not text:
        return set()
    entities = set(_CAPITALIZED.findall(text))
    entities.update(_PATH_PATTERN.findall(text))
    return {entity.strip("./\\") for entity in entities if entity.strip("./\\")}


def _recency_weight(created_at: str | None) -> float:
    """Map the fragment's age to a weight in [0, 1].

    Fragments with no parsable timestamp get the neutral midpoint so
    they are not penalized purely for missing metadata.
    """
    if not created_at:
        return 0.5
    try:
        ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.5
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - ts).total_seconds() / 3600.0)
    # Half-life of about 24 hours; floor at 0.1 so old fragments still
    # contribute when they are the only signal.
    return max(0.1, 0.5 ** (age_hours / 24.0))


@dataclass(frozen=True)
class RelevanceScore:
    fragment_id: str
    score: float
    signals: dict[str, float]
    explanation: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fragment_id": self.fragment_id,
            "score": self.score,
            "signals": dict(self.signals),
            "explanation": list(self.explanation),
        }


@dataclass(frozen=True)
class RelevanceContext:
    """Caller-supplied governance state used by the scorer."""

    task: str = ""
    policy_tier: int = 1
    current_constraints: tuple[Constraint, ...] = ()
    current_tool: str | None = None
    current_event_type: str | None = None
    preferred_backend: str | None = None


class RelevanceScorer:
    def __init__(
        self,
        *,
        kind_weights: dict[str, float] | None = None,
        backend_role_weights: dict[str, float] | None = None,
    ) -> None:
        self.kind_weights = dict(KIND_WEIGHTS)
        if kind_weights:
            self.kind_weights.update(kind_weights)
        self.backend_role_weights = dict(BACKEND_ROLE_WEIGHTS)
        if backend_role_weights:
            self.backend_role_weights.update(backend_role_weights)

    def score(
        self,
        fragment: ContextFragment,
        ctx: RelevanceContext | None = None,
    ) -> RelevanceScore:
        ctx = ctx or RelevanceContext()
        task_tokens = _tokenize(ctx.task)
        task_entities = _entities(ctx.task)
        text = fragment.redacted_text or fragment.text or ""
        fragment_tokens = _tokenize(text)
        fragment_entities = _entities(text)

        signals: dict[str, float] = {}
        explanation: list[str] = []

        # Lexical overlap, normalized by task token count so short tasks
        # don't pin every fragment to 0.
        if task_tokens and fragment_tokens:
            overlap = len(task_tokens & fragment_tokens)
            lexical = overlap / max(1, len(task_tokens))
        else:
            lexical = 0.0
        signals["lexical_overlap"] = lexical
        if lexical:
            explanation.append(f"lexical overlap={lexical:.2f}")

        # Entity overlap weighs path-like and capitalized identifiers.
        if task_entities and fragment_entities:
            entity_overlap = len(task_entities & fragment_entities) / max(
                1, len(task_entities)
            )
        else:
            entity_overlap = 0.0
        signals["entity_overlap"] = entity_overlap
        if entity_overlap:
            explanation.append(f"entity overlap={entity_overlap:.2f}")

        # Recency.
        recency = _recency_weight(fragment.created_at)
        signals["recency"] = recency

        # Kind weight.
        kind_weight = self.kind_weights.get(fragment.kind, 0.5)
        signals["kind_weight"] = kind_weight

        # Backend role weight.
        backend = fragment.memory_backend or fragment.source.memory_backend
        backend_weight = (
            self.backend_role_weights.get(backend, 0.0) if backend else 0.0
        )
        signals["backend_weight"] = backend_weight
        if ctx.preferred_backend and backend == ctx.preferred_backend:
            backend_weight += 0.2
            signals["backend_weight"] = backend_weight
            explanation.append(f"preferred backend match: {backend}")

        # Constraint match: a constraint fragment whose target/condition
        # appears in the task or in the active constraint set ranks high.
        constraint_match = self._constraint_match(fragment, ctx)
        signals["constraint_match"] = constraint_match
        if constraint_match:
            explanation.append("constraint relevant to task")

        # Decision match: decisions that mention the task tokens are
        # likely the active decision the agent should respect.
        decision_match = 0.0
        if fragment.kind == "decision" and task_tokens & fragment_tokens:
            decision_match = 1.0
            explanation.append("decision references task tokens")
        signals["decision_match"] = decision_match

        # Tool dependency match.
        tool_match = 0.0
        if (
            ctx.current_tool
            and fragment.source.tool_name
            and ctx.current_tool == fragment.source.tool_name
        ):
            tool_match = 1.0
            explanation.append(f"same tool as current event ({ctx.current_tool})")
        signals["tool_dependency"] = tool_match

        # Memory confidence: highly weighted memory items keep their
        # confidence if they originated from a memory backend.
        memory_confidence = 0.0
        if backend:
            memory_confidence = float(fragment.confidence or 0.0)
        signals["memory_confidence"] = memory_confidence

        # Risk if omitted: explicit constraints, decisions, and final
        # blockers are expensive to drop, so they get a non-trivial
        # baseline score even when the task tokens don't overlap.
        risk_if_omitted = 0.0
        if fragment.kind in {"constraint", "decision", "open_question"}:
            risk_if_omitted = 0.8
        elif fragment.kind == "user_instruction":
            risk_if_omitted = 0.5
        signals["risk_if_omitted"] = risk_if_omitted

        # Contradiction penalty: fragments that contain both halves of a
        # well-known contradictory pair are demoted.
        contradiction = self._contradiction_penalty(text)
        signals["contradiction_penalty"] = contradiction
        if contradiction:
            explanation.append("contains contradiction pattern")

        # Tier weight: higher tiers care more about constraints and
        # decisions; lower tiers care more about plain instruction.
        tier_weight = 0.0
        if ctx.policy_tier >= 3 and fragment.kind in {"constraint", "decision"}:
            tier_weight = 0.4
        signals["tier_weight"] = tier_weight

        # Final score: weighted sum.
        score = (
            (lexical * 1.5)
            + (entity_overlap * 1.0)
            + (recency * 0.4)
            + (kind_weight * 1.0)
            + (backend_weight * 0.6)
            + (constraint_match * 1.5)
            + (decision_match * 1.0)
            + (tool_match * 1.0)
            + (memory_confidence * 0.3)
            + (risk_if_omitted * 1.0)
            + (tier_weight * 1.0)
            - contradiction
        )
        return RelevanceScore(
            fragment_id=fragment.fragment_id,
            score=round(score, 4),
            signals={k: round(v, 4) for k, v in signals.items()},
            explanation=tuple(explanation),
        )

    def rank(
        self,
        fragments: Iterable[ContextFragment],
        ctx: RelevanceContext | None = None,
    ) -> list[tuple[ContextFragment, RelevanceScore]]:
        scored = [(fragment, self.score(fragment, ctx)) for fragment in fragments]
        scored.sort(key=lambda pair: pair[1].score, reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Internal helpers

    def _constraint_match(
        self,
        fragment: ContextFragment,
        ctx: RelevanceContext,
    ) -> float:
        if fragment.kind != "constraint":
            return 0.0
        condition = (
            fragment.metadata.get("constraint_condition") if fragment.metadata else None
        )
        target = (
            fragment.metadata.get("constraint_target") if fragment.metadata else None
        )
        if not (condition or target):
            return 0.0
        haystack = (ctx.task or "").lower()
        score = 0.0
        if condition and condition.lower() in haystack:
            score += 0.5
        if target and target.lower() in haystack:
            score += 0.5
        # Constraints already in the active set are always relevant.
        for active in ctx.current_constraints:
            if (
                condition
                and target
                and active.condition.lower() == condition.lower()
                and active.target.lower() == target.lower()
            ):
                score = max(score, 1.0)
                break
        return score

    @staticmethod
    def _contradiction_penalty(text: str) -> float:
        if not text:
            return 0.0
        normalized = re.sub(r"\s+", " ", text.lower())
        pairs = (
            ("always", "never"),
            ("must", "must not"),
            ("required", "forbidden"),
        )
        penalty = 0.0
        for left, right in pairs:
            if left in normalized and right in normalized:
                penalty += 0.5
        return min(penalty, 1.0)
