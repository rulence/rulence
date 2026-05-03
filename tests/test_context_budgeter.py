from __future__ import annotations

import unittest

from rulence.context import (
    ContextBudgeter,
    ContextFragment,
    SourceRef,
)


def _fragment(
    *,
    kind: str = "tool_result",
    text: str = "x" * 40,
    fragment_id: str | None = None,
    metadata: dict | None = None,
    token_estimate: int | None = None,
) -> ContextFragment:
    source = SourceRef(source_type="user_prompt", runner="claude_code")
    return ContextFragment.build(
        kind=kind,
        text=text,
        source=source,
        fragment_id=fragment_id,
        metadata=metadata or {},
        token_estimate=token_estimate,
    )


class ContextBudgeterTests(unittest.TestCase):
    def test_under_budget_keeps_all_fragments(self) -> None:
        b = ContextBudgeter()
        f1 = _fragment(token_estimate=10, fragment_id="f1")
        f2 = _fragment(token_estimate=15, fragment_id="f2")
        result = b.fit([f1, f2], max_tokens=100)
        self.assertEqual(
            [f.fragment_id for f in result.included_fragments], ["f1", "f2"]
        )
        self.assertEqual(result.dropped_fragments, ())
        self.assertFalse(result.over_budget)
        self.assertTrue(result.required_fragments_preserved)
        self.assertEqual(result.token_estimate_after, 25)
        self.assertEqual(result.token_estimate_before, 25)

    def test_over_budget_drops_optional_with_reason(self) -> None:
        b = ContextBudgeter()
        # Two large optional fragments and one required (user_instruction).
        instr = _fragment(
            kind="user_instruction",
            text="Do the thing",
            token_estimate=10,
            fragment_id="instr",
        )
        big1 = _fragment(token_estimate=80, fragment_id="big1")
        big2 = _fragment(token_estimate=80, fragment_id="big2")
        result = b.fit([instr, big1, big2], max_tokens=100)
        included_ids = [f.fragment_id for f in result.included_fragments]
        self.assertIn("instr", included_ids)
        # Only one of the big optionals can fit alongside instr (10+80=90 <= 100)
        self.assertEqual(len(included_ids), 2)
        self.assertEqual(len(result.dropped_fragments), 1)
        for dropped in result.dropped_fragments:
            self.assertIn(dropped.fragment_id, result.drop_reasons)
            self.assertTrue(
                result.drop_reasons[dropped.fragment_id].startswith("budget_full")
            )

    def test_active_forbids_constraint_survives_budgeting(self) -> None:
        b = ContextBudgeter()
        forbid = _fragment(
            kind="constraint",
            text="forbids(secrets, logs)",
            metadata={"constraint_kind": "forbids"},
            token_estimate=20,
            fragment_id="forbid",
        )
        # Many big optional fragments together blow past the budget.
        bigs = [
            _fragment(token_estimate=200, fragment_id=f"big{i}") for i in range(3)
        ]
        result = b.fit([forbid, *bigs], max_tokens=50)
        included_ids = [f.fragment_id for f in result.included_fragments]
        self.assertIn("forbid", included_ids)
        self.assertTrue(result.required_fragments_preserved)

    def test_required_only_set_can_exceed_budget(self) -> None:
        b = ContextBudgeter()
        forbid = _fragment(
            kind="constraint",
            text="forbids(secrets, logs)",
            metadata={"constraint_kind": "forbids"},
            token_estimate=200,
            fragment_id="forbid",
        )
        result = b.fit([forbid], max_tokens=50)
        self.assertTrue(result.over_budget)
        self.assertTrue(result.required_fragments_preserved)
        self.assertEqual(
            [f.fragment_id for f in result.included_fragments], ["forbid"]
        )

    def test_explicit_required_ids_are_preserved(self) -> None:
        b = ContextBudgeter()
        f1 = _fragment(
            kind="tool_result", text="a", token_estimate=80, fragment_id="f1"
        )
        f2 = _fragment(
            kind="tool_result", text="b", token_estimate=80, fragment_id="f2"
        )
        result = b.fit([f1, f2], max_tokens=100, required_ids=["f2"])
        ids = [f.fragment_id for f in result.included_fragments]
        self.assertIn("f2", ids)
        # f1 should be dropped because f2 (required) takes most of the budget.
        self.assertNotIn("f1", ids)

    def test_blocker_metadata_is_required(self) -> None:
        b = ContextBudgeter()
        blocker = _fragment(
            kind="audit_observation",
            text="blocker text",
            metadata={"blocker": True},
            token_estimate=200,
            fragment_id="b",
        )
        result = b.fit([blocker], max_tokens=10)
        self.assertIn("b", [f.fragment_id for f in result.included_fragments])
        self.assertTrue(result.over_budget)

    def test_drop_reasons_are_recorded(self) -> None:
        b = ContextBudgeter()
        f1 = _fragment(token_estimate=120, fragment_id="f1")
        result = b.fit([f1], max_tokens=50)
        # f1 is optional and exceeds the budget alone.
        self.assertEqual([f.fragment_id for f in result.dropped_fragments], ["f1"])
        self.assertIn("budget_full", result.drop_reasons["f1"])


if __name__ == "__main__":
    unittest.main()
