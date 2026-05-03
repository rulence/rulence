"""Eval: TokenAccountant.rollup reports a non-negative governance overhead."""
from __future__ import annotations

from rulence.audit.accountant import TokenAccountant
from rulence.evals import Eval


def run() -> dict:
    accountant = TokenAccountant()
    audit_records = [
        {
            "event_type": "PreToolUse",
            "evidence": ["block: rm -rf forbidden", "policy:tier-5"],
            "metadata": {"input_estimate": 12, "output_estimate": 0},
            "corr_id": "ct_overhead",
        },
        {
            "event_type": "Stop",
            "evidence": ["secret leakage detected in final response"],
            "metadata": {"input_estimate": 0, "output_estimate": 8},
            "corr_id": "ct_overhead",
        },
    ]
    rollup = accountant.rollup(audit_records)
    has_overhead = rollup.rulence_overhead_estimate > 0
    return {
        "status": "pass" if has_overhead else "fail",
        "detail": f"overhead={rollup.rulence_overhead_estimate}",
        "metrics": {
            "rulence_overhead_estimate": int(rollup.rulence_overhead_estimate),
            "rulence_overhead_reported": bool(has_overhead),
        },
    }


EVAL = Eval(
    eval_id="token.rulence_overhead_estimated",
    category="token",
    description="The rollup reports an estimated governance overhead from evidence text.",
    claim_ids=("token_accounting",),
    runner=run,
)
