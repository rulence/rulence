"""Eval: the relevance scorer ranks an on-task fragment above an off-task one."""
from __future__ import annotations

from rulence.context import ContextFragment, RelevanceContext, RelevanceScorer, SourceRef
from rulence.evals import Eval


def _fragment(text: str, fragment_id: str) -> ContextFragment:
    return ContextFragment.build(
        kind="user_instruction",
        text=text,
        source=SourceRef(source_type="user_prompt", runner="claude_code"),
        fragment_id=fragment_id,
    )


def run() -> dict:
    scorer = RelevanceScorer()
    ctx = RelevanceContext(task="Add tests for the auth module")
    relevant = _fragment("auth module fixes are needed for tests", "frag_relevant")
    irrelevant = _fragment("totally unrelated discussion of pizza toppings", "frag_irrelevant")
    rs_relevant = scorer.score(relevant, ctx)
    rs_irrelevant = scorer.score(irrelevant, ctx)
    correct = rs_relevant.score > rs_irrelevant.score
    return {
        "status": "pass" if correct else "fail",
        "detail": f"relevant={rs_relevant.score:.3f} irrelevant={rs_irrelevant.score:.3f}",
        "metrics": {
            "relevance_correct_top": bool(correct),
            "relevance_score_gap": round(rs_relevant.score - rs_irrelevant.score, 4),
        },
    }


EVAL = Eval(
    eval_id="context.relevant_fragment_selected",
    category="context",
    description="The relevance scorer ranks an on-task fragment above an off-task one.",
    claim_ids=("context_assembly",),
    runner=run,
)
