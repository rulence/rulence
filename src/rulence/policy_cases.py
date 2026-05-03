from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .preflight import preflight_task


def run_policy_cases(path: str | Path, policy_dir: str | None = None) -> list[dict[str, Any]]:
    case_path = Path(path).expanduser()
    data = tomllib.loads(case_path.read_text(encoding="utf-8"))
    cases = data.get("case", [])
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        name = str(case.get("name", f"case-{index}"))
        task = str(case.get("task", ""))
        memory = str(case.get("memory", ""))
        expected_tier = case.get("expected_tier")
        expected_verdict = case.get("expected_verdict")
        preflight = preflight_task(task, memory=memory, policy_dir=policy_dir)
        failures: list[str] = []
        if expected_tier is not None and preflight.classification.tier != int(expected_tier):
            failures.append(f"expected tier {expected_tier}, got {preflight.classification.tier}")
        if expected_verdict is not None and preflight.verdict != str(expected_verdict):
            failures.append(f"expected verdict {expected_verdict}, got {preflight.verdict}")
        results.append(
            {
                "name": name,
                "task": task,
                "pass": not failures,
                "failures": failures,
                "actual_tier": preflight.classification.tier,
                "actual_verdict": preflight.verdict,
            }
        )
    return results
