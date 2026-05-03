from __future__ import annotations

import json
import unittest

from rulence.context import Blocker, Decision, TaskState
from rulence.logic import Constraint


class TaskStateTests(unittest.TestCase):
    def test_new_assigns_task_id_and_hash(self) -> None:
        state = TaskState.new(user_goal="ship the audit feature")
        self.assertTrue(state.task_id.startswith("task_"))
        self.assertEqual(state.user_goal, "ship the audit feature")
        self.assertTrue(state.content_hash.startswith("sha256:"))
        self.assertEqual(state.status, "open")
        self.assertEqual(state.version, 1)
        self.assertIsNone(state.current_snapshot_id)

    def test_to_dict_round_trips_through_json(self) -> None:
        state = TaskState.new(
            user_goal="add audit logs",
            active_constraints=(Constraint("forbids", "secrets", "logs"),),
            decisions=(Decision(text="use bun for tests"),),
            open_questions=("who approves the migration?",),
            blockers=(Blocker(text="security review pending"),),
            memory_backends_used=("honcho", "mempalace"),
            metadata={"policy_tier": 3},
        )
        encoded = json.dumps(state.to_dict())
        decoded = TaskState.from_dict(json.loads(encoded))
        self.assertEqual(decoded.task_id, state.task_id)
        self.assertEqual(decoded.user_goal, state.user_goal)
        self.assertEqual(decoded.content_hash, state.content_hash)
        self.assertEqual(
            [c.to_dict() for c in decoded.active_constraints],
            [c.to_dict() for c in state.active_constraints],
        )
        self.assertEqual(
            [d.to_dict() for d in decoded.decisions],
            [d.to_dict() for d in state.decisions],
        )
        self.assertEqual(
            [b.to_dict() for b in decoded.blockers],
            [b.to_dict() for b in state.blockers],
        )
        self.assertEqual(decoded.metadata, state.metadata)

    def test_replace_recomputes_hash_and_updated_at(self) -> None:
        state = TaskState.new(user_goal="initial")
        self.assertEqual(state.user_goal, "initial")
        first_hash = state.content_hash
        first_updated = state.updated_at
        # Force a different timestamp by sleeping; the hash should change
        # because user_goal participates in the digest.
        new_state = state.replace(user_goal="updated goal")
        self.assertNotEqual(new_state.content_hash, first_hash)
        self.assertGreaterEqual(new_state.updated_at, first_updated)
        self.assertEqual(new_state.task_id, state.task_id)

    def test_replace_decisions_and_blockers(self) -> None:
        state = TaskState.new(user_goal="goal")
        new_state = state.replace(
            decisions=(Decision(text="rollback the migration"),),
            blockers=(Blocker(text="waiting on review", resolved=False),),
        )
        self.assertEqual(len(new_state.decisions), 1)
        self.assertEqual(len(new_state.blockers), 1)
        self.assertEqual(new_state.decisions[0].text, "rollback the migration")
        self.assertFalse(new_state.blockers[0].resolved)

    def test_from_dict_requires_task_id(self) -> None:
        with self.assertRaises(ValueError):
            TaskState.from_dict({"user_goal": "x"})

    def test_explicit_task_id_is_preserved(self) -> None:
        state = TaskState.new(user_goal="x", task_id="task_explicit")
        self.assertEqual(state.task_id, "task_explicit")

    def test_content_hash_excludes_metadata_and_updated_at(self) -> None:
        # metadata and updated_at are volatile annotations; they must not
        # change the content hash.
        state_a = TaskState.new(user_goal="g", task_id="task_a")
        state_b = state_a.replace(metadata={**state_a.metadata, "extra": "x"})
        self.assertEqual(state_a.content_hash, state_b.content_hash)

        # But two states with different goals hash differently.
        state_c = TaskState.new(user_goal="different", task_id="task_a")
        self.assertNotEqual(state_a.content_hash, state_c.content_hash)


if __name__ == "__main__":
    unittest.main()
