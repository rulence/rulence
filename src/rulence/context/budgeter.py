"""Token budgeter for assembled context.

The budgeter takes a ranked list of fragments and a max-token budget
and produces a fitted subset that respects the milestone's preservation
rules:

* Active constraints, explicit user forbids, the current task goal, and
  unresolved final-response blockers are *required* — they are never
  dropped while there is room.
* Optional fragments are added in rank order until the budget is full.
* When the required set already exceeds the budget, the budgeter
  reports ``over_budget=True`` and ``required_fragments_preserved=True``
  rather than silently truncating a constraint.

The budgeter never rewrites fragments. Compression is the
:class:`ContextCompressor`'s job.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .fragment import ContextFragment


@dataclass(frozen=True)
class ContextBudgetResult:
    included_fragments: tuple[ContextFragment, ...]
    dropped_fragments: tuple[ContextFragment, ...]
    drop_reasons: dict[str, str]
    token_estimate_before: int
    token_estimate_after: int
    over_budget: bool
    required_fragments_preserved: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "included_fragment_ids": [f.fragment_id for f in self.included_fragments],
            "dropped_fragment_ids": [f.fragment_id for f in self.dropped_fragments],
            "drop_reasons": dict(self.drop_reasons),
            "token_estimate_before": self.token_estimate_before,
            "token_estimate_after": self.token_estimate_after,
            "over_budget": self.over_budget,
            "required_fragments_preserved": self.required_fragments_preserved,
            "metadata": dict(self.metadata),
        }


def _is_forbid_fragment(fragment: ContextFragment) -> bool:
    if fragment.kind != "constraint":
        return False
    constraint_kind = (fragment.metadata or {}).get("constraint_kind")
    return str(constraint_kind).lower() == "forbids"


def _is_active_constraint(fragment: ContextFragment) -> bool:
    return fragment.kind == "constraint"


def is_required_by_default(fragment: ContextFragment) -> bool:
    """Default rule for fragments the budgeter must preserve.

    Active constraints, explicit user instructions, and final-response
    blocker fragments (kind=open_question or kind=decision marked
    blocker via metadata) all count.
    """
    if _is_active_constraint(fragment):
        return True
    if fragment.kind == "user_instruction":
        return True
    if fragment.kind == "open_question":
        return True
    metadata = fragment.metadata or {}
    if metadata.get("blocker") is True:
        return True
    return False


class ContextBudgeter:
    def __init__(self, *, default_required_predicate=is_required_by_default) -> None:
        self.default_required_predicate = default_required_predicate

    def fit(
        self,
        fragments: Iterable[ContextFragment],
        *,
        max_tokens: int,
        required_ids: Iterable[str] | None = None,
        policy: Any = None,
        model: str | None = None,  # accepted for symmetry; not yet used
    ) -> ContextBudgetResult:
        ordered = list(fragments)
        required_set = set(required_ids or ())
        for fragment in ordered:
            if self.default_required_predicate(fragment):
                required_set.add(fragment.fragment_id)
            if _is_forbid_fragment(fragment):
                required_set.add(fragment.fragment_id)

        token_total_before = sum(int(f.token_estimate or 0) for f in ordered)

        included: list[ContextFragment] = []
        dropped: list[ContextFragment] = []
        drop_reasons: dict[str, str] = {}
        used = 0

        # Required fragments first, preserving their relative rank order.
        for fragment in ordered:
            if fragment.fragment_id not in required_set:
                continue
            included.append(fragment)
            used += int(fragment.token_estimate or 0)

        over_budget = used > max(0, max_tokens)
        # If the required set alone busts the budget, preserve all
        # required and report over_budget. Optional fragments cannot
        # be added without further cost.
        if over_budget:
            for fragment in ordered:
                if fragment.fragment_id not in required_set:
                    dropped.append(fragment)
                    drop_reasons[fragment.fragment_id] = (
                        "over_budget: required-only set already exceeds max_tokens"
                    )
            return ContextBudgetResult(
                included_fragments=tuple(included),
                dropped_fragments=tuple(dropped),
                drop_reasons=drop_reasons,
                token_estimate_before=token_total_before,
                token_estimate_after=used,
                over_budget=True,
                required_fragments_preserved=True,
                metadata={"required_count": len(required_set), "policy": _policy_ref(policy)},
            )

        # Add optional fragments in rank order until the budget is full.
        for fragment in ordered:
            if fragment.fragment_id in required_set:
                continue
            cost = int(fragment.token_estimate or 0)
            if used + cost <= max_tokens:
                included.append(fragment)
                used += cost
            else:
                dropped.append(fragment)
                drop_reasons[fragment.fragment_id] = (
                    f"budget_full: would exceed max_tokens ({used + cost} > {max_tokens})"
                )

        return ContextBudgetResult(
            included_fragments=tuple(included),
            dropped_fragments=tuple(dropped),
            drop_reasons=drop_reasons,
            token_estimate_before=token_total_before,
            token_estimate_after=used,
            over_budget=False,
            required_fragments_preserved=True,
            metadata={
                "required_count": len(required_set),
                "policy": _policy_ref(policy),
            },
        )


def _policy_ref(policy: Any) -> str | None:
    if policy is None:
        return None
    return getattr(policy, "ref", None) or getattr(policy, "label", None)
