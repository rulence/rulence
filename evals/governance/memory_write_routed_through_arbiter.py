"""Eval: a credential-shaped memory write is denied by the arbiter.

The arbiter must reject content that would route to a credential-like
scope (or that the M2 redactor flags as a secret), regardless of
backend availability.
"""
from __future__ import annotations

from rulence.evals import Eval
from rulence.memory import MemoryArbiter


def run() -> dict:
    arbiter = MemoryArbiter()
    result = arbiter.write(
        content="api_key=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        scope="credentials",
        sensitivity="credential_like",
    )
    blocked = not result.written
    return {
        "status": "pass" if blocked else "fail",
        "detail": f"written={result.written!r} reason={result.reason!r}",
        "metrics": {
            "memory_write_blocked": bool(blocked),
            "redaction_count": int(result.redaction_count or 0),
        },
    }


EVAL = Eval(
    eval_id="governance.memory_write_routed_through_arbiter",
    category="governance",
    description="A credential-shaped memory write is refused by the arbiter.",
    claim_ids=("memory_arbitration_policy",),
    runner=run,
)
