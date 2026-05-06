from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from rulence.cli import main as cli_main
from rulence.context import AgentContextBus, ContextStore, TaskState
from rulence.context.resume import ActiveCheckpointStore
from rulence.mcp_server import _call_tool, _tools


class ActiveResumeCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tasks_dir = root / "tasks"
        self.context_dir = root / "context"
        self.checkpoint_dir = root / "active-checkpoints"
        self.env = {
            "RULENCE_TASKS_DIR": str(self.tasks_dir),
            "RULENCE_CONTEXT_DIR": str(self.context_dir),
            "RULENCE_ACTIVE_CHECKPOINT_DIR": str(self.checkpoint_dir),
        }

    def test_context_bus_writes_active_checkpoint_on_task_save(self) -> None:
        with patch.dict(os.environ, self.env, clear=False):
            bus = AgentContextBus(self.tasks_dir, store=ContextStore(self.context_dir))
            state = TaskState.new(
                task_id="rulence_resume_test",
                user_goal="Fix startup resume UX",
                metadata={"next_recommended_step": "Ask Serge continue, pause, or mark done."},
            )
            bus.save_state(state)

            checkpoint = ActiveCheckpointStore(self.checkpoint_dir).load("rulence_resume_test")

        self.assertEqual(checkpoint["task_id"], "rulence_resume_test")
        self.assertEqual(checkpoint["status"], "active")
        self.assertEqual(checkpoint["last_user_intent"], "Fix startup resume UX")
        self.assertEqual(checkpoint["last_completed_step"], "task_state_saved")
        self.assertIn("Continue, pause, or mark done", checkpoint["resume_question"])

    def test_resume_summary_returns_newest_active_checkpoint_prompt(self) -> None:
        with patch.dict(os.environ, self.env, clear=False):
            store = ActiveCheckpointStore(self.checkpoint_dir)
            state = TaskState.new(
                task_id="old_task",
                user_goal="Old paused work",
                status="paused",
            )
            store.write_for_task(state, event_type="task_state_saved")
            active = TaskState.new(
                task_id="new_task",
                user_goal="Current Rulence memory work",
                metadata={"next_recommended_step": "Implement startup prompt."},
            )
            store.write_for_task(active, event_type="snapshot_created")

            summary = store.resume_summary()

        self.assertTrue(summary["has_active_checkpoint"])
        self.assertEqual(summary["checkpoint"]["task_id"], "new_task")
        self.assertIn("Current Rulence memory work", summary["prompt"])
        self.assertIn("Continue, pause, or mark done", summary["prompt"])

    def test_cli_context_resume_prints_startup_prompt(self) -> None:
        with patch.dict(os.environ, self.env, clear=False):
            bus = AgentContextBus(self.tasks_dir, store=ContextStore(self.context_dir))
            bus.save_state(TaskState.new(task_id="cli_resume", user_goal="Resume CLI work"))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli_main(["context", "resume", "--json"])

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["has_active_checkpoint"])
        self.assertIn("Resume CLI work", payload["prompt"])

    def test_mcp_exposes_resume_startup_tool(self) -> None:
        tool_names = {tool["name"] for tool in _tools()}
        self.assertIn("rulence_resume_startup", tool_names)

        with patch.dict(os.environ, self.env, clear=False):
            bus = AgentContextBus(self.tasks_dir, store=ContextStore(self.context_dir))
            bus.save_state(TaskState.new(task_id="mcp_resume", user_goal="Resume MCP work"))
            payload = _call_tool("rulence_resume_startup", {})["structuredContent"]

        self.assertTrue(payload["has_active_checkpoint"])
        self.assertIn("Resume MCP work", payload["prompt"])


if __name__ == "__main__":
    unittest.main()
