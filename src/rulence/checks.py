from __future__ import annotations

import re
from collections.abc import Callable

from .models import CheckResult, Classification, Policy, TaskDAG
from .logic import evaluate_policy_constraints, find_constraint_conflicts, parse_constraints
from .policies import (
    CHECK_APPROVAL,
    CHECK_BUDGET,
    CHECK_CONSISTENCY,
    CHECK_CONSTRAINT,
    CHECK_COUNTEREXAMPLE,
    CHECK_DECOMPOSITION,
    CHECK_MEMORY,
    CHECK_TOOL,
    CHECK_TRANSCRIPT_CONTRADICTION,
    CHECK_TRANSCRIPT_DRIFT,
    CHECK_TRANSCRIPT_STALENESS,
    KNOWN_CHECKS,
)
from .token_budget import build_token_budget
from .transcript import TranscriptTurn

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
    transcript_turns: tuple[TranscriptTurn, ...] = (),
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

    if name == CHECK_TRANSCRIPT_DRIFT:
        return _transcript_drift_check(task, transcript_turns)

    if name == CHECK_TRANSCRIPT_CONTRADICTION:
        return _transcript_contradiction_check(task, transcript_turns)

    if name == CHECK_TRANSCRIPT_STALENESS:
        return _transcript_staleness_check(task, transcript_turns)

    return CheckResult(name, "warn", f"unknown check '{name}'")


_KEYWORD_PATTERN = re.compile(r"[a-z][a-z0-9_-]{2,}")
_STOPWORDS = frozenset(
    """
    the and for with that this from your into about have been will should could
    just only also when then than because they them their there here over under
    using able very more much such been being were was are can may might want
    """.split()
)


def _keywords(text: str) -> set[str]:
    return {token for token in _KEYWORD_PATTERN.findall(text.lower()) if token not in _STOPWORDS}


def _transcript_drift_check(task: str, turns: tuple[TranscriptTurn, ...]) -> CheckResult:
    name = CHECK_TRANSCRIPT_DRIFT
    if not turns:
        return CheckResult(name, "pass", "no transcript provided")

    first_user = next((turn for turn in turns if turn.role == "user"), None)
    if first_user is None:
        return CheckResult(name, "pass", "transcript has no user turn to compare against")

    original = _keywords(first_user.content)
    current = _keywords(task)
    if not original or not current:
        return CheckResult(name, "pass", "no keyword signal to compare")

    overlap = original & current
    if overlap:
        return CheckResult(
            name,
            "pass",
            f"current task shares {len(overlap)} keyword(s) with the original goal",
            tuple(sorted(overlap))[:5],
        )

    return CheckResult(
        name,
        "warn",
        "current task shares no keywords with the original user goal; possible drift",
        tuple(sorted(current))[:5],
    )


def _transcript_contradiction_check(task: str, turns: tuple[TranscriptTurn, ...]) -> CheckResult:
    name = CHECK_TRANSCRIPT_CONTRADICTION
    if not turns:
        return CheckResult(name, "pass", "no transcript provided")

    transcript_text = "\n".join(turn.content for turn in turns)
    transcript_constraints = parse_constraints(transcript_text)
    if not transcript_constraints:
        return CheckResult(name, "pass", "no constraints found in transcript")

    task_lower = task.lower()
    violations: list[str] = []
    for constraint in transcript_constraints:
        condition = constraint.condition.lower()
        target = constraint.target.lower()
        if not condition or not target:
            continue
        if condition not in task_lower:
            continue
        if constraint.kind == "forbids" and target in task_lower:
            violations.append(
                f"transcript said forbids({constraint.condition}, {constraint.target}); current task references both"
            )
        elif constraint.kind == "requires" and target not in task_lower:
            violations.append(
                f"transcript said requires({constraint.condition}, {constraint.target}); current task does not mention {constraint.target}"
            )

    if any(v.startswith("transcript said forbids") for v in violations):
        return CheckResult(name, "block", "current task contradicts a transcript constraint", tuple(violations))
    if violations:
        return CheckResult(name, "warn", "transcript constraint not yet satisfied", tuple(violations))
    return CheckResult(name, "pass", f"task is consistent with {len(transcript_constraints)} transcript constraint(s)")


def _transcript_staleness_check(task: str, turns: tuple[TranscriptTurn, ...]) -> CheckResult:
    name = CHECK_TRANSCRIPT_STALENESS
    if not turns:
        return CheckResult(name, "pass", "no transcript provided")

    current = _keywords(task)
    if not current:
        return CheckResult(name, "pass", "no keyword signal in current task")

    stale_indices: list[str] = []
    for turn in turns:
        if not _keywords(turn.content) & current:
            stale_indices.append(f"turn {turn.index} ({turn.role})")

    if not stale_indices:
        return CheckResult(name, "pass", "all transcript turns share keywords with the current task")

    if len(stale_indices) >= len(turns):
        detail = "every transcript turn appears unrelated to the current task"
    else:
        detail = f"{len(stale_indices)} of {len(turns)} transcript turn(s) appear unrelated; consider pruning"
    return CheckResult(name, "warn", detail, tuple(stale_indices[:8]))


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
