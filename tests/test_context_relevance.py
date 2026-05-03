from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from rulence.context import (
    ContextFragment,
    RelevanceContext,
    RelevanceScorer,
    SourceRef,
)
from rulence.logic import Constraint


def _fragment(
    *,
    kind: str = "user_instruction",
    text: str = "the quick brown fox",
    memory_backend: str | None = None,
    metadata: dict | None = None,
    confidence: float = 1.0,
    created_at: str | None = None,
    tool_name: str | None = None,
    fragment_id: str | None = None,
) -> ContextFragment:
    source = SourceRef(
        source_type="user_prompt",
        runner="claude_code",
        memory_backend=memory_backend,
        tool_name=tool_name,
    )
    return ContextFragment.build(
        kind=kind,
        text=text,
        source=source,
        memory_backend=memory_backend,
        metadata=metadata or {},
        confidence=confidence,
        created_at=created_at,
        fragment_id=fragment_id,
    )


class RelevanceScorerTests(unittest.TestCase):
    def test_relevant_fragment_ranks_above_generic_fragment(self) -> None:
        scorer = RelevanceScorer()
        ctx = RelevanceContext(task="Add tests for the auth module")
        relevant = _fragment(text="auth module fixes are needed for tests")
        generic = _fragment(text="totally unrelated discussion of pizza toppings")
        rs_relevant = scorer.score(relevant, ctx)
        rs_generic = scorer.score(generic, ctx)
        self.assertGreater(rs_relevant.score, rs_generic.score)
        self.assertGreater(rs_relevant.signals["lexical_overlap"], 0.0)

    def test_honcho_user_preference_carries_backend_signal(self) -> None:
        scorer = RelevanceScorer()
        ctx = RelevanceContext(
            task="format a python file", preferred_backend="honcho"
        )
        honcho = _fragment(
            kind="honcho_memory",
            text="user prefers tabs over spaces",
            memory_backend="honcho",
            confidence=0.9,
        )
        score = scorer.score(honcho, ctx)
        self.assertGreater(score.signals["backend_weight"], 0.0)
        self.assertGreater(score.signals["memory_confidence"], 0.0)

    def test_mempalace_project_decision_carries_backend_signal(self) -> None:
        scorer = RelevanceScorer()
        ctx = RelevanceContext(
            task="refactor the auth module",
            preferred_backend="mempalace",
        )
        mempalace = _fragment(
            kind="mempalace_memory",
            text="auth module lives at src/auth/; sessions go through Redis",
            memory_backend="mempalace",
            confidence=0.8,
        )
        score = scorer.score(mempalace, ctx)
        self.assertGreater(score.signals["backend_weight"], 0.0)
        self.assertGreater(score.score, 0.0)

    def test_constraint_match_lifts_constraint_with_target_in_task(self) -> None:
        scorer = RelevanceScorer()
        ctx = RelevanceContext(task="write secrets to logs for debugging")
        constraint = _fragment(
            kind="constraint",
            text="forbids(secrets, logs)",
            metadata={
                "constraint_kind": "forbids",
                "constraint_condition": "secrets",
                "constraint_target": "logs",
            },
        )
        score = scorer.score(constraint, ctx)
        self.assertGreaterEqual(score.signals["constraint_match"], 1.0)

    def test_active_constraint_match_uses_current_constraints(self) -> None:
        scorer = RelevanceScorer()
        active = (Constraint("forbids", "secrets", "logs"),)
        ctx = RelevanceContext(task="something else", current_constraints=active)
        constraint = _fragment(
            kind="constraint",
            text="forbids(secrets, logs)",
            metadata={
                "constraint_kind": "forbids",
                "constraint_condition": "secrets",
                "constraint_target": "logs",
            },
        )
        score = scorer.score(constraint, ctx)
        self.assertGreaterEqual(score.signals["constraint_match"], 1.0)

    def test_recency_signal_decays_for_older_fragments(self) -> None:
        scorer = RelevanceScorer()
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        new_ts = datetime.now(timezone.utc).isoformat()
        old = _fragment(created_at=old_ts)
        new = _fragment(created_at=new_ts)
        self.assertLess(
            scorer.score(old).signals["recency"],
            scorer.score(new).signals["recency"],
        )

    def test_tool_dependency_match_lifts_same_tool(self) -> None:
        scorer = RelevanceScorer()
        ctx = RelevanceContext(task="run a command", current_tool="Bash")
        match = _fragment(
            kind="tool_input", text="bash command ls", tool_name="Bash"
        )
        miss = _fragment(
            kind="tool_input", text="read a file", tool_name="Read"
        )
        self.assertGreater(
            scorer.score(match, ctx).signals["tool_dependency"],
            scorer.score(miss, ctx).signals["tool_dependency"],
        )

    def test_contradiction_pattern_lowers_score(self) -> None:
        scorer = RelevanceScorer()
        contradictory = _fragment(text="must always succeed but must never fail")
        score = scorer.score(contradictory)
        self.assertGreater(score.signals["contradiction_penalty"], 0.0)

    def test_rank_orders_by_descending_score(self) -> None:
        scorer = RelevanceScorer()
        ctx = RelevanceContext(task="auth module fixes")
        relevant = _fragment(
            text="auth module needs fixes", fragment_id="frag_relevant"
        )
        irrelevant = _fragment(
            text="random unrelated chatter", fragment_id="frag_irrelevant"
        )
        ranked = scorer.rank([irrelevant, relevant], ctx)
        self.assertEqual(ranked[0][0].fragment_id, "frag_relevant")


if __name__ == "__main__":
    unittest.main()
