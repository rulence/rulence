"""Eval: contradictory Honcho/MemPalace memory items raise an internal_vs_external warning."""
from __future__ import annotations

from rulence.context import (
    ContextFragment,
    SourceRef,
    detect_conflicts,
)
from rulence.evals import Eval


def _fragment(kind: str, text: str, backend: str, fragment_id: str) -> ContextFragment:
    return ContextFragment.build(
        kind=kind,
        text=text,
        source=SourceRef(
            source_type="memory_item", runner=None, memory_backend=backend
        ),
        memory_backend=backend,
        fragment_id=fragment_id,
    )


def run() -> dict:
    honcho = _fragment(
        "honcho_memory", "user prefers npm", "honcho", "honcho_eval_1"
    )
    mempalace = _fragment(
        "mempalace_memory", "project standardized on bun", "mempalace", "mempalace_eval_1"
    )
    report = detect_conflicts(fragments=(honcho, mempalace))
    has_conflict = any(
        c.kind == "internal_vs_external_memory" for c in report.conflicts
    )
    return {
        "status": "pass" if has_conflict else "fail",
        "detail": f"conflict_kinds={[c.kind for c in report.conflicts]}",
        "metrics": {
            "memory_contradiction_warned": bool(has_conflict),
            "conflict_count": len(report.conflicts),
        },
    }


EVAL = Eval(
    eval_id="memory.contradictory_memory_warned",
    category="memory",
    description="Contradictory Honcho/MemPalace memory items are flagged as a conflict.",
    claim_ids=("shared_agent_context", "memory_arbitration_policy"),
    runner=run,
)
