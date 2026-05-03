from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Constraint:
    kind: str
    condition: str
    target: str
    source_index: int | None = None

    def key(self) -> tuple[str, str]:
        return (_normalize(self.condition), _normalize(self.target))

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "kind": self.kind,
            "condition": self.condition,
            "target": self.target,
            "source_index": self.source_index,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Constraint":
        source_index = data.get("source_index")
        return cls(
            kind=str(data["kind"]),
            condition=str(data["condition"]),
            target=str(data["target"]),
            source_index=int(source_index) if source_index is not None else None,
        )


def parse_constraints(text: str) -> tuple[Constraint, ...]:
    constraints: list[Constraint] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parsed = _parse_constraint(line)
        if parsed:
            constraints.append(parsed)
    return tuple(constraints)


def find_constraint_conflicts(text: str) -> tuple[str, ...]:
    required: dict[tuple[str, str], Constraint] = {}
    forbidden: dict[tuple[str, str], Constraint] = {}
    for constraint in parse_constraints(text):
        if constraint.kind == "requires":
            required[constraint.key()] = constraint
        elif constraint.kind == "forbids":
            forbidden[constraint.key()] = constraint

    conflicts = []
    for key in sorted(set(required) & set(forbidden)):
        constraint = required[key]
        conflicts.append(f"{constraint.condition} both requires and forbids {constraint.target}")
    return tuple(conflicts)


def evaluate_policy_constraints(
    constraints: tuple[str, ...],
    task: str,
    memory: str,
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    blocks: list[str] = []
    task_text = _normalize(task)
    full_text = _normalize(f"{task}\n{memory}")

    for constraint in parse_constraints("\n".join(constraints)):
        condition = _normalize(constraint.condition)
        target = _normalize(constraint.target)
        condition_in_task = condition in task_text
        condition_in_full = condition in full_text
        target_in_full = target in full_text

        if constraint.kind == "requires" and condition_in_task and not target_in_full:
            warnings.append(f"policy requires '{constraint.target}' when '{constraint.condition}' is present")
        elif constraint.kind == "forbids" and condition_in_full and target_in_full:
            blocks.append(f"policy forbids '{constraint.condition}' with '{constraint.target}'")

    return warnings, blocks


def _parse_constraint(line: str, source_index: int | None = None) -> Constraint | None:
    call = re.fullmatch(r"(requires|forbids)\(([^,]+),\s*([^)]+)\)", line, flags=re.IGNORECASE)
    if call:
        return Constraint(call.group(1).lower(), call.group(2).strip(), call.group(3).strip(), source_index)

    arrow = re.fullmatch(r"(requires|forbids)\s*:\s*(.+?)\s*->\s*(.+)", line, flags=re.IGNORECASE)
    if arrow:
        return Constraint(arrow.group(1).lower(), arrow.group(2).strip(), arrow.group(3).strip(), source_index)

    must = re.search(
        r"\b(?P<condition>[\w-]+)\s+must\s+"
        r"(?:(?:use|have|include|require|run|pass|be|keep)\s+)?"
        r"(?P<target>[\w-]+)\b",
        line,
        flags=re.IGNORECASE,
    )
    if must:
        return Constraint("requires", must.group("condition").strip(), must.group("target").strip(), source_index)

    negative = re.search(
        r"\b(?:don'?t|do\s+not|must\s+not|never)\s+"
        r"(?P<condition>[\w-]+)"
        r"(?:\s+(?:with|in|on|using|for)\s+|\s+)"
        r"(?P<target>[\w-]+)\b",
        line,
        flags=re.IGNORECASE,
    )
    if negative:
        return Constraint("forbids", negative.group("condition").strip(), negative.group("target").strip(), source_index)

    return None


def _normalize(value: str) -> str:
    return re.sub(r"[\s_-]+", " ", value.strip().lower())
