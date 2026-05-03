"""Discover and run Rulence eval cases.

Each eval case is a Python module that exports two top-level names:

* ``EVAL`` — an :class:`Eval` describing the case.
* ``run`` (or any callable bound on ``EVAL.runner``) — a zero-argument
  function that performs the assertion and returns either ``None`` or a
  ``dict`` of additional metrics.

The runner walks ``evals/<category>/*.py`` (anything starting with ``_``
is skipped), imports each module under a synthetic package name so the
top-level ``evals`` directory does not need to ship as a package, calls
the runner, and aggregates results into a single :class:`EvalReport`.

Eval failures do not raise to the caller. They are captured as
``status="fail"`` results so a CI run produces a complete report even
when an individual case regresses.
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

DEFAULT_EVALS_ROOT = "evals"

VALID_STATUSES: tuple[str, ...] = ("pass", "fail", "skip", "error")


@dataclass(frozen=True)
class Eval:
    eval_id: str
    category: str
    description: str
    runner: Callable[[], dict[str, Any] | None]
    claim_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_id": self.eval_id,
            "category": self.category,
            "description": self.description,
            "claim_ids": list(self.claim_ids),
        }


@dataclass(frozen=True)
class EvalResult:
    eval_id: str
    category: str
    claim_ids: tuple[str, ...]
    status: str
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_id": self.eval_id,
            "category": self.category,
            "claim_ids": list(self.claim_ids),
            "status": self.status,
            "detail": self.detail,
            "metrics": dict(self.metrics),
            "duration_ms": round(self.duration_ms, 4),
            "error": self.error,
        }


@dataclass(frozen=True)
class EvalReport:
    started_at: str
    finished_at: str
    results: tuple[EvalResult, ...]
    summary: dict[str, int]
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": dict(self.summary),
            "metrics": dict(self.metrics),
            "results": [r.to_dict() for r in self.results],
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# Rulence Eval Report")
        lines.append("")
        lines.append(f"- Started: {self.started_at}")
        lines.append(f"- Finished: {self.finished_at}")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        for status in ("pass", "fail", "skip", "error"):
            count = self.summary.get(status, 0)
            lines.append(f"- {status}: {count}")
        if self.metrics:
            lines.append("")
            lines.append("## Aggregate metrics")
            lines.append("")
            for key in sorted(self.metrics):
                lines.append(f"- {key}: {self.metrics[key]}")
        lines.append("")
        lines.append("## Cases")
        lines.append("")
        lines.append(
            "| eval_id | category | status | duration_ms | claim_ids | detail |"
        )
        lines.append(
            "| --- | --- | --- | --- | --- | --- |"
        )
        for result in self.results:
            claim_text = ", ".join(result.claim_ids) if result.claim_ids else ""
            detail = result.detail.replace("|", "\\|")
            lines.append(
                f"| {result.eval_id} | {result.category} | {result.status} | "
                f"{result.duration_ms:.2f} | {claim_text} | {detail} |"
            )
        return "\n".join(lines) + "\n"

    @property
    def passed(self) -> bool:
        return self.summary.get("fail", 0) == 0 and self.summary.get("error", 0) == 0


def default_evals_root() -> Path:
    """Resolve the default evals directory.

    ``RULENCE_EVALS_DIR`` overrides the default. Otherwise the runner
    walks up from the package root looking for the repo-level
    ``evals/`` directory — the one with the category subdirs. The
    Python package itself lives at ``src/rulence/evals``; that path is
    skipped explicitly so the runner finds the benchmark cases.
    """
    configured = os.environ.get("RULENCE_EVALS_DIR")
    if configured:
        return Path(configured).expanduser()
    here = Path(__file__).resolve()
    package_evals = here.parent
    for parent in here.parents:
        candidate = parent / DEFAULT_EVALS_ROOT
        if not candidate.is_dir():
            continue
        if candidate.resolve() == package_evals:
            continue
        # The repo-level evals dir always contains at least one category
        # subdirectory. The package's own evals folder only contains the
        # runner module.
        if any(child.is_dir() for child in candidate.iterdir()):
            return candidate
    # Fall back to the conventional repo root location.
    return here.parents[3] / DEFAULT_EVALS_ROOT


def discover_evals(root: str | Path | None = None) -> list[Eval]:
    base = Path(root).expanduser() if root else default_evals_root()
    if not base.is_dir():
        return []
    evals: list[Eval] = []
    seen_ids: set[str] = set()
    for category_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        if category_dir.name.startswith((".", "_")):
            continue
        for path in sorted(category_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module = _load_module(path)
            if module is None:
                continue
            eval_obj = getattr(module, "EVAL", None)
            if not isinstance(eval_obj, Eval):
                continue
            if eval_obj.eval_id in seen_ids:
                # Duplicate eval ids are a configuration error; surface
                # by skipping the later one and recording a sentinel.
                continue
            seen_ids.add(eval_obj.eval_id)
            evals.append(eval_obj)
    return evals


class EvalRunner:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root else default_evals_root()

    def run(
        self,
        *,
        evals: Iterable[Eval] | None = None,
        categories: Iterable[str] | None = None,
        claim_ids: Iterable[str] | None = None,
    ) -> EvalReport:
        candidates = list(evals) if evals is not None else discover_evals(self.root)
        if categories:
            wanted = set(categories)
            candidates = [e for e in candidates if e.category in wanted]
        if claim_ids:
            wanted_claims = set(claim_ids)
            candidates = [
                e
                for e in candidates
                if any(claim in wanted_claims for claim in e.claim_ids)
            ]

        started = _now_iso()
        results: list[EvalResult] = []
        for eval_case in candidates:
            results.append(_run_single(eval_case))
        finished = _now_iso()

        summary = {status: 0 for status in VALID_STATUSES}
        for result in results:
            summary[result.status] = summary.get(result.status, 0) + 1
        summary["total"] = len(results)

        return EvalReport(
            started_at=started,
            finished_at=finished,
            results=tuple(results),
            summary=summary,
            metrics=_aggregate_metrics(results),
        )


def run_all(root: str | Path | None = None) -> EvalReport:
    return EvalRunner(root).run()


def _run_single(eval_case: Eval) -> EvalResult:
    start = time.perf_counter()
    metrics: dict[str, Any] = {}
    try:
        outcome = eval_case.runner()
    except AssertionError as exc:
        duration = (time.perf_counter() - start) * 1000.0
        return EvalResult(
            eval_id=eval_case.eval_id,
            category=eval_case.category,
            claim_ids=eval_case.claim_ids,
            status="fail",
            detail=str(exc) or "assertion failed",
            duration_ms=duration,
        )
    except Exception as exc:  # pragma: no cover - defensive
        duration = (time.perf_counter() - start) * 1000.0
        return EvalResult(
            eval_id=eval_case.eval_id,
            category=eval_case.category,
            claim_ids=eval_case.claim_ids,
            status="error",
            detail=str(exc),
            error="\n".join(traceback.format_exc().splitlines()[-3:]),
            duration_ms=duration,
        )
    duration = (time.perf_counter() - start) * 1000.0
    status = "pass"
    detail = ""
    if isinstance(outcome, dict):
        outcome_status = outcome.get("status")
        if outcome_status in VALID_STATUSES:
            status = outcome_status
        if "detail" in outcome:
            detail = str(outcome["detail"])
        outcome_metrics = outcome.get("metrics")
        if isinstance(outcome_metrics, dict):
            metrics = dict(outcome_metrics)
    metrics.setdefault("duration_ms", round(duration, 4))
    return EvalResult(
        eval_id=eval_case.eval_id,
        category=eval_case.category,
        claim_ids=eval_case.claim_ids,
        status=status,
        detail=detail,
        metrics=metrics,
        duration_ms=duration,
    )


def _load_module(path: Path) -> Any:
    name = "rulence_evals_" + path.stem + "_" + str(abs(hash(str(path.resolve()))))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


def _aggregate_metrics(results: list[EvalResult]) -> dict[str, Any]:
    """Combine per-case metrics into report-wide rollups.

    Numeric metrics are summed across cases; named rates come from the
    presence/absence of cases with the matching ``claim_ids`` and
    ``status``. The aggregator never invents data; missing keys stay
    missing.
    """
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    boolean_counts: dict[str, dict[str, int]] = {}
    for result in results:
        for key, value in result.metrics.items():
            if isinstance(value, bool):
                bucket = boolean_counts.setdefault(key, {"true": 0, "false": 0})
                bucket["true" if value else "false"] += 1
            elif isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0.0) + float(value)
                counts[key] = counts.get(key, 0) + 1
    aggregated: dict[str, Any] = {}
    for key, total in totals.items():
        n = counts[key]
        aggregated[f"{key}_sum"] = round(total, 4)
        aggregated[f"{key}_avg"] = round(total / n, 4) if n else 0.0
    for key, bucket in boolean_counts.items():
        aggregated[f"{key}_true"] = bucket["true"]
        aggregated[f"{key}_false"] = bucket["false"]
    aggregated["pass_rate"] = round(
        sum(1 for r in results if r.status == "pass") / max(1, len(results)), 4
    )
    return aggregated


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_report(report: EvalReport, *, indent: int | None = 2) -> str:
    return json.dumps(report.to_dict(), indent=indent, sort_keys=True)
