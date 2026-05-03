from __future__ import annotations

from .checks import run_check
from .classifier import classify_task
from .decomposer import decompose_prompt
from .models import PreflightResult
from .policies import CHECK_BUDGET, DEFAULT_POLICIES, load_policy
from .token_budget import build_token_budget


def preflight_task(
    task: str,
    memory: str = "",
    policy_dir: str | None = None,
    model: str | None = None,
) -> PreflightResult:
    if not task.strip():
        raise ValueError("task cannot be empty")

    classification = classify_task(task)
    policy = DEFAULT_POLICIES[0] if classification.tier == 0 else load_policy(classification.tier, policy_dir)
    word_count = int(classification.signals.get("word_count", 0))
    decomposition = None
    if word_count > policy.decompose_threshold:
        decomposition = decompose_prompt(task, max_depth=policy.decompose_max_depth)
    checks = tuple(
        run_check(name, task, memory, classification, policy, model=model, decomposition=decomposition)
        for name in policy.required_checks
    )

    warnings = [check.detail for check in checks if check.status == "warn"]
    blocks = [check.detail for check in checks if check.status == "block"]
    signals = classification.signals

    if "memory_missing" in policy.warn_if and not memory.strip():
        warnings.append("memory_missing")
    if "contradictions_found" in policy.warn_if and any(check.name == "consistency_check" and check.status == "warn" for check in checks):
        warnings.append("contradictions_found")
    if "sensitive_or_destructive_action" in policy.block_if and (
        signals.get("high_risk_terms") or signals.get("destructive_terms")
    ):
        blocks.append("sensitive_or_destructive_action")
    if "destructive_with_weak_context" in policy.block_if and signals.get("destructive_terms") and not memory.strip():
        blocks.append("destructive_with_weak_context")

    token_budget = None
    if CHECK_BUDGET in policy.required_checks:
        token_budget = build_token_budget(task, memory, policy.required_checks, classification.tier, model=model)

    verdict = "approve"
    if blocks:
        verdict = "block"
    elif warnings or classification.tier >= 4:
        verdict = "warn"

    next_steps = _next_steps(verdict, classification.tier, warnings, blocks)

    return PreflightResult(
        classification=classification,
        policy=policy,
        verdict=verdict,
        warnings=tuple(dict.fromkeys(warnings)),
        blocks=tuple(dict.fromkeys(blocks)),
        checks=checks,
        token_budget=token_budget,
        decomposition=decomposition,
        next_steps=next_steps,
    )


def _next_steps(verdict: str, tier: int, warnings: list[str], blocks: list[str]) -> tuple[str, ...]:
    if verdict == "block":
        return (
            "stop before taking action",
            "show blocks to the user",
            "ask for explicit approval or missing context",
        )
    if verdict == "warn":
        steps = ["show warnings before execution"]
        if tier >= 4:
            steps.append("propose a smaller plan before tool calls")
        if warnings:
            steps.append("attach policy and check trace to the agent response")
        return tuple(steps)
    return ("proceed with policy trace",)
