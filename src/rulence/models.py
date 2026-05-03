from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from .logic import Constraint


class ClassificationSignals(TypedDict, total=False):
    word_count: int
    high_risk_terms: list[str]
    destructive_terms: list[str]
    external_action_terms: list[str]
    tool_terms: list[str]
    complex_terms: list[str]
    formal_reasoning_terms: list[str]
    ambiguity_terms: list[str]
    multi_clause: bool
    risk_score: int
    complexity_score: int
    local_model_tier: int
    local_model_fallback: str


@dataclass(frozen=True)
class Classification:
    task: str
    tier: int
    label: str
    reasons: tuple[str, ...]
    signals: ClassificationSignals

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "tier": self.tier,
            "label": self.label,
            "reasons": list(self.reasons),
            "signals": self.signals,
        }


@dataclass(frozen=True)
class Policy:
    tier: int
    label: str
    ref: str
    required_checks: tuple[str, ...]
    warn_if: tuple[str, ...] = ()
    block_if: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    decompose_threshold: int = 2000
    decompose_max_depth: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "label": self.label,
            "ref": self.ref,
            "required_checks": list(self.required_checks),
            "warn_if": list(self.warn_if),
            "block_if": list(self.block_if),
            "constraints": list(self.constraints),
            "decompose_threshold": self.decompose_threshold,
            "decompose_max_depth": self.decompose_max_depth,
        }


@dataclass(frozen=True)
class TokenBudget:
    input_estimate: int
    governed_context_estimate: int
    policy_overhead_estimate: int
    method: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_estimate": self.input_estimate,
            "governed_context_estimate": self.governed_context_estimate,
            "policy_overhead_estimate": self.policy_overhead_estimate,
            "method": self.method,
        }


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class TaskUnit:
    id: str
    instruction: str
    raw_text: str
    tier: int
    polarity: str
    verb: str
    constraints: tuple[Constraint, ...] = ()
    depends_on: tuple[str, ...] = ()
    applies_to: tuple[str, ...] = ()
    children: tuple["TaskUnit", ...] = ()
    word_count: int = 0
    depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "instruction": self.instruction,
            "raw_text": self.raw_text,
            "tier": self.tier,
            "polarity": self.polarity,
            "verb": self.verb,
            "constraints": [constraint.to_dict() for constraint in self.constraints],
            "depends_on": list(self.depends_on),
            "applies_to": list(self.applies_to),
            "children": [child.to_dict() for child in self.children],
            "word_count": self.word_count,
            "depth": self.depth,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskUnit":
        return cls(
            id=str(data["id"]),
            instruction=str(data["instruction"]),
            raw_text=str(data["raw_text"]),
            tier=int(data["tier"]),
            polarity=str(data.get("polarity", "do")),
            verb=str(data.get("verb", "")),
            constraints=tuple(Constraint.from_dict(item) for item in data.get("constraints", [])),
            depends_on=tuple(data.get("depends_on", [])),
            applies_to=tuple(data.get("applies_to", [])),
            children=tuple(cls.from_dict(item) for item in data.get("children", [])),
            word_count=int(data.get("word_count", 0)),
            depth=int(data.get("depth", 0)),
        )


@dataclass(frozen=True)
class TaskDAG:
    root_units: tuple[TaskUnit, ...]
    flat: tuple[TaskUnit, ...]
    all_constraints: tuple[Constraint, ...]
    word_count_original: int
    word_count_decomposed: int
    max_depth_reached: int
    method: str = "deterministic-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_units": [unit.to_dict() for unit in self.root_units],
            "flat": [unit.to_dict() for unit in self.flat],
            "all_constraints": [constraint.to_dict() for constraint in self.all_constraints],
            "word_count_original": self.word_count_original,
            "word_count_decomposed": self.word_count_decomposed,
            "max_depth_reached": self.max_depth_reached,
            "method": self.method,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskDAG":
        return cls(
            root_units=tuple(TaskUnit.from_dict(item) for item in data.get("root_units", [])),
            flat=tuple(TaskUnit.from_dict(item) for item in data.get("flat", [])),
            all_constraints=tuple(Constraint.from_dict(item) for item in data.get("all_constraints", [])),
            word_count_original=int(data.get("word_count_original", 0)),
            word_count_decomposed=int(data.get("word_count_decomposed", 0)),
            max_depth_reached=int(data.get("max_depth_reached", 0)),
            method=str(data.get("method", "deterministic-v1")),
        )


@dataclass(frozen=True)
class PreflightResult:
    classification: Classification
    policy: Policy
    verdict: str
    warnings: tuple[str, ...] = ()
    blocks: tuple[str, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    token_budget: TokenBudget | None = None
    decomposition: TaskDAG | None = None
    next_steps: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.to_dict(),
            "policy": self.policy.to_dict(),
            "verdict": self.verdict,
            "warnings": list(self.warnings),
            "blocks": list(self.blocks),
            "checks": [check.to_dict() for check in self.checks],
            "token_budget": self.token_budget.to_dict() if self.token_budget else None,
            "decomposition": self.decomposition.to_dict() if self.decomposition else None,
            "next_steps": list(self.next_steps),
        }


@dataclass(frozen=True)
class ReasoningStep:
    thought: str
    thought_number: int
    total_thoughts: int
    next_thought_needed: bool
    is_revision: bool = False
    revises_thought: int | None = None
    branch_from_thought: int | None = None
    branch_id: str | None = None
    needs_more_thoughts: bool = False
    checks: tuple[CheckResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "thought": self.thought,
            "thought_number": self.thought_number,
            "total_thoughts": self.total_thoughts,
            "next_thought_needed": self.next_thought_needed,
            "is_revision": self.is_revision,
            "revises_thought": self.revises_thought,
            "branch_from_thought": self.branch_from_thought,
            "branch_id": self.branch_id,
            "needs_more_thoughts": self.needs_more_thoughts,
            "checks": [check.to_dict() for check in self.checks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReasoningStep":
        return cls(
            thought=str(data["thought"]),
            thought_number=int(data["thought_number"]),
            total_thoughts=int(data["total_thoughts"]),
            next_thought_needed=bool(data["next_thought_needed"]),
            is_revision=bool(data.get("is_revision", False)),
            revises_thought=data.get("revises_thought"),
            branch_from_thought=data.get("branch_from_thought"),
            branch_id=data.get("branch_id"),
            needs_more_thoughts=bool(data.get("needs_more_thoughts", False)),
            checks=tuple(CheckResult(**check) for check in data.get("checks", [])),
        )


@dataclass(frozen=True)
class ReasoningSession:
    session_id: str
    task: str
    preflight: PreflightResult
    steps: tuple[ReasoningStep, ...] = ()
    status: str = "thinking"
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task": self.task,
            "preflight": self.preflight.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "status": self.status,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReasoningSession":
        preflight_data = data["preflight"]
        classification_data = preflight_data["classification"]
        policy_data = preflight_data["policy"]
        token_budget_data = preflight_data.get("token_budget")
        token_budget = None
        if token_budget_data:
            token_budget = TokenBudget(
                input_estimate=int(token_budget_data.get("input_estimate", 0)),
                governed_context_estimate=int(token_budget_data.get("governed_context_estimate", 0)),
                policy_overhead_estimate=int(token_budget_data.get("policy_overhead_estimate", 0)),
                method=str(token_budget_data.get("method", "unknown")),
            )
        decompose_threshold = policy_data.get("decompose_threshold")
        decompose_max_depth = policy_data.get("decompose_max_depth")
        preflight = PreflightResult(
            classification=Classification(
                task=classification_data["task"],
                tier=int(classification_data["tier"]),
                label=classification_data["label"],
                reasons=tuple(classification_data.get("reasons", [])),
                signals=classification_data.get("signals", {}),
            ),
            policy=Policy(
                tier=int(policy_data["tier"]),
                label=policy_data["label"],
                ref=policy_data["ref"],
                required_checks=tuple(policy_data.get("required_checks", [])),
                warn_if=tuple(policy_data.get("warn_if", [])),
                block_if=tuple(policy_data.get("block_if", [])),
                constraints=tuple(policy_data.get("constraints", [])),
                decompose_threshold=int(decompose_threshold) if decompose_threshold is not None else 2000,
                decompose_max_depth=int(decompose_max_depth) if decompose_max_depth is not None else 3,
            ),
            verdict=preflight_data["verdict"],
            warnings=tuple(preflight_data.get("warnings", [])),
            blocks=tuple(preflight_data.get("blocks", [])),
            checks=tuple(CheckResult(**check) for check in preflight_data.get("checks", [])),
            token_budget=token_budget,
            decomposition=TaskDAG.from_dict(preflight_data["decomposition"]) if preflight_data.get("decomposition") else None,
            next_steps=tuple(preflight_data.get("next_steps", [])),
        )
        return cls(
            session_id=data["session_id"],
            task=data["task"],
            preflight=preflight,
            steps=tuple(ReasoningStep.from_dict(step) for step in data.get("steps", [])),
            status=data.get("status", "thinking"),
            warnings=tuple(data.get("warnings", [])),
        )
