from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rulence.context import (
    AgentContextBus,
    ContextStore,
    TaskNotFoundError,
    TaskState,
    fragments_from_user_prompt,
)


class AgentContextBusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tasks_dir = root / "tasks"
        self.context_dir = root / "context"
        self.store = ContextStore(self.context_dir)
        self.bus = AgentContextBus(self.tasks_dir, store=self.store)

    def _make_task(self, **overrides) -> TaskState:
        defaults = dict(user_goal="add audit logs to auth module")
        defaults.update(overrides)
        state = TaskState.new(**defaults)
        self.bus.save_state(state)
        return state

    def test_save_and_load_round_trip(self) -> None:
        state = self._make_task()
        loaded = self.bus.load_state(state.task_id)
        self.assertEqual(loaded.task_id, state.task_id)
        self.assertEqual(loaded.user_goal, state.user_goal)
        self.assertEqual(loaded.content_hash, state.content_hash)

    def test_missing_task_raises(self) -> None:
        with self.assertRaises(TaskNotFoundError):
            self.bus.load_state("task_does_not_exist")

    def test_publish_appends_event_jsonl_line(self) -> None:
        state = self._make_task()
        record = self.bus.publish(
            state.task_id,
            event_type="custom_event",
            agent="claude-code",
            data={"k": "v"},
        )
        events = self.bus.read_events(state.task_id)
        # There is a save event from setUp + the custom event we just added.
        self.assertTrue(any(e["event_id"] == record["event_id"] for e in events))
        custom = [e for e in events if e["event_type"] == "custom_event"]
        self.assertEqual(len(custom), 1)
        self.assertEqual(custom[0]["agent"], "claude-code")
        self.assertEqual(custom[0]["data"]["k"], "v")

    def test_update_state_recomputes_hash(self) -> None:
        state = self._make_task()
        new_state = self.bus.update_state(
            state.task_id,
            {"user_goal": "do something else"},
            agent="cursor",
        )
        self.assertNotEqual(new_state.content_hash, state.content_hash)
        events = self.bus.read_events(state.task_id)
        self.assertTrue(
            any(e["event_type"] == "task_state_updated" for e in events)
        )

    def test_create_snapshot_attaches_snapshot_id_to_state(self) -> None:
        state = self._make_task()
        # Add some governed fragments tagged with the task id.
        for fragment in fragments_from_user_prompt(
            state.user_goal, corr_id=state.task_id
        ):
            self.store.add(fragment)
        snapshot = self.bus.create_snapshot(state.task_id)
        loaded = self.bus.load_state(state.task_id)
        self.assertEqual(loaded.current_snapshot_id, snapshot.snapshot_id)
        self.assertGreater(len(snapshot.fragment_ids), 0)
        events = self.bus.read_events(state.task_id)
        self.assertTrue(
            any(e["event_type"] == "snapshot_created" for e in events)
        )

    def test_list_tasks_returns_only_tasks_with_state(self) -> None:
        a = self._make_task()
        b = self._make_task()
        ids = self.bus.list_tasks()
        self.assertIn(a.task_id, ids)
        self.assertIn(b.task_id, ids)

    def test_default_directory_uses_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"RULENCE_TASKS_DIR": tmp}, clear=False):
                bus = AgentContextBus()
                state = TaskState.new(user_goal="env-defaulted")
                bus.save_state(state)
                self.assertTrue(
                    (Path(tmp) / state.task_id / "state.json").exists()
                )

    def test_save_state_writes_pretty_json(self) -> None:
        state = self._make_task()
        text = self.bus.state_path(state.task_id).read_text("utf-8")
        decoded = json.loads(text)
        self.assertEqual(decoded["task_id"], state.task_id)


if __name__ == "__main__":
    unittest.main()
