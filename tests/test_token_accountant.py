"""Unit tests for the TokenAccountant, TokenRollup, and TokenBudget.

These tests use synthetic audit records and short text inputs only.
"""
from __future__ import annotations

import unittest

from rulence.audit import TokenAccountant, TokenBudget, TokenEstimate


def _audit(
    *,
    event_type: str,
    corr_id: str = "ct_one",
    tool_name: str | None = None,
    decision: str | None = None,
    metadata: dict | None = None,
    evidence: list[str] | None = None,
) -> dict:
    return {
        "event_type": event_type,
        "corr_id": corr_id,
        "tool_name": tool_name,
        "decision": decision,
        "metadata": metadata or {},
        "evidence": evidence or [],
    }


class EstimateTests(unittest.TestCase):
    def test_text_estimate_is_stable(self) -> None:
        accountant = TokenAccountant()
        a = accountant.estimate(
            "deploy the staging environment",
            kind="user_prompt",
            event_type="UserPromptSubmit",
            session_id="rs_x",
            corr_id="ct_a",
        )
        b = accountant.estimate(
            "deploy the staging environment",
            kind="user_prompt",
            event_type="UserPromptSubmit",
            session_id="rs_x",
            corr_id="ct_a",
        )
        self.assertEqual(a.input_estimate, b.input_estimate)
        self.assertEqual(a.estimator_name, b.estimator_name)
        self.assertGreater(a.input_estimate, 0)

    def test_user_prompt_kind_goes_to_input_side(self) -> None:
        estimate = TokenAccountant().estimate(
            "hello", kind="user_prompt",
            event_type="UserPromptSubmit", session_id="rs_x", corr_id="ct_a",
        )
        self.assertGreater(estimate.input_estimate, 0)
        self.assertEqual(estimate.output_estimate, 0)

    def test_tool_output_kind_goes_to_output_side(self) -> None:
        estimate = TokenAccountant().estimate(
            "hello world", kind="tool_output",
            event_type="PostToolUse", session_id="rs_x", corr_id="ct_a",
        )
        self.assertEqual(estimate.input_estimate, 0)
        self.assertGreater(estimate.output_estimate, 0)

    def test_empty_text_is_zero(self) -> None:
        estimate = TokenAccountant().estimate(
            "", kind="user_prompt",
            event_type="UserPromptSubmit", session_id="rs_x", corr_id="ct_a",
        )
        self.assertEqual(estimate.input_estimate, 0)
        self.assertEqual(estimate.output_estimate, 0)
        self.assertEqual(estimate.estimator_name, "none")

    def test_to_dict_is_json_friendly(self) -> None:
        import json

        estimate = TokenAccountant().estimate(
            "hi", kind="user_prompt",
            event_type="UserPromptSubmit", session_id="rs_x", corr_id="ct_a",
        )
        json.dumps(estimate.to_dict())  # must not raise


class RollupTests(unittest.TestCase):
    def test_aggregates_by_event_type(self) -> None:
        records = [
            _audit(
                event_type="UserPromptSubmit",
                corr_id="ct_one",
                metadata={"input_estimate": 50, "output_estimate": 0},
            ),
            _audit(
                event_type="PreToolUse",
                corr_id="ct_one",
                tool_name="Bash",
                metadata={"input_estimate": 20, "output_estimate": 0},
            ),
            _audit(
                event_type="PostToolUse",
                corr_id="ct_one",
                tool_name="Bash",
                metadata={"input_estimate": 0, "output_estimate": 200},
            ),
            _audit(
                event_type="Stop",
                corr_id="ct_one",
                metadata={"input_estimate": 0, "output_estimate": 80},
            ),
        ]
        rollup = TokenAccountant().rollup(records)
        self.assertEqual(rollup.total_estimated_input, 70)
        self.assertEqual(rollup.total_estimated_output, 280)
        self.assertEqual(rollup.total_estimated, 350)
        self.assertEqual(rollup.by_event_type["UserPromptSubmit"], 50)
        self.assertEqual(rollup.by_event_type["PreToolUse"], 20)
        self.assertEqual(rollup.by_event_type["PostToolUse"], 200)
        self.assertEqual(rollup.by_event_type["Stop"], 80)

    def test_aggregates_by_corr_id(self) -> None:
        records = [
            _audit(
                event_type="PreToolUse",
                corr_id="ct_a",
                metadata={"input_estimate": 10, "output_estimate": 0},
            ),
            _audit(
                event_type="PreToolUse",
                corr_id="ct_b",
                metadata={"input_estimate": 30, "output_estimate": 0},
            ),
        ]
        rollup = TokenAccountant().rollup(records)
        self.assertEqual(rollup.by_corr_id["ct_a"], 10)
        self.assertEqual(rollup.by_corr_id["ct_b"], 30)

    def test_aggregates_by_memory_backend(self) -> None:
        records = [
            _audit(
                event_type="PreToolUse",
                tool_name="mcp__honcho__search",
                metadata={"input_estimate": 5, "output_estimate": 0},
            ),
            _audit(
                event_type="PreToolUse",
                tool_name="mcp__mempalace__store",
                metadata={"input_estimate": 9, "output_estimate": 0},
            ),
            _audit(
                event_type="PreToolUse",
                tool_name="mcp__github__create_issue",
                metadata={"input_estimate": 11, "output_estimate": 0},
            ),
        ]
        rollup = TokenAccountant().rollup(records)
        self.assertEqual(rollup.by_memory_backend["honcho"], 5)
        self.assertEqual(rollup.by_memory_backend["mempalace"], 9)
        self.assertNotIn("github", rollup.by_memory_backend)

    def test_aggregates_by_tool(self) -> None:
        records = [
            _audit(
                event_type="PreToolUse",
                tool_name="Bash",
                metadata={"input_estimate": 4, "output_estimate": 0},
            ),
            _audit(
                event_type="PreToolUse",
                tool_name="Bash",
                metadata={"input_estimate": 6, "output_estimate": 0},
            ),
            _audit(
                event_type="PreToolUse",
                tool_name="Read",
                metadata={"input_estimate": 7, "output_estimate": 0},
            ),
        ]
        rollup = TokenAccountant().rollup(records)
        self.assertEqual(rollup.by_tool["Bash"], 10)
        self.assertEqual(rollup.by_tool["Read"], 7)

    def test_overhead_estimate_includes_evidence(self) -> None:
        records = [
            _audit(
                event_type="PreToolUse",
                metadata={"input_estimate": 0, "output_estimate": 0},
                evidence=["destructive action; sensitive_or_destructive_action"],
            ),
        ]
        rollup = TokenAccountant().rollup(records)
        self.assertGreater(rollup.rulence_overhead_estimate, 0)

    def test_post_tool_use_falls_back_to_original_token_estimate(self) -> None:
        records = [
            _audit(
                event_type="PostToolUse",
                tool_name="Bash",
                metadata={"original_token_estimate": 123},
            ),
        ]
        rollup = TokenAccountant().rollup(records)
        self.assertEqual(rollup.total_estimated_output, 123)


class BudgetTests(unittest.TestCase):
    def test_user_prompt_over_budget_warns(self) -> None:
        records = [
            _audit(
                event_type="UserPromptSubmit",
                metadata={"input_estimate": 5000, "output_estimate": 0},
            ),
        ]
        budget = TokenBudget(max_user_prompt_tokens=1000)
        warnings = TokenAccountant().check_budget(records, budget)
        self.assertEqual(len(warnings), 1)
        self.assertIn("max_user_prompt_tokens", warnings[0])

    def test_session_total_over_budget_warns(self) -> None:
        records = [
            _audit(
                event_type="PostToolUse",
                metadata={"input_estimate": 0, "output_estimate": 50000},
            ),
        ]
        budget = TokenBudget(max_session_estimated_tokens=10000)
        warnings = TokenAccountant().check_budget(records, budget)
        self.assertTrue(any("max_session_estimated_tokens" in w for w in warnings))

    def test_memory_result_over_budget_warns(self) -> None:
        records = [
            _audit(
                event_type="PreToolUse",
                tool_name="mcp__honcho__search",
                metadata={"input_estimate": 8000, "output_estimate": 0},
            ),
        ]
        budget = TokenBudget(max_memory_result_tokens=2000)
        warnings = TokenAccountant().check_budget(records, budget)
        self.assertTrue(any("memory backend honcho" in w for w in warnings))

    def test_no_warnings_when_within_budget(self) -> None:
        records = [
            _audit(
                event_type="UserPromptSubmit",
                metadata={"input_estimate": 50, "output_estimate": 0},
            ),
        ]
        budget = TokenBudget(max_user_prompt_tokens=1000)
        warnings = TokenAccountant().check_budget(records, budget)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
