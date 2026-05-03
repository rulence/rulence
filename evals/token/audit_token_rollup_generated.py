"""Eval: TokenAccountant.rollup aggregates tokens across audit records."""
from __future__ import annotations

from rulence.audit.accountant import TokenAccountant
from rulence.evals import Eval


def run() -> dict:
    accountant = TokenAccountant()
    audit_records = [
        {
            "event_type": "UserPromptSubmit",
            "metadata": {"input_estimate": 50, "output_estimate": 0},
            "corr_id": "ct_1",
        },
        {
            "event_type": "PostToolUse",
            "metadata": {"input_estimate": 0, "output_estimate": 200},
            "corr_id": "ct_1",
            "tool_name": "Bash",
        },
        {
            "event_type": "Stop",
            "metadata": {"input_estimate": 0, "output_estimate": 80},
            "corr_id": "ct_1",
        },
    ]
    rollup = accountant.rollup(audit_records)
    correct = (
        rollup.total_estimated_input == 50
        and rollup.total_estimated_output == 280
        and rollup.total_estimated == 330
    )
    return {
        "status": "pass" if correct else "fail",
        "detail": f"input={rollup.total_estimated_input} output={rollup.total_estimated_output}",
        "metrics": {
            "token_rollup_correct": bool(correct),
            "rollup_total_estimated": rollup.total_estimated,
        },
    }


EVAL = Eval(
    eval_id="token.audit_token_rollup_generated",
    category="token",
    description="TokenAccountant.rollup sums input/output estimates across audit records.",
    claim_ids=("token_accounting",),
    runner=run,
)
