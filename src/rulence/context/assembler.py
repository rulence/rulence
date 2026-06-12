"""Pipeline that assembles, dedupes, ranks, budgets, and compresses
context fragments into a :class:`ContextSnapshot`.

The assembler is the public entry point a runtime calls to materialize
the governed context for the next reasoning step. It:

1. Pulls candidates from a :class:`ContextStore` (filtered by
   ``corr_id`` / ``session_id`` if provided).
2. Adds caller-supplied Honcho and MemPalace memory items as fragments.
3. Scores every candidate via :class:`RelevanceScorer`.
4. Dedupes by content hash and near-exact text.
5. Splits fragments into "required" (active constraints, current task
   user instructions, blockers) and "optional".
6. Calls :class:`ContextBudgeter` to fit the final list into the budget.
7. Optionally compresses the fitted list with :class:`ContextCompressor`.
8. Records a :class:`ContextSnapshot` describing what made the cut and,
   when an :class:`rulence.audit.AuditTraceStore` is configured, appends
   a ``ContextAssembled`` audit event so assembly decisions are
   inspectable.

When a :class:`ContextStore` is configured the caller must scope the
assembly via ``corr_id``, ``session_id``, or ``task_id``; an unscoped
``store.list()`` fallback would mix fragments across tasks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..audit import AuditTraceStore, RulenceAuditEvent
from ..logic import Constraint
from .budgeter import ContextBudgeter, ContextBudgetResult
from .compressor import CompressedContext, ContextCompressor
from .extract import (
    fragments_from_honcho_memory_item,
    fragments_from_mempalace_memory_item,
)
from .fragment import ContextFragment
from .relevance import RelevanceContext, RelevanceScore, RelevanceScorer
from .snapshot import ContextSnapshot
from .store import ContextStore


@dataclass(frozen=True)
class AssemblyResult:
    snapshot: ContextSnapshot
    budget: ContextBudgetResult
    compressed: CompressedContext | None
    scores: tuple[RelevanceScore, ...]
    dropped_fragment_ids: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot.to_dict(),
            "budget": self.budget.to_dict(),
            "compressed": self.compressed.to_dict() if self.compressed else None,
            "scores": [score.to_dict() for score in self.scores],
            "dropped_fragment_ids": list(self.dropped_fragment_ids),
            "metadata": dict(self.metadata),
        }


class ContextAssembler:
    def __init__(
        self,
        *,
        store: ContextStore | None = None,
        scorer: RelevanceScorer | None = None,
        budgeter: ContextBudgeter | None = None,
        compressor: ContextCompressor | None = None,
        audit_store: AuditTraceStore | None = None,
        runner: str = "rulence-context",
    ) -> None:
        self.store = store
        self.scorer = scorer or RelevanceScorer()
        self.budgeter = budgeter or ContextBudgeter()
        self.compressor = compressor or ContextCompressor()
        self.audit_store = audit_store
        self.runner = runner

    def assemble(
        self,
        task: str,
        *,
        corr_id: str | None = None,
        session_id: str | None = None,
        max_tokens: int = 4000,
        policy: Any = None,
        policy_tier: int = 1,
        current_constraints: tuple[Constraint, ...] = (),
        current_tool: str | None = None,
        preferred_backend: str | None = None,
        honcho_items: Iterable[Any] = (),
        mempalace_items: Iterable[Any] = (),
        extra_fragments: Iterable[ContextFragment] = (),
        compress: bool = True,
        task_id: str | None = None,
    ) -> AssemblyResult:
        candidates: list[ContextFragment] = []

        # 1. Pull candidates from the store (if any). A configured store
        # must be scoped — unscoped ``store.list()`` would mix fragments
        # across tasks and silently break governance boundaries.
        if self.store is not None:
            if corr_id:
                candidates.extend(self.store.by_corr_id(corr_id))
            elif session_id:
                candidates.extend(self.store.by_session_id(session_id))
            elif task_id:
                candidates.extend(self.store.by_corr_id(task_id))
            else:
                raise ValueError(
                    "ContextAssembler.assemble requires corr_id, session_id, "
                    "or task_id when a ContextStore is configured."
                )

        # 2. Memory items become fragments inline. They are not
        # persisted by this call; that is the arbiter's job.
        for item in honcho_items:
            candidates.extend(
                fragments_from_honcho_memory_item(
                    item,
                    runner=None,
                    session_id=session_id,
                    corr_id=corr_id,
                )
            )
        for item in mempalace_items:
            candidates.extend(
                fragments_from_mempalace_memory_item(
                    item,
                    runner=None,
                    session_id=session_id,
                    corr_id=corr_id,
                )
            )

        candidates.extend(extra_fragments)

        # 3. Dedupe by content hash and near-exact text.
        candidates = self._dedupe(candidates)

        # 4. Score and rank.
        ctx = RelevanceContext(
            task=task,
            policy_tier=policy_tier,
            current_constraints=tuple(current_constraints),
            current_tool=current_tool,
            preferred_backend=preferred_backend,
        )
        ranked = self.scorer.rank(candidates, ctx)
        ranked_fragments = [fragment for fragment, _ in ranked]
        scores = tuple(score for _, score in ranked)

        # 5. Determine required ids: active constraints (kind=constraint),
        # the user instruction(s) (the task), explicit blockers, plus
        # any forbids fragment.
        required_ids: set[str] = set()
        for fragment in ranked_fragments:
            metadata = fragment.metadata or {}
            if fragment.kind == "constraint":
                required_ids.add(fragment.fragment_id)
            if fragment.kind == "open_question":
                required_ids.add(fragment.fragment_id)
            if metadata.get("blocker") is True:
                required_ids.add(fragment.fragment_id)
            if fragment.kind == "user_instruction":
                required_ids.add(fragment.fragment_id)

        # 6. Budget.
        budget = self.budgeter.fit(
            ranked_fragments,
            max_tokens=max_tokens,
            required_ids=required_ids,
            policy=policy,
        )

        # 7. Optional compression.
        compressed: CompressedContext | None = None
        if compress and budget.included_fragments:
            compressed = self.compressor.compress(budget.included_fragments)

        # 8. Snapshot.
        snapshot = ContextSnapshot.build(
            budget.included_fragments,
            task_id=task_id,
            corr_id=corr_id,
            policy_name=_policy_ref(policy),
            metadata={
                "max_tokens": max_tokens,
                "policy_tier": policy_tier,
                "current_tool": current_tool,
                "preferred_backend": preferred_backend,
                "compressed": compressed is not None,
                "validation_status": (
                    compressed.validation_status if compressed else None
                ),
            },
        )

        dropped_ids = tuple(f.fragment_id for f in budget.dropped_fragments)
        metadata = {
            "candidate_count": len(candidates),
            "ranked_count": len(ranked_fragments),
            "required_count": len(required_ids),
            "policy_tier": policy_tier,
            "preferred_backend": preferred_backend,
            "current_tool": current_tool,
        }

        # 9. Audit. Assembly decisions must be inspectable.
        if self.audit_store is not None:
            audit_session = session_id or task_id or corr_id or "rulence-context"
            event = RulenceAuditEvent.now(
                event_type="ContextAssembled",
                session_id=audit_session,
                runner=self.runner,
                corr_id=corr_id,
                policy_name=_policy_ref(policy),
                tool_name=current_tool,
                token_estimate=budget.token_estimate_after,
                metadata={
                    "snapshot_id": snapshot.snapshot_id,
                    "task_id": task_id,
                    "candidate_count": len(candidates),
                    "ranked_count": len(ranked_fragments),
                    "required_count": len(required_ids),
                    "included_count": len(budget.included_fragments),
                    "dropped_count": len(budget.dropped_fragments),
                    "token_estimate_before": budget.token_estimate_before,
                    "token_estimate_after": budget.token_estimate_after,
                    "max_tokens": max_tokens,
                    "policy_tier": policy_tier,
                    "preferred_backend": preferred_backend,
                    "compressed": compressed is not None,
                    "validation_status": (
                        compressed.validation_status if compressed else None
                    ),
                    "memory_backends_used": list(snapshot.memory_backends_used),
                },
            )
            self.audit_store.append(event)

        return AssemblyResult(
            snapshot=snapshot,
            budget=budget,
            compressed=compressed,
            scores=scores,
            dropped_fragment_ids=dropped_ids,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Internal helpers

    @staticmethod
    def _dedupe(
        fragments: Iterable[ContextFragment],
    ) -> list[ContextFragment]:
        seen_hashes: set[str] = set()
        seen_text: set[str] = set()
        out: list[ContextFragment] = []
        for fragment in fragments:
            if fragment.content_hash and fragment.content_hash in seen_hashes:
                continue
            normalized = re.sub(
                r"\s+",
                " ",
                (fragment.redacted_text or fragment.text or "").strip().lower(),
            )
            if normalized and normalized in seen_text:
                continue
            if fragment.content_hash:
                seen_hashes.add(fragment.content_hash)
            if normalized:
                seen_text.add(normalized)
            out.append(fragment)
        return out


def _policy_ref(policy: Any) -> str | None:
    if policy is None:
        return None
    return getattr(policy, "ref", None) or getattr(policy, "label", None)
