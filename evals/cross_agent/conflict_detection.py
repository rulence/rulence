"""Eval: completion-with-open-blocker is detected as a conflict."""
from __future__ import annotations

from rulence.context import Blocker, TaskState, detect_conflicts
from rulence.evals import Eval


def run() -> dict:
    state = TaskState.new(
        user_goal="ship audit logging",
        blockers=(Blocker(text="security review pending"),),
    )
    report = detect_conflicts(
        task_state=state,
        final_response="All set, this is done and ready to ship.",
    )
    has_conflict = any(
        c.kind == "completion_with_open_blocker" for c in report.conflicts
    )
    return {
        "status": "pass" if has_conflict else "fail",
        "detail": f"conflicts={[c.kind for c in report.conflicts]}",
        "metrics": {
            "completion_blocker_detected": bool(has_conflict),
            "conflict_count": len(report.conflicts),
        },
    }


EVAL = Eval(
    eval_id="cross_agent.conflict_detection",
    category="cross_agent",
    description="Completion claim with an unresolved blocker is detected as a conflict.",
    claim_ids=("shared_agent_context",),
    runner=run,
)
