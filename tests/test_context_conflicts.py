from __future__ import annotations

import unittest

from rulence.context import (
    Blocker,
    ContextFragment,
    Decision,
    SourceRef,
    TaskState,
    detect_conflicts,
)
from rulence.logic import Constraint


def _fragment(
    *,
    kind: str,
    text: str,
    fragment_id: str | None = None,
    memory_backend: str | None = None,
    metadata: dict | None = None,
) -> ContextFragment:
    source = SourceRef(
        source_type="memory_item" if memory_backend else "user_prompt",
        runner="claude_code",
        memory_backend=memory_backend,
    )
    return ContextFragment.build(
        kind=kind,
        text=text,
        source=source,
        memory_backend=memory_backend,
        fragment_id=fragment_id,
        metadata=metadata or {},
    )


class ConflictDetectionTests(unittest.TestCase):
    def test_completion_with_open_blocker_is_flagged(self) -> None:
        state = TaskState.new(
            user_goal="ship audit logging",
            blockers=(Blocker(text="security review pending"),),
        )
        report = detect_conflicts(
            task_state=state,
            final_response="All set, this is done and ready to ship.",
        )
        kinds = [c.kind for c in report.conflicts]
        self.assertIn("completion_with_open_blocker", kinds)
        completion = next(
            c for c in report.conflicts if c.kind == "completion_with_open_blocker"
        )
        self.assertEqual(completion.severity, "block")

    def test_resolved_blocker_does_not_trigger_completion_conflict(self) -> None:
        state = TaskState.new(
            user_goal="ship audit logging",
            blockers=(Blocker(text="security review pending", resolved=True),),
        )
        report = detect_conflicts(
            task_state=state, final_response="Done shipping."
        )
        kinds = [c.kind for c in report.conflicts]
        self.assertNotIn("completion_with_open_blocker", kinds)

    def test_npm_vs_bun_decisions_conflict(self) -> None:
        a = _fragment(kind="decision", text="use npm for installs", fragment_id="a")
        b = _fragment(kind="decision", text="use bun for installs", fragment_id="b")
        report = detect_conflicts(fragments=(a, b))
        kinds = [c.kind for c in report.conflicts]
        self.assertIn("tool_choice_conflict", kinds)

    def test_decision_npm_with_state_decision_bun_conflicts(self) -> None:
        state = TaskState.new(
            user_goal="install deps",
            decisions=(Decision(text="standardize on bun for tests"),),
        )
        a = _fragment(
            kind="tool_input", text="bash: npm install", fragment_id="frag_a"
        )
        report = detect_conflicts(task_state=state, fragments=(a,))
        kinds = [c.kind for c in report.conflicts]
        self.assertIn("tool_choice_conflict", kinds)

    def test_honcho_and_mempalace_disagree_on_tool_choice(self) -> None:
        honcho = _fragment(
            kind="honcho_memory",
            text="user prefers npm",
            memory_backend="honcho",
            fragment_id="honcho_1",
        )
        mempalace = _fragment(
            kind="mempalace_memory",
            text="project standardized on bun",
            memory_backend="mempalace",
            fragment_id="mempalace_1",
        )
        report = detect_conflicts(fragments=(honcho, mempalace))
        kinds = [c.kind for c in report.conflicts]
        self.assertIn("internal_vs_external_memory", kinds)

    def test_old_memory_negation_against_new_decision_conflicts(self) -> None:
        state = TaskState.new(
            user_goal="x",
            decisions=(Decision(text="rollback the migration"),),
        )
        memory = _fragment(
            kind="honcho_memory",
            text="don't rollback the migration; it has already shipped",
            memory_backend="honcho",
            fragment_id="m1",
        )
        report = detect_conflicts(task_state=state, fragments=(memory,))
        kinds = [c.kind for c in report.conflicts]
        self.assertIn("memory_vs_decision", kinds)

    def test_final_response_violates_active_forbid(self) -> None:
        state = TaskState.new(
            user_goal="audit logs",
            active_constraints=(Constraint("forbids", "secrets", "logs"),),
        )
        report = detect_conflicts(
            task_state=state,
            final_response="I added secrets to the logs for debugging.",
        )
        kinds = [c.kind for c in report.conflicts]
        self.assertIn("final_vs_constraint", kinds)
        violation = next(
            c for c in report.conflicts if c.kind == "final_vs_constraint"
        )
        self.assertEqual(violation.severity, "block")

    def test_no_conflict_for_clean_state(self) -> None:
        state = TaskState.new(user_goal="clean state")
        report = detect_conflicts(task_state=state, final_response="all good")
        self.assertFalse(report.has_conflicts)

    def test_constraint_fragment_drives_final_constraint_check(self) -> None:
        constraint = _fragment(
            kind="constraint",
            text="forbids(secrets, logs)",
            fragment_id="c1",
            metadata={
                "constraint_kind": "forbids",
                "constraint_condition": "secrets",
                "constraint_target": "logs",
            },
        )
        report = detect_conflicts(
            fragments=(constraint,),
            final_response="I added secrets to the logs.",
        )
        kinds = [c.kind for c in report.conflicts]
        self.assertIn("final_vs_constraint", kinds)


if __name__ == "__main__":
    unittest.main()
