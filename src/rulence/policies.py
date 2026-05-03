from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path

from .logic import _parse_constraint
from .models import Policy

CHECK_MEMORY = "memory_check"
CHECK_CONSISTENCY = "consistency_check"
CHECK_BUDGET = "context_budget"
CHECK_TOOL = "tool_preflight"
CHECK_COUNTEREXAMPLE = "counterexample_search"
CHECK_CONSTRAINT = "constraint_solver"
CHECK_APPROVAL = "user_approval"
CHECK_DECOMPOSITION = "decomposition_check"
CHECK_TRANSCRIPT_DRIFT = "transcript_drift"
CHECK_TRANSCRIPT_CONTRADICTION = "transcript_contradiction"
CHECK_TRANSCRIPT_STALENESS = "transcript_staleness"
KNOWN_CHECKS = {
    CHECK_MEMORY,
    CHECK_CONSISTENCY,
    CHECK_BUDGET,
    CHECK_TOOL,
    CHECK_COUNTEREXAMPLE,
    CHECK_CONSTRAINT,
    CHECK_APPROVAL,
    CHECK_DECOMPOSITION,
    CHECK_TRANSCRIPT_DRIFT,
    CHECK_TRANSCRIPT_CONTRADICTION,
    CHECK_TRANSCRIPT_STALENESS,
}

DEFAULT_POLICIES: dict[int, Policy] = {
    0: Policy(
        tier=0,
        label="direct",
        ref="builtin:tier-0-direct",
        required_checks=(),
        decompose_threshold=99999,
    ),
    1: Policy(
        tier=1,
        label="trivial",
        ref="builtin:tier-1-trivial",
        required_checks=(CHECK_MEMORY,),
        decompose_threshold=99999,
    ),
    2: Policy(
        tier=2,
        label="mild",
        ref="builtin:tier-2-mild",
        required_checks=(CHECK_MEMORY, CHECK_CONSISTENCY),
        warn_if=("memory_missing",),
        decompose_threshold=4000,
    ),
    3: Policy(
        tier=3,
        label="moderate",
        ref="builtin:tier-3-moderate",
        required_checks=(CHECK_MEMORY, CHECK_CONSISTENCY, CHECK_BUDGET, CHECK_TOOL, CHECK_DECOMPOSITION),
        warn_if=("memory_missing", "budget_high", "contradictions_found"),
        decompose_threshold=2000,
    ),
    4: Policy(
        tier=4,
        label="complex",
        ref="builtin:tier-4-complex",
        required_checks=(CHECK_MEMORY, CHECK_CONSISTENCY, CHECK_BUDGET, CHECK_TOOL, CHECK_COUNTEREXAMPLE, CHECK_CONSTRAINT, CHECK_DECOMPOSITION),
        warn_if=("memory_missing", "budget_high", "counterexample_found", "contradictions_found"),
        block_if=("destructive_with_weak_context",),
        decompose_threshold=1500,
    ),
    5: Policy(
        tier=5,
        label="high-risk",
        ref="builtin:tier-5-high-risk",
        required_checks=(
            CHECK_MEMORY,
            CHECK_CONSISTENCY,
            CHECK_BUDGET,
            CHECK_TOOL,
            CHECK_COUNTEREXAMPLE,
            CHECK_CONSTRAINT,
            CHECK_DECOMPOSITION,
            CHECK_APPROVAL,
        ),
        warn_if=("memory_missing", "budget_high", "counterexample_found", "contradictions_found"),
        block_if=("sensitive_or_destructive_action",),
        decompose_threshold=1000,
    ),
}


def default_policy_dir() -> Path:
    configured = os.environ.get("RULENCE_POLICY_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".rulence" / "policies"


def builtin_policies_dir() -> Path:
    return Path(__file__).parent / "builtin_policies"


def list_builtin_policies() -> list[dict]:
    policies = []
    for path in sorted(builtin_policies_dir().glob("*.toml")):
        parsed = _parse_policy_toml(path)
        policies.append(
            {
                "name": path.stem,
                "label": parsed.get("label", ""),
                "tier": parsed.get("tier"),
                "constraints_count": len(parsed.get("constraints", [])),
            }
        )
    return policies


def install_builtin_policy(name: str, policy_dir: str | Path | None = None) -> Path:
    source = builtin_policies_dir() / f"{name}.toml"
    if not source.exists():
        raise ValueError(f"unknown policy: {name}. Run 'rulence policy list-available'.")

    errors = validate_policy_file(source)
    if errors:
        raise ValueError(f"bundled policy is invalid: {source}: {'; '.join(errors)}")

    parsed = _parse_policy_toml(source)
    directory = Path(policy_dir).expanduser() if policy_dir else default_policy_dir()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"tier-{parsed['tier']}-{parsed['label']}.toml"
    shutil.copy2(source, destination)
    return destination


def load_policy(tier: int, policy_dir: str | Path | None = None) -> Policy:
    directory = Path(policy_dir).expanduser() if policy_dir else default_policy_dir()
    matches: list[Path] = []
    if directory.exists():
        matches = sorted(directory.glob(f"tier-{tier}-*.toml"))
        if not matches:
            matches = sorted(directory.glob(f"tier-{tier}-*.md"))
    if not matches:
        return DEFAULT_POLICIES[tier]

    fallback = DEFAULT_POLICIES[tier]
    parsed_policies = [(match, _parse_policy_file(match)) for match in matches]
    labels = tuple(str(parsed.get("label") or fallback.label) for _, parsed in parsed_policies)
    thresholds = [parsed.get("decompose_threshold") for _, parsed in parsed_policies if parsed.get("decompose_threshold") is not None]
    max_depths = [parsed.get("decompose_max_depth") for _, parsed in parsed_policies if parsed.get("decompose_max_depth") is not None]
    return Policy(
        tier=tier,
        label="+".join(dict.fromkeys(labels)),
        ref="merged:" + ",".join(str(match) for match, _ in parsed_policies),
        required_checks=_merged_values(parsed.get("required_checks", ()) for _, parsed in parsed_policies),
        warn_if=_merged_values(parsed.get("warn_if", ()) for _, parsed in parsed_policies),
        block_if=_merged_values(parsed.get("block_if", ()) for _, parsed in parsed_policies),
        constraints=_merged_values(parsed.get("constraints", ()) for _, parsed in parsed_policies),
        decompose_threshold=min((int(value) for value in thresholds), default=fallback.decompose_threshold),
        decompose_max_depth=max((int(value) for value in max_depths), default=fallback.decompose_max_depth),
    )


def _merged_values(values: object) -> tuple[str, ...]:
    merged: list[str] = []
    for group in values:
        if isinstance(group, str):
            candidates = [group]
        else:
            candidates = list(group)
        for candidate in candidates:
            item = str(candidate)
            if item not in merged:
                merged.append(item)
    return tuple(merged)


def write_default_policies(policy_dir: str | Path | None = None) -> list[Path]:
    directory = Path(policy_dir).expanduser() if policy_dir else default_policy_dir()
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for tier, policy in DEFAULT_POLICIES.items():
        path = directory / f"tier-{tier}-{policy.label}.toml"
        if path.exists():
            continue
        path.write_text(_policy_to_toml(policy), encoding="utf-8")
        written.append(path)
    return written


def validate_policy_file(path: str | Path) -> list[str]:
    policy_path = Path(path).expanduser()
    errors: list[str] = []
    if not policy_path.exists():
        return [f"policy file not found: {policy_path}"]

    try:
        parsed = _parse_policy_file(policy_path)
    except ValueError as exc:
        return [str(exc)]
    tier_raw = parsed.get("tier")
    try:
        tier = int(tier_raw) if tier_raw is not None else None
    except (TypeError, ValueError):
        tier = None

    if tier not in DEFAULT_POLICIES:
        errors.append("tier must be an integer from 0 to 5")

    label = parsed.get("label")
    if not isinstance(label, str) or not label:
        errors.append("label is required")

    for field in ("required_checks", "warn_if", "block_if", "constraints"):
        value = parsed.get(field, [])
        if isinstance(value, str) and value == "[]":
            continue
        if not isinstance(value, list):
            errors.append(f"{field} must be a list")

    required = parsed.get("required_checks", [])
    if isinstance(required, list):
        unknown = sorted(check for check in required if check not in KNOWN_CHECKS)
        if unknown:
            errors.append(f"unknown required_checks: {', '.join(unknown)}")

    constraints = parsed.get("constraints", [])
    if isinstance(constraints, list):
        invalid = [constraint for constraint in constraints if not isinstance(constraint, str) or not _parse_constraint(constraint)]
        if invalid:
            errors.append(f"invalid constraints: {', '.join(str(item) for item in invalid)}")

    for field in ("decompose_threshold", "decompose_max_depth"):
        value = parsed.get(field)
        if value is None:
            continue
        try:
            int_value = int(value)
        except (TypeError, ValueError):
            errors.append(f"{field} must be an integer")
            continue
        if int_value < 0:
            errors.append(f"{field} must be non-negative")

    max_depth = parsed.get("decompose_max_depth")
    if max_depth is not None:
        try:
            if int(max_depth) > 5:
                errors.append("decompose_max_depth must be 5 or less")
        except (TypeError, ValueError):
            pass

    return errors


def validate_policy_dir(policy_dir: str | Path | None = None) -> dict[str, list[str]]:
    directory = Path(policy_dir).expanduser() if policy_dir else default_policy_dir()
    if not directory.exists():
        return {str(directory): ["policy directory does not exist"]}
    results: dict[str, list[str]] = {}
    for path in sorted([*directory.glob("tier-*.toml"), *directory.glob("tier-*.md")]):
        results[str(path)] = validate_policy_file(path)
    if not results:
        results[str(directory)] = ["no policy files found"]
    return results


def _parse_policy_file(path: Path) -> dict[str, list[str] | str | int]:
    if path.suffix == ".toml":
        return _parse_policy_toml(path)
    if path.suffix == ".md":
        return _parse_policy_markdown(path)
    raise ValueError(f"unsupported policy file extension: {path.suffix}")


def _parse_policy_toml(path: Path) -> dict[str, list[str] | str | int]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid TOML policy: {exc}") from exc
    return {
        "tier": data.get("tier"),
        "label": data.get("label"),
        "required_checks": data.get("required_checks", []),
        "warn_if": data.get("warn_if", []),
        "block_if": data.get("block_if", []),
        "constraints": data.get("constraints", []),
        "decompose_threshold": data.get("decompose_threshold"),
        "decompose_max_depth": data.get("decompose_max_depth"),
    }


def _parse_policy_markdown(path: Path) -> dict[str, list[str] | str]:
    data: dict[str, list[str] | str] = {}
    current_key: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line == "---" or line.startswith("#"):
            continue
        if line.endswith(":"):
            current_key = line[:-1]
            data[current_key] = []
            continue
        if line.startswith("- ") and current_key:
            value = line[2:].strip()
            existing = data.setdefault(current_key, [])
            if isinstance(existing, list):
                existing.append(value)
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            current_key = None
            data[key.strip()] = value.strip().strip('"')
    return data


def _policy_to_toml(policy: Policy) -> str:
    def array(values: tuple[str, ...]) -> str:
        if not values:
            return "[]"
        quoted = ", ".join(f'"{value}"' for value in values)
        return f"[{quoted}]"

    return (
        "# This policy is local and inspectable. Edit it to change how agents are governed.\n"
        f"tier = {policy.tier}\n"
        f'label = "{policy.label}"\n'
        f"required_checks = {array(policy.required_checks)}\n"
        f"warn_if = {array(policy.warn_if)}\n"
        f"block_if = {array(policy.block_if)}\n"
        f"constraints = {array(policy.constraints)}\n"
        f"decompose_threshold = {policy.decompose_threshold}\n"
        f"decompose_max_depth = {policy.decompose_max_depth}\n"
    )


def _policy_to_markdown(policy: Policy) -> str:
    def lines(name: str, values: tuple[str, ...]) -> str:
        if not values:
            return f"{name}: []\n"
        joined = "\n".join(f"  - {value}" for value in values)
        return f"{name}:\n{joined}\n"

    return (
        "---\n"
        f"tier: {policy.tier}\n"
        f'label: "{policy.label}"\n'
        f"{lines('required_checks', policy.required_checks)}"
        f"{lines('warn_if', policy.warn_if)}"
        f"{lines('block_if', policy.block_if)}"
        "---\n\n"
        "This policy is local and inspectable. Edit it to change how agents are governed.\n"
    )
