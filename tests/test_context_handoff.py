from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from rulence.cli import main as cli_main
from rulence.context import (
    AgentContextBus,
    ContextStore,
    TaskNotFoundError,
    TaskState,
    fragments_from_user_prompt,
)


class HandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tasks_dir = root / "tasks"
        self.context_dir = root / "context"
        self.store = ContextStore(self.context_dir)
        self.bus = AgentContextBus(self.tasks_dir, store=self.store)

    def _seed(self) -> TaskState:
        state = TaskState.new(user_goal="add audit logs")
        self.bus.save_state(state)
        for fragment in fragments_from_user_prompt(
            state.user_goal, corr_id=state.task_id
        ):
            self.store.add(fragment)
        self.bus.create_snapshot(state.task_id)
        return self.bus.load_state(state.task_id)

    def test_handoff_records_snapshot_id_in_event(self) -> None:
        state = self._seed()
        record = self.bus.handoff(
            from_agent="claude-code",
            to_agent="cursor",
            task_id=state.task_id,
        )
        self.assertEqual(record["from_agent"], "claude-code")
        self.assertEqual(record["to_agent"], "cursor")
        self.assertEqual(record["snapshot_id"], state.current_snapshot_id)
        events = self.bus.read_events(state.task_id)
        handoff_events = [e for e in events if e["event_type"] == "handoff"]
        self.assertEqual(len(handoff_events), 1)
        self.assertEqual(
            handoff_events[0]["data"]["snapshot_id"], state.current_snapshot_id
        )

    def test_handoff_without_snapshot_raises(self) -> None:
        state = TaskState.new(user_goal="goal without snapshot")
        self.bus.save_state(state)
        with self.assertRaises(ValueError):
            self.bus.handoff(
                from_agent="claude-code",
                to_agent="cursor",
                task_id=state.task_id,
            )

    def test_agent_b_can_load_agent_a_snapshot(self) -> None:
        state = self._seed()
        # Simulate agent A handoff to agent B via the bus.
        record = self.bus.handoff(
            from_agent="claude-code",
            to_agent="cursor",
            task_id=state.task_id,
        )
        # Agent B (a fresh bus pointing at the same dir) should see the
        # current state and be able to look up the snapshot id without
        # calling create_snapshot itself.
        agent_b_bus = AgentContextBus(self.tasks_dir, store=self.store)
        loaded = agent_b_bus.load_state(state.task_id)
        self.assertEqual(loaded.current_snapshot_id, record["snapshot_id"])
        # Source fragments are still indexed by the shared ContextStore.
        fragments = self.store.by_corr_id(state.task_id)
        self.assertGreater(len(fragments), 0)

    def test_duplicate_retrieval_avoided_through_shared_store(self) -> None:
        state = self._seed()
        before = len(self.store.list())
        # Agent A creates a snapshot from existing fragments. Agent B
        # creating a fresh snapshot should not duplicate fragments in
        # the store; it only writes a new snapshot record on the task.
        agent_b_bus = AgentContextBus(self.tasks_dir, store=self.store)
        agent_b_bus.create_snapshot(state.task_id)
        after = len(self.store.list())
        self.assertEqual(before, after)

    def test_missing_task_raises(self) -> None:
        with self.assertRaises(TaskNotFoundError):
            self.bus.handoff(
                from_agent="a", to_agent="b", task_id="task_missing"
            )

    def test_cli_handoff_round_trip(self) -> None:
        # End-to-end through the CLI: init, snapshot, handoff.
        env = {
            "RULENCE_TASKS_DIR": str(self.tasks_dir),
            "RULENCE_CONTEXT_DIR": str(self.context_dir),
        }
        with patch.dict(os.environ, env, clear=False):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli_main([
                    "context", "init", "--task", "task_cli", "--goal",
                    "do the thing", "--json",
                ])
            self.assertEqual(code, 0)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli_main(["context", "snapshot", "--task", "task_cli", "--json"])
            self.assertEqual(code, 0)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli_main([
                    "context", "handoff", "--task", "task_cli",
                    "--from", "claude-code", "--to", "cursor", "--json",
                ])
            self.assertEqual(code, 0)
            output = stdout.getvalue()
            self.assertIn("claude-code", output)
            self.assertIn("cursor", output)


class SamePolicySameVerdictAcrossSimulatedRunnersTests(unittest.TestCase):
    """Verify Rulence preflight gives the same verdict on the same task
    regardless of which simulated runner asked. This is a thin proxy for
    'same policy across simulated runners' from M11."""

    def test_same_policy_same_verdict(self) -> None:
        from rulence.preflight import preflight_task

        task = "delete the production database"
        result_claude = preflight_task(task)
        result_cursor = preflight_task(task)
        self.assertEqual(result_claude.verdict, result_cursor.verdict)
        self.assertEqual(
            [c.name for c in result_claude.checks],
            [c.name for c in result_cursor.checks],
        )


if __name__ == "__main__":
    unittest.main()
