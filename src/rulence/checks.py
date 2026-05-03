from __future__ import annotations

import re
from collections.abc import Callable

from .models import CheckResult, Classification, Policy, TaskDAG
from .logic import evaluate_policy_constraints, find_constraint_conflicts
from .policies import (
    CHECK_APPROVAL,
    CHECK_BUDGET,
    CHECK_CONSISTENCY,
    CHECK_CONSTRAINT,
    CHECK_COUNTEREXAMPLE,
    CHECK_DECOMPOSITION,
    CHECK_MEMORY,
    CHECK_TOOL,
    KNOWN_CHECKS,
)
from .token_budget import build_token_budget

CONTRADICTION_PATTERNS = (
    ("always", "never"),
    ("must", "must not"),
    ("required", "forbidden"),
    ("local only", "cloud"),
    ("free", "paid"),
    ("delete", "keep"),
    ("safe", "unsafe"),
)

CheckHandler = Callable[[str, str, Classification, Policy, str | None], CheckResult]
CUSTOM_CHECKS: dict[str, CheckHandler] = {}


def register_check(name: str, handler: CheckHandler) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("custom check names must be lowercase snake_case")
    CUSTOM_CHECKS[name] = handler
    KNOWN_CHECKS.add(name)

def run_check(
    name: str,
    task: str,
    memory: str,
    classification: Classification,
    policy: Policy,
    model: str | None = None,
    decomposition: TaskDAG | None = None,
) -> CheckResult:
    if name in CUSTOM_CHECKS:
        return CUSTOM_CHECKS[name](task, memory, classification, policy, model)

    if name == CHECK_MEMORY:
        if memory.strip():
            return CheckResult(name, "pass", "memory supplied for local preflight", (_preview(memory),))
        return CheckResult(name, "warn", "no memory supplied; agent may rediscover context")

    if name == CHECK_CONSISTENCY:
        contradictions = find_contradictions(f"{task}\n{memory}")
        if contradictions:
            return CheckResult(name, "warn", "possible contradiction pattern found; inspect before acting", tuple(contradictions))
        return CheckResult(name, "pass", "no simple contradiction pattern found")

    if name == CHECK_BUDGET:
        budget = build_token_budget(task, memory, policy.required_checks, classification.tier, model=model)
        if budget.governed_context_estimate > 12000:
            return CheckResult(name, "warn", "estimated governed context is high")
        return CheckResult(
            name,
            "pass",
            f"estimated {budget.governed_context_estimate} governed context tokens",
        )

    if name == CHECK_TOOL:
        signals = classification.signals
        if signals.get("destructive_terms") and signals.get("high_risk_terms"):
            return CheckResult(name, "block", "destructive action intersects with sensitive/high-impact terms")
        if signals.get("external_action_terms"):
            return CheckResult(name, "warn", "external communication or upload may require user approval")
        if signals.get("tool_terms"):
            return CheckResult(name, "pass", "tool use detected and routed through policy")
        return CheckResult(name, "pass", "no risky tool-use signal detected")

    if name == CHECK_COUNTEREXAMPLE:
        examples = counterexamples(task, classification)
        if examples:
            return CheckResult(name, "warn", "possible failure mode found", tuple(examples))
        return CheckResult(name, "pass", "no obvious counterexample found")

    if name == CHECK_CONSTRAINT:
        conflicts = find_constraint_conflicts(f"{task}\n{memory}")
        if decomposition:
            derived = "\n".join(
                f"{constraint.kind}({constraint.condition}, {constraint.target})"
                for constraint in decomposition.all_constraints
            )
            conflicts = tuple([*conflicts, *find_constraint_conflicts(derived)])
        policy_warnings, policy_blocks = evaluate_policy_constraints(policy.constraints, task, memory)
        evidence = tuple([*conflicts, *policy_blocks])
        if evidence:
            return CheckResult(name, "block", "constraint conflict or policy violation", evidence)
        if policy_warnings:
            return CheckResult(name, "warn", "; ".join(policy_warnings), tuple(policy_warnings))
        return CheckResult(name, "pass", "no conflicts; policy constraints satisfied")

    if name == CHECK_DECOMPOSITION:
        if not decomposition:
            return CheckResult(name, "pass", "prompt below decomposition threshold")
        high_risk = [unit for unit in decomposition.flat if unit.tier >= 5]
        if high_risk:
            return CheckResult(
                name,
                "warn",
                "decomposition contains high-risk unit",
                tuple(unit.instruction for unit in high_risk[:5]),
            )
        return CheckResult(
            name,
            "pass",
            f"decomposed into {len(decomposition.flat)} governable unit(s)",
        )

    if name == CHECK_APPROVAL:
        return CheckResult(name, "block", "high-risk tier requires explicit user approval")

    return CheckResult(name, "warn", f"unknown check '{name}'")


def find_contradictions(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.lower())
    found: list[str] = []
    for left, right in CONTRADICTION_PATTERNS:
        if left in normalized and right in normalized:
            found.append(f"contains both '{left}' and '{right}'")
    return found


def counterexamples(task: str, classification: Classification) -> list[str]:
    signals = classification.signals
    examples: list[str] = []
    if signals.get("complex_terms") and not signals.get("tool_terms"):
        examples.append("task may need tools, but no tool path is explicit")
    if "migration" in signals.get("complex_terms", []) or "migrate" in signals.get("complex_terms", []):
        examples.append("migration may fail if rollback, backup, or freeze window is missing")
    if signals.get("ambiguity_terms"):
        examples.append("ambiguous wording may cause the agent to choose the wrong scope")
    return examples


def _preview(text: str, limit: int = 180) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit - 3]}..."
