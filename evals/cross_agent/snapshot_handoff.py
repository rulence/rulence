"""Eval: a snapshot handoff records the snapshot id and is replayable."""
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
        bus = AgentContextBus(root / "tasks", store=store)
        state = TaskState.new(user_goal="add audit logs")
        bus.save_state(state)
        for fragment in fragments_from_user_prompt(state.user_goal, corr_id=state.task_id):
            store.add(fragment)
        snapshot = bus.create_snapshot(state.task_id)
        record = bus.handoff(
            from_agent="claude-code",
            to_agent="cursor",
            task_id=state.task_id,
        )
        agent_b = AgentContextBus(root / "tasks", store=store)
        agent_b_state = agent_b.load_state(state.task_id)
    handoff_correct = (
        record["snapshot_id"] == snapshot.snapshot_id
        and agent_b_state.current_snapshot_id == snapshot.snapshot_id
    )
    return {
        "status": "pass" if handoff_correct else "fail",
        "detail": f"snapshot={snapshot.snapshot_id} record={record['snapshot_id']}",
        "metrics": {
            "snapshot_handoff_correct": bool(handoff_correct),
        },
    }


EVAL = Eval(
    eval_id="cross_agent.snapshot_handoff",
    category="cross_agent",
    description="Agent B can load Agent A's snapshot id after handoff.",
    claim_ids=("shared_agent_context",),
    runner=run,
)
