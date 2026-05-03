"""Latency budget for the fast risk classifier.

The fast path's whole purpose is to dispatch low-risk tool calls
without paying for a full preflight. Per-call classification time is
measured here. The threshold is generous (25ms per call) so the test
is not flaky on shared CI runners; in practice classifications run in
microseconds.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from rulence.tool_risk import ToolRiskClassifier


def _isolate_audit() -> tuple[tempfile.TemporaryDirectory, "patch._patch_dict"]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    env_patch = patch.dict(
        os.environ,
        {
            "RULENCE_AUDIT_DIR": str(root / "audit"),
            "RULENCE_STATE_DIR": str(root / "state"),
        },
        clear=False,
    )
    return tmp, env_patch


class ToolRiskLatencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.classifier = ToolRiskClassifier()

    def _measure(self, *, tool_name, tool_input, iterations=1000):
        # Warm up imports / regex compile caches.
        self.classifier.classify(tool_name=tool_name, tool_input=tool_input)
        start = time.perf_counter()
        last = None
        for _ in range(iterations):
            last = self.classifier.classify(tool_name=tool_name, tool_input=tool_input)
        elapsed = time.perf_counter() - start
        per_call_ms = (elapsed / iterations) * 1000.0
        return per_call_ms, last

    def test_low_risk_read_is_well_under_threshold(self) -> None:
        per_call_ms, result = self._measure(
            tool_name="Read",
            tool_input={"file_path": "/home/user/code/main.py"},
        )
        self.assertEqual(result.risk_level, "low")
        self.assertLess(
            per_call_ms,
            25.0,
            f"low-risk Read classification took {per_call_ms:.3f}ms per call",
        )

    def test_low_risk_bash_is_well_under_threshold(self) -> None:
        per_call_ms, result = self._measure(
            tool_name="Bash",
            tool_input={"command": "ls -la"},
        )
        self.assertEqual(result.risk_level, "low")
        self.assertLess(per_call_ms, 25.0)

    def test_critical_classification_is_also_quick(self) -> None:
        per_call_ms, result = self._measure(
            tool_name="Bash",
            tool_input={"command": "rm -rf /tmp/foo"},
        )
        self.assertEqual(result.risk_level, "critical")
        self.assertLess(per_call_ms, 25.0)


class FastPathBypassesPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, env_patch = _isolate_audit()
        self.addCleanup(self._tmp.cleanup)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_low_risk_dispatch_does_not_invoke_preflight(self) -> None:
        # Patch the preflight reference inside cli.py so we can observe
        # whether the dispatcher reached into the heavy path.
        with patch("rulence.cli.preflight_task") as preflight_mock:
            preflight_mock.side_effect = AssertionError(
                "preflight_task must not run on the fast path"
            )
            from rulence.cli import _claude_code_pretooluse_dispatch

            output = _claude_code_pretooluse_dispatch(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "cc-fastpath-1",
                    "tool_name": "Read",
                    "tool_input": {"file_path": "/home/user/code/main.py"},
                }
            )
        self.assertEqual(output, {"suppressOutput": True})
        preflight_mock.assert_not_called()

    def test_medium_risk_dispatch_invokes_preflight(self) -> None:
        with patch("rulence.cli.preflight_task") as preflight_mock:
            from rulence.models import (
                Classification,
                Policy,
                PreflightResult,
            )

            preflight_mock.return_value = PreflightResult(
                classification=Classification(
                    task="dispatch test",
                    label="Tier 0 / direct",
                    tier=0,
                    reasons=("dispatch test",),
                    signals={},
                ),
                policy=Policy(tier=0, label="direct", ref="test", required_checks=()),
                verdict="approve",
                warnings=(),
                blocks=(),
                checks=(),
                token_budget=None,
                decomposition=None,
                next_steps=("proceed",),
            )
            from rulence.cli import _claude_code_pretooluse_dispatch

            _claude_code_pretooluse_dispatch(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "cc-mediumpath-1",
                    "tool_name": "WebFetch",
                    "tool_input": {"url": "https://example.com/foo"},
                }
            )
        preflight_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
