"""Eval: compression reduces estimated tokens while preserving constraints."""
from __future__ import annotations

from rulence.context import ContextCompressor, ContextFragment, SourceRef
from rulence.evals import Eval


def _fragment(kind: str, text: str, fragment_id: str, *, metadata=None, tokens=None):
    return ContextFragment.build(
        kind=kind,
        text=text,
        source=SourceRef(source_type="user_prompt", runner="claude_code"),
        fragment_id=fragment_id,
        metadata=metadata or {},
        token_estimate=tokens,
    )


def run() -> dict:
    compressor = ContextCompressor()
    repeated_log = "REPEATED LOG LINE\n" * 80
    fragments = [
        _fragment("user_instruction", "Add audit logs without leaking secrets.", "frag_instr"),
        _fragment(
            "constraint",
            "forbids(secrets, logs)",
            "frag_forbid",
            metadata={"constraint_kind": "forbids"},
        ),
        _fragment("decision", "rollback the migration", "frag_decision"),
        _fragment("tool_result", repeated_log, "frag_tr", tokens=200),
    ]
    result = compressor.compress(fragments)
    reduced = result.token_estimate_after < result.token_estimate_before
    constraint_kept = "forbids(secrets, logs)" in result.text
    decision_kept = "rollback the migration" in result.text
    overall = reduced and constraint_kept and decision_kept and result.validation_status == "pass"
    return {
        "status": "pass" if overall else "fail",
        "detail": (
            f"before={result.token_estimate_before} after={result.token_estimate_after} "
            f"validation={result.validation_status}"
        ),
        "metrics": {
            "compression_reduces_tokens": bool(reduced),
            "estimated_token_reduction": int(
                max(0, result.token_estimate_before - result.token_estimate_after)
            ),
            "constraint_preserved": bool(constraint_kept),
            "decision_preserved": bool(decision_kept),
            "compression_validation_pass": result.validation_status == "pass",
        },
    }


EVAL = Eval(
    eval_id="context.compression_reduces_tokens",
    category="context",
    description="Deterministic compression reduces estimated tokens while preserving constraints and decisions.",
    claim_ids=(
        "context_compression_local_benchmark",
        "context_assembly",
    ),
    runner=run,
)
