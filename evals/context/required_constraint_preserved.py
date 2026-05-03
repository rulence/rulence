"""Eval: an active forbids constraint survives a tight token budget."""
from __future__ import annotations

from rulence.context import ContextAssembler, ContextFragment, SourceRef
from rulence.evals import Eval


def _constraint(fragment_id: str) -> ContextFragment:
    return ContextFragment.build(
        kind="constraint",
        text="forbids(secrets, logs)",
        source=SourceRef(source_type="user_prompt", runner="claude_code"),
        fragment_id=fragment_id,
        metadata={
            "constraint_kind": "forbids",
            "constraint_condition": "secrets",
            "constraint_target": "logs",
        },
        token_estimate=20,
    )


def _bulk(fragment_id: str) -> ContextFragment:
    return ContextFragment.build(
        kind="tool_result",
        text="x" * 800,
        source=SourceRef(source_type="user_prompt", runner="claude_code"),
        fragment_id=fragment_id,
        token_estimate=300,
    )


def run() -> dict:
    asm = ContextAssembler()
    forbid = _constraint("frag_forbid")
    big = _bulk("frag_big")
    result = asm.assemble(
        "do something",
        extra_fragments=(forbid, big),
        max_tokens=80,
        compress=False,
    )
    included_ids = {f.fragment_id for f in result.budget.included_fragments}
    preserved = "frag_forbid" in included_ids
    big_dropped = "frag_big" not in included_ids
    return {
        "status": "pass" if preserved and big_dropped else "fail",
        "detail": f"included={sorted(included_ids)} dropped={sorted(result.dropped_fragment_ids)}",
        "metrics": {
            "required_constraint_preserved": bool(preserved),
            "optional_dropped": bool(big_dropped),
        },
    }


EVAL = Eval(
    eval_id="context.required_constraint_preserved",
    category="context",
    description="An active forbids constraint survives a tight token budget.",
    claim_ids=("context_assembly",),
    runner=run,
)
