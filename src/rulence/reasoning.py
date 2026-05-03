from __future__ import annotations

import uuid

from .checks import find_contradictions
from .models import CheckResult, ReasoningSession, ReasoningStep
from .preflight import preflight_task


def start_session(
    task: str,
    memory: str = "",
    policy_dir: str | None = None,
    model: str | None = None,
    session_id: str | None = None,
) -> ReasoningSession:
    preflight = preflight_task(task, memory=memory, policy_dir=policy_dir, model=model)
    warnings = preflight.blocks if preflight.verdict == "block" else preflight.warnings
    return ReasoningSession(
        session_id=session_id or uuid.uuid4().hex,
        task=task,
        preflight=preflight,
        status="thinking",
        warnings=warnings,
    )


def add_thought(
    session: ReasoningSession,
    thought: str,
    thought_number: int | None = None,
    total_thoughts: int | None = None,
    next_thought_needed: bool = False,
    is_revision: bool = False,
    revises_thought: int | None = None,
    branch_from_thought: int | None = None,
    branch_id: str | None = None,
    needs_more_thoughts: bool = False,
) -> ReasoningSession:
    number = thought_number or len(session.steps) + 1
    total = max(total_thoughts or max(number, len(session.steps) + 1), number)
    step = ReasoningStep(
        thought=thought,
        thought_number=number,
        total_thoughts=total,
        next_thought_needed=next_thought_needed,
        is_revision=is_revision,
        revises_thought=revises_thought,
        branch_from_thought=branch_from_thought,
        branch_id=branch_id,
        needs_more_thoughts=needs_more_thoughts,
        checks=tuple(_step_checks(session, thought, number, total, next_thought_needed, is_revision, revises_thought, branch_from_thought, branch_id)),
    )

    warnings = session.warnings + tuple(check.detail for check in step.checks if check.status == "warn")
    status = _status_for(step)
    return _replace_session(
        session,
        steps=session.steps + (step,),
        status=status,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def summarize_session(session: ReasoningSession) -> dict:
    branches = sorted({step.branch_id for step in session.steps if step.branch_id})
    revisions = [step for step in session.steps if step.is_revision]
    warnings = list(session.warnings)
    return {
        "session_id": session.session_id,
        "task": session.task,
        "status": session.status,
        "tier": session.preflight.classification.label,
        "verdict": session.preflight.verdict,
        "step_count": len(session.steps),
        "branches": branches,
        "revision_count": len(revisions),
        "warnings": warnings,
        "next_thought_needed": bool(session.steps and session.steps[-1].next_thought_needed),
    }


def _step_checks(
    session: ReasoningSession,
    thought: str,
    number: int,
    total: int,
    next_thought_needed: bool,
    is_revision: bool,
    revises_thought: int | None,
    branch_from_thought: int | None,
    branch_id: str | None,
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    existing_numbers = {step.thought_number for step in session.steps}

    if not thought.strip():
        checks.append(CheckResult("thought_presence", "block", "thought cannot be empty"))
    else:
        checks.append(CheckResult("thought_presence", "pass", "thought recorded"))

    if number < 1 or total < 1 or number > total:
        checks.append(CheckResult("sequence_bounds", "warn", "thought number should be between 1 and total thoughts"))
    else:
        checks.append(CheckResult("sequence_bounds", "pass", "thought number is within declared range"))

    if is_revision and revises_thought not in existing_numbers:
        checks.append(CheckResult("revision_target", "warn", "revision points to a missing thought"))
    elif is_revision:
        checks.append(CheckResult("revision_target", "pass", "revision target exists"))

    if branch_from_thought is not None and branch_from_thought not in existing_numbers:
        checks.append(CheckResult("branch_target", "warn", "branch points to a missing thought"))
    elif branch_from_thought is not None and not branch_id:
        checks.append(CheckResult("branch_target", "warn", "branch_id is required when branching"))
    elif branch_from_thought is not None:
        checks.append(CheckResult("branch_target", "pass", "branch target exists"))

    contradictions = find_contradictions(_trace_text(session, thought))
    if contradictions:
        checks.append(CheckResult("trace_consistency", "warn", "reasoning trace contains a contradiction pattern", tuple(contradictions)))
    else:
        checks.append(CheckResult("trace_consistency", "pass", "no simple contradiction pattern found in trace"))

    quality_warnings = _quality_warnings(thought, session.preflight.classification.tier)
    if quality_warnings:
        checks.append(CheckResult("reasoning_quality", "warn", "reasoning step is missing expected structure", tuple(quality_warnings)))
    else:
        checks.append(CheckResult("reasoning_quality", "pass", "reasoning step includes enough structure for this tier"))

    readiness_warnings = _final_readiness_warnings(thought, session.preflight.classification.tier, next_thought_needed)
    if readiness_warnings:
        checks.append(CheckResult("final_readiness", "warn", "final thought is missing expected verification", tuple(readiness_warnings)))
    elif not next_thought_needed:
        checks.append(CheckResult("final_readiness", "pass", "final thought includes enough readiness signal for this tier"))

    return checks


def _quality_warnings(thought: str, tier: int) -> list[str]:
    normalized = thought.lower()
    warnings: list[str] = []
    if tier >= 3 and not any(marker in normalized for marker in ("because", "therefore", "so ", "evidence", "assume", "risk")):
        warnings.append("tier 3+ reasoning should name evidence, assumptions, or causal logic")
    if tier >= 4 and not any(marker in normalized for marker in ("risk", "failure", "counterexample", "rollback", "constraint", "tradeoff")):
        warnings.append("tier 4+ reasoning should include a risk, constraint, tradeoff, or counterexample")
    if tier >= 5 and "approval" not in normalized:
        warnings.append("tier 5 reasoning should mention explicit approval before action")
    return warnings


def _final_readiness_warnings(thought: str, tier: int, next_thought_needed: bool) -> list[str]:
    if next_thought_needed:
        return []
    normalized = thought.lower()
    warnings: list[str] = []
    if tier >= 3 and not any(marker in normalized for marker in ("therefore", "conclusion", "decision", "answer", "final")):
        warnings.append("final tier 3+ thought should state a conclusion, decision, or answer")
    if tier >= 4 and not any(marker in normalized for marker in ("verified", "counterexample", "risk", "constraint", "rollback", "test")):
        warnings.append("final tier 4+ thought should mention verification, risk, test, constraint, or counterexample")
    if tier >= 5 and "approval" not in normalized:
        warnings.append("final tier 5 thought must keep action blocked until explicit approval")
    return warnings


def _trace_text(session: ReasoningSession, current_thought: str) -> str:
    return "\n".join([session.task, *(step.thought for step in session.steps), current_thought])


def _status_for(step: ReasoningStep) -> str:
    if any(check.status == "block" for check in step.checks):
        return "blocked"
    if step.next_thought_needed or step.needs_more_thoughts:
        return "thinking"
    return "complete"


def _replace_session(
    session: ReasoningSession,
    *,
    steps: tuple[ReasoningStep, ...],
    status: str,
    warnings: tuple[str, ...],
) -> ReasoningSession:
    return ReasoningSession(
        session_id=session.session_id,
        task=session.task,
        preflight=session.preflight,
        steps=steps,
        status=status,
        warnings=warnings,
    )
