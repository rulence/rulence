from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rulence.evals import Eval, EvalRunner, discover_evals
from rulence.evals.runner import EvalReport


def _write_eval(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(body, encoding="utf-8")


class EvalRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _seed_evals(self) -> None:
        _write_eval(
            self.root / "governance",
            "ok_case",
            """
from rulence.evals import Eval


def run():
    return {"status": "pass", "metrics": {"latency_ms": 1.5}}


EVAL = Eval(
    eval_id="governance.ok_case",
    category="governance",
    description="Trivial passing case.",
    claim_ids=("synthetic_supported",),
    runner=run,
)
""".strip(),
        )
        _write_eval(
            self.root / "governance",
            "fail_case",
            """
from rulence.evals import Eval


def run():
    raise AssertionError("intentional failure for tests")


EVAL = Eval(
    eval_id="governance.fail_case",
    category="governance",
    description="A case that fails on purpose.",
    claim_ids=("synthetic_supported",),
    runner=run,
)
""".strip(),
        )
        _write_eval(
            self.root / "memory",
            "ok_case",
            """
from rulence.evals import Eval


def run():
    return {"status": "pass", "metrics": {"items_returned": 3}}


EVAL = Eval(
    eval_id="memory.ok_case",
    category="memory",
    description="Trivial memory case.",
    claim_ids=("synthetic_memory",),
    runner=run,
)
""".strip(),
        )

    def test_discover_finds_evals_in_categories(self) -> None:
        self._seed_evals()
        evals = discover_evals(self.root)
        ids = sorted(e.eval_id for e in evals)
        self.assertEqual(
            ids,
            [
                "governance.fail_case",
                "governance.ok_case",
                "memory.ok_case",
            ],
        )

    def test_runner_reports_pass_and_fail(self) -> None:
        self._seed_evals()
        runner = EvalRunner(self.root)
        report = runner.run()
        self.assertEqual(report.summary["pass"], 2)
        self.assertEqual(report.summary["fail"], 1)
        self.assertEqual(report.summary["total"], 3)
        self.assertFalse(report.passed)

    def test_runner_filters_by_category(self) -> None:
        self._seed_evals()
        runner = EvalRunner(self.root)
        report = runner.run(categories=["memory"])
        self.assertEqual(report.summary["total"], 1)
        self.assertEqual(report.results[0].eval_id, "memory.ok_case")

    def test_runner_filters_by_claim_id(self) -> None:
        self._seed_evals()
        runner = EvalRunner(self.root)
        report = runner.run(claim_ids=["synthetic_memory"])
        self.assertEqual(report.summary["total"], 1)
        self.assertEqual(report.results[0].claim_ids, ("synthetic_memory",))

    def test_report_to_json_round_trip(self) -> None:
        self._seed_evals()
        runner = EvalRunner(self.root)
        report = runner.run()
        encoded = json.dumps(report.to_dict())
        decoded = json.loads(encoded)
        self.assertEqual(decoded["summary"], report.summary)
        self.assertEqual(len(decoded["results"]), 3)

    def test_report_to_markdown_includes_table(self) -> None:
        self._seed_evals()
        runner = EvalRunner(self.root)
        report = runner.run()
        markdown = report.to_markdown()
        self.assertIn("# Rulence Eval Report", markdown)
        self.assertIn("| eval_id | category | status |", markdown)
        self.assertIn("governance.ok_case", markdown)

    def test_runner_handles_empty_root(self) -> None:
        runner = EvalRunner(self.root)
        report = runner.run()
        self.assertEqual(report.summary["total"], 0)
        self.assertTrue(report.passed)

    def test_real_repo_evals_discoverable_and_pass(self) -> None:
        # The repo ships eval cases under <repo>/evals. Discovery should
        # find them without env overrides, and they should all pass on a
        # clean tree.
        report = EvalRunner().run()
        self.assertGreater(report.summary["total"], 0)
        # If something here ever flips to fail, the report's `detail`
        # field tells us which case regressed.
        failures = [r for r in report.results if r.status != "pass"]
        self.assertEqual(
            failures, [], f"unexpected failures: {[r.to_dict() for r in failures]}"
        )

    def test_aggregate_metrics_include_pass_rate(self) -> None:
        self._seed_evals()
        runner = EvalRunner(self.root)
        report = runner.run()
        self.assertIn("pass_rate", report.metrics)
        self.assertGreater(report.metrics["pass_rate"], 0.0)
        self.assertLess(report.metrics["pass_rate"], 1.0)


class EvalCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _write_eval(
            self.root / "context",
            "ok_case",
            """
from rulence.evals import Eval


def run():
    return {"status": "pass", "metrics": {}}


EVAL = Eval(
    eval_id="context.ok_case",
    category="context",
    description="Trivial passing context case.",
    claim_ids=("synthetic_context",),
    runner=run,
)
""".strip(),
        )

    def test_cli_eval_run_writes_json_report(self) -> None:
        import io
        from contextlib import redirect_stdout

        from rulence.cli import main as cli_main

        out_path = self.root / "report.json"
        markdown_path = self.root / "report.md"

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli_main(
                [
                    "eval",
                    "run",
                    "--root",
                    str(self.root),
                    "--out",
                    str(out_path),
                    "--markdown",
                    str(markdown_path),
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(out_path.exists())
        data = json.loads(out_path.read_text("utf-8"))
        self.assertEqual(data["summary"]["pass"], 1)
        self.assertTrue(markdown_path.exists())
        self.assertIn("Rulence Eval Report", markdown_path.read_text("utf-8"))

    def test_cli_eval_report_renders_markdown(self) -> None:
        import io
        from contextlib import redirect_stdout

        from rulence.cli import main as cli_main

        out_path = self.root / "report.json"

        with redirect_stdout(io.StringIO()):
            cli_main(
                [
                    "eval",
                    "run",
                    "--root",
                    str(self.root),
                    "--out",
                    str(out_path),
                    "--json",
                ]
            )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli_main([
                "eval", "report", str(out_path), "--markdown",
            ])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("Rulence Eval Report", output)
        self.assertIn("context.ok_case", output)


if __name__ == "__main__":
    unittest.main()
