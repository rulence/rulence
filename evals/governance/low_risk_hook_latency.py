"""Eval: low-risk PreToolUse dispatch is well under the latency budget.

This is a soft-budget check, not a strict performance test. Failures
indicate the fast path regressed badly, not microsecond-level noise.
"""
from __future__ import annotations

import time

from rulence.cli import _claude_code_pretooluse_dispatch
from rulence.evals import Eval

LATENCY_BUDGET_MS = 50.0


def run() -> dict:
    payload = {
        "session_id": "eval-governance-latency",
        "tool_name": "Read",
        "tool_input": {"file_path": "src/rulence/cli.py"},
        "hook_event_name": "PreToolUse",
    }
    start = time.perf_counter()
    _claude_code_pretooluse_dispatch(payload)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    within_budget = elapsed_ms < LATENCY_BUDGET_MS
    return {
        "status": "pass" if within_budget else "fail",
        "detail": f"elapsed_ms={elapsed_ms:.2f} budget_ms={LATENCY_BUDGET_MS}",
        "metrics": {
            "low_risk_hook_latency_ms": round(elapsed_ms, 4),
            "low_risk_hook_within_budget": bool(within_budget),
        },
    }


EVAL = Eval(
    eval_id="governance.low_risk_hook_latency",
    category="governance",
    description="Low-risk PreToolUse dispatch finishes within a soft latency budget.",
    claim_ids=("tool_risk_classification",),
    runner=run,
)
