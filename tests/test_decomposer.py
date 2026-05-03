from __future__ import annotations

import json
import tempfile
import unittest

from rulence.decomposer import decompose_prompt
from rulence.mcp_server import handle_request
from rulence.preflight import preflight_task
from rulence.reasoning import start_session
from rulence.trace_html import render_trace_html


class DecomposerTests(unittest.TestCase):
    def test_decompose_simple_three_task_prompt(self) -> None:
        dag = decompose_prompt("Build A. Build B. Build C.")

        self.assertEqual(len(dag.root_units), 3)
        self.assertTrue(all(not unit.depends_on for unit in dag.root_units))
        self.assertEqual(dag.all_constraints, ())

    def test_decompose_detects_sequential_dependency(self) -> None:
        dag = decompose_prompt("First build the API. Then build the UI.")

        self.assertEqual(len(dag.root_units), 2)
        self.assertEqual(dag.root_units[1].depends_on, (dag.root_units[0].id,))

    def test_decompose_extracts_natural_constraint_must(self) -> None:
        dag = decompose_prompt("Auth must use JWT.")

        constraint = dag.root_units[0].constraints[0]
        self.assertEqual(constraint.kind, "requires")
        self.assertEqual(constraint.condition.lower(), "auth")
        self.assertEqual(constraint.target.lower(), "jwt")

    def test_decompose_extracts_natural_constraint_dont(self) -> None:
        dag = decompose_prompt("Don't log credentials.")

        constraint = dag.root_units[0].constraints[0]
        self.assertEqual(constraint.kind, "forbids")
        self.assertEqual(constraint.condition.lower(), "log")
        self.assertEqual(constraint.target.lower(), "credentials")

    def test_decompose_handles_markdown_headers(self) -> None:
        dag = decompose_prompt("# Frontend\nBuild UI.\n# Backend\nBuild API.")

        self.assertEqual([unit.instruction for unit in dag.root_units], ["Build UI", "Build API"])
        self.assertEqual(dag.root_units[1].depends_on, ())

    def test_decompose_handles_numbered_list(self) -> None:
        dag = decompose_prompt("1. Set up DB.\n2. Write migrations.\n3. Deploy.")

        self.assertEqual(len(dag.root_units), 3)
        self.assertEqual(dag.root_units[1].depends_on, (dag.root_units[0].id,))
        self.assertEqual(dag.root_units[2].depends_on, (dag.root_units[1].id,))

    def test_decompose_recurses_on_complex_unit(self) -> None:
        dag = decompose_prompt(
            "Plan the database migration: verify backups, write rollback instructions, "
            "run dry-run validation, update monitoring, prepare release notes, notify owners, "
            "stage deployment, verify metrics, confirm stakeholder approval, schedule communication, "
            "verify database locks, and document the audit trail.",
            max_depth=3,
        )

        self.assertTrue(any(unit.children for unit in dag.root_units))

    def test_decompose_respects_max_depth(self) -> None:
        dag = decompose_prompt(
            "Plan the database migration: verify backups, write rollback instructions, "
            "run dry-run validation, update monitoring, prepare release notes, notify owners, "
            "stage deployment, verify metrics, confirm stakeholder approval, schedule communication, "
            "verify database locks, and document the audit trail.",
            max_depth=1,
        )

        self.assertTrue(all(not unit.children for unit in dag.root_units))

    def test_decompose_preserves_code_blocks(self) -> None:
        dag = decompose_prompt("Write this function.\n```python\nprint('x')\n```")

        self.assertIn("```python", dag.root_units[0].raw_text)
        self.assertIn("print('x')", dag.root_units[0].raw_text)

    def test_decompose_cross_cutting_applies_to_all(self) -> None:
        dag = decompose_prompt("Build API. Build UI. Write docs. Test everything thoroughly.")

        self.assertEqual(dag.root_units[-1].applies_to, tuple(unit.id for unit in dag.root_units[:-1]))
        self.assertEqual(dag.root_units[-1].depends_on, ())

    def test_preflight_auto_decomposes_when_over_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with open(f"{directory}/tier-4-low-threshold.toml", "w", encoding="utf-8") as handle:
                handle.write(
                    'tier = 4\nlabel = "low-threshold"\n'
                    'required_checks = ["decomposition_check"]\nwarn_if = []\nblock_if = []\n'
                    "decompose_threshold = 3\ndecompose_max_depth = 2\n"
                )
            result = preflight_task(
                "plan database migration with rollback backup dry-run deployment monitoring",
                policy_dir=directory,
            )

        self.assertIsNotNone(result.decomposition)
        self.assertTrue(any(check.name == "decomposition_check" for check in result.checks))

    def test_preflight_short_prompt_no_decomposition(self) -> None:
        result = preflight_task("say hello")

        self.assertIsNone(result.decomposition)

    def test_decomposer_constraints_flow_into_constraint_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with open(f"{directory}/tier-1-constraints.toml", "w", encoding="utf-8") as handle:
                handle.write(
                    'tier = 1\nlabel = "constraints"\n'
                    'required_checks = ["constraint_solver"]\nwarn_if = []\nblock_if = []\n'
                    "decompose_threshold = 1\ndecompose_max_depth = 2\n"
                )
            result = preflight_task("Auth must use JWT. Never auth with JWT.", policy_dir=directory)

        self.assertEqual(result.verdict, "block")
        self.assertTrue(any(check.name == "constraint_solver" and check.status == "block" for check in result.checks))

    def test_mcp_exposes_decompose_tool(self) -> None:
        response = handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "rulence_decompose",
                    "arguments": {"prompt": "Build API. Then build UI."},
                },
            }
        )

        self.assertIsNotNone(response)
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(len(payload["root_units"]), 2)

    def test_trace_html_renders_decomposition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with open(f"{directory}/tier-4-low-threshold.toml", "w", encoding="utf-8") as handle:
                handle.write(
                    'tier = 4\nlabel = "low-threshold"\n'
                    'required_checks = ["decomposition_check"]\nwarn_if = []\nblock_if = []\n'
                    "decompose_threshold = 3\ndecompose_max_depth = 2\n"
                )
            session = start_session(
                "plan database migration with rollback backup dry-run deployment monitoring",
                policy_dir=directory,
            )
            output = render_trace_html(session)

        self.assertIn("Decomposition", output)
        self.assertIn("root units", output.lower())


if __name__ == "__main__":
    unittest.main()
