"""Eval: the assembler dedupes fragments with identical text."""
from __future__ import annotations

from rulence.context import ContextAssembler, ContextFragment, SourceRef
from rulence.evals import Eval


def _fragment(text: str, fragment_id: str) -> ContextFragment:
    return ContextFragment.build(
        kind="user_instruction",
        text=text,
        source=SourceRef(source_type="user_prompt", runner="claude_code"),
        fragment_id=fragment_id,
    )


def run() -> dict:
    asm = ContextAssembler()
    a = _fragment("duplicate text body", "frag_a")
    b = _fragment("duplicate text body", "frag_b")
    result = asm.assemble(
        "duplicate text body",
        extra_fragments=(a, b),
        max_tokens=1000,
        compress=False,
    )
    included_count = len(result.budget.included_fragments)
    deduped = included_count == 1
    return {
        "status": "pass" if deduped else "fail",
        "detail": f"included_count={included_count}",
        "metrics": {
            "duplicates_removed": bool(deduped),
            "included_fragment_count": included_count,
        },
    }


EVAL = Eval(
    eval_id="context.duplicate_fragment_removed",
    category="context",
    description="Duplicate fragments are removed by the assembler.",
    claim_ids=("context_assembly",),
    runner=run,
)
