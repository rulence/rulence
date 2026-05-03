"""Eval: a second agent reuses the first agent's ContextStore fragments
without reinserting duplicates after a handoff.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from rulence.context import (
    AgentContextBus,
    ContextStore,
    TaskState,
    fragments_from_user_prompt,
)
from rulence.evals import Eval


def run() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = ContextStore(root / "context")
        bus_a = AgentContextBus(root / "tasks", store=store)
        state = TaskState.new(user_goal="add audit logs to auth")
        bus_a.save_state(state)
        for fragment in fragments_from_user_prompt(
            state.user_goal, corr_id=state.task_id
        ):
            store.add(fragment)
        bus_a.create_snapshot(state.task_id)
        before = len(store.list())

        # Agent B opens the same task and creates another snapshot. The
        # store should not gain new fragments — only a new snapshot
        # record on the task.
        bus_b = AgentContextBus(root / "tasks", store=store)
        bus_b.create_snapshot(state.task_id)
        after = len(store.list())
        no_duplicates = before == after
        agent_b_state = bus_b.load_state(state.task_id)
    has_snapshot = agent_b_state.current_snapshot_id is not None
    return {
        "status": "pass" if no_duplicates and has_snapshot else "fail",
        "detail": f"before={before} after={after} snapshot_id={agent_b_state.current_snapshot_id}",
        "metrics": {
            "shared_context_reused": bool(no_duplicates),
            "fragment_count": before,
        },
    }


EVAL = Eval(
    eval_id="cross_agent.shared_context_reuse",
    category="cross_agent",
    description="Agent B reuses Agent A's context fragments without duplication.",
    claim_ids=("shared_agent_context",),
    runner=run,
)
