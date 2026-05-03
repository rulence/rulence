"""Eval: a write to the credentials scope is denied at the router."""
from __future__ import annotations

from rulence.evals import Eval
from rulence.memory import MemoryArbiter


def run() -> dict:
    arbiter = MemoryArbiter()
    result = arbiter.write(
        content="rotated keys live here",
        scope="credentials",
    )
    denied = not result.written
    return {
        "status": "pass" if denied else "fail",
        "detail": f"written={result.written!r} reason={result.reason!r}",
        "metrics": {
            "sensitive_write_denied": bool(denied),
        },
    }


EVAL = Eval(
    eval_id="memory.sensitive_memory_write_denied",
    category="memory",
    description="Writes to the credentials scope are denied by the arbiter.",
    claim_ids=("memory_arbitration_policy",),
    runner=run,
)
