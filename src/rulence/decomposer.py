from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from .classifier import classify_task
from .logic import Constraint, _parse_constraint
from .models import TaskDAG, TaskUnit


IMPERATIVE_VERBS = {
    "add",
    "audit",
    "build",
    "check",
    "classify",
    "compare",
    "confirm",
    "configure",
    "create",
    "debug",
    "delete",
    "deploy",
    "design",
    "document",
    "ensure",
    "extract",
    "fix",
    "generate",
    "implement",
    "install",
    "investigate",
    "make",
    "migrate",
    "notify",
    "plan",
    "prepare",
    "publish",
    "refactor",
    "remove",
    "render",
    "replace",
    "research",
    "review",
    "run",
    "schedule",
    "scaffold",
    "set",
    "ship",
    "stage",
    "start",
    "stop",
    "test",
    "update",
    "use",
    "validate",
    "verify",
    "write",
}

ORDER_MARKERS = {
    "first",
    "then",
    "next",
    "after",
    "before",
    "once",
    "finally",
}

DEPENDENCY_MARKERS = {"then", "next", "after", "once", "finally"}
PARALLEL_MARKERS = {"in parallel", "concurrently", "at the same time"}
NEGATIVE_RE = re.compile(r"\b(?:don'?t|do\s+not|must\s+not|never)\b", re.IGNORECASE)
MODAL_RE = re.compile(r"\b(?:must|should|need\s+to|needs\s+to|has\s+to|have\s+to|will)\b", re.IGNORECASE)
WORD_RE = re.compile(r"[a-zA-Z0-9_'-]+")
CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)


@dataclass
class Sentence:
    index: int
    text: str
    raw_text: str
    header: str | None = None
    list_number: int | None = None
    marker: str | None = None


@dataclass
class UnitDraft:
    instruction: str
    raw_text: str
    polarity: str
    verb: str
    constraints: tuple[Constraint, ...]
    sentence: Sentence
    tier: int
    word_count: int
    depends_on: tuple[str, ...] = ()
    applies_to: tuple[str, ...] = ()
    children: tuple[TaskUnit, ...] = ()


def decompose_prompt(prompt: str, max_depth: int = 3, depth: int = 0) -> TaskDAG:
    max_depth = max(1, min(int(max_depth), 5))
    sentences = _segment_prompt(prompt)
    drafts = _group_units(sentences)
    drafts = _assign_dependencies(drafts)
    drafts = _assign_children(drafts, max_depth=max_depth, depth=depth)
    root_units = _materialize_units(drafts, depth=depth)
    flat = _flatten(root_units)
    constraints = tuple(constraint for unit in flat for constraint in unit.constraints)
    return TaskDAG(
        root_units=root_units,
        flat=flat,
        all_constraints=constraints,
        word_count_original=_word_count(prompt),
        word_count_decomposed=sum(unit.word_count for unit in flat),
        max_depth_reached=max((unit.depth for unit in flat), default=depth),
        method="deterministic-v1",
    )


def constraints_to_policy_lines(constraints: tuple[Constraint, ...]) -> tuple[str, ...]:
    return tuple(f"{constraint.kind}({constraint.condition}, {constraint.target})" for constraint in constraints)


def _segment_prompt(prompt: str) -> list[Sentence]:
    masked, code_blocks = _mask_code_blocks(prompt)
    sentences: list[Sentence] = []
    current_header: str | None = None

    for raw_line in masked.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if _is_code_placeholder(line):
            restored = _restore_code(line, code_blocks)
            if sentences:
                previous = sentences[-1]
                sentences[-1] = replace(previous, text=f"{previous.text}\n\n{restored}", raw_text=f"{previous.raw_text}\n\n{restored}")
            else:
                sentences.append(
                    Sentence(
                        index=len(sentences),
                        text=restored,
                        raw_text=restored,
                        header=current_header,
                    )
                )
            continue

        header = re.match(r"^(#+)\s+(.+)$", line)
        if header:
            current_header = header.group(2).strip()
            continue

        numbered = re.match(r"^(\d+)\.\s+(.+)$", line)
        list_number = int(numbered.group(1)) if numbered else None
        body = numbered.group(2).strip() if numbered else line

        for part in _split_sentences(body):
            restored = _restore_code(part, code_blocks).strip()
            if not restored:
                continue
            marker, instruction = _strip_order_marker(restored)
            sentences.append(
                Sentence(
                    index=len(sentences),
                    text=instruction,
                    raw_text=restored,
                    header=current_header,
                    list_number=list_number,
                    marker=marker,
                )
            )

    return sentences


def _mask_code_blocks(prompt: str) -> tuple[str, dict[str, str]]:
    blocks: dict[str, str] = {}

    def replace_block(match: re.Match[str]) -> str:
        key = f"@@RULENCE_CODE_BLOCK_{len(blocks)}@@"
        blocks[key] = match.group(0)
        return f"\n{key}\n"

    return CODE_BLOCK_RE.sub(replace_block, prompt), blocks


def _restore_code(text: str, blocks: dict[str, str]) -> str:
    restored = text
    for key, value in blocks.items():
        restored = restored.replace(key, value)
    return restored


def _is_code_placeholder(text: str) -> bool:
    return bool(re.fullmatch(r"@@RULENCE_CODE_BLOCK_\d+@@", text))


def _split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?;])\s+|;\s+", normalized)
    return [part.strip(" \t\r\n.!?;") for part in parts if part.strip(" \t\r\n.!?;")]


def _group_units(sentences: list[Sentence]) -> list[UnitDraft]:
    units: list[UnitDraft] = []
    for sentence in sentences:
        polarity, verb, imperative = _detect_imperative(sentence.text)
        constraints = _extract_constraints(sentence)
        if imperative or not units:
            instruction = _instruction_core(sentence.text)
            tier = classify_task(instruction).tier
            units.append(
                UnitDraft(
                    instruction=instruction,
                    raw_text=sentence.raw_text,
                    polarity=polarity,
                    verb=verb,
                    constraints=constraints,
                    sentence=sentence,
                    tier=tier,
                    word_count=_word_count(instruction),
                )
            )
            continue

        previous = units[-1]
        units[-1] = replace(
            previous,
            raw_text=f"{previous.raw_text} {sentence.raw_text}".strip(),
            constraints=tuple([*previous.constraints, *constraints]),
        )
    return units


def _detect_imperative(text: str) -> tuple[str, str, bool]:
    normalized = text.strip()
    lower = normalized.lower()
    polarity = "don't" if NEGATIVE_RE.search(lower) else "do"

    negative = re.match(r"^(?:please\s+)?(?:don'?t|do\s+not|must\s+not|never)\s+([a-zA-Z0-9_-]+)", lower)
    if negative:
        return polarity, negative.group(1), True

    words = WORD_RE.findall(lower)
    if not words:
        return polarity, "", False

    start = 1 if words[0] in ORDER_MARKERS and len(words) > 1 else 0
    if words[start] == "please" and len(words) > start + 1:
        start += 1

    if words[start] in IMPERATIVE_VERBS:
        return polarity, words[start], True

    if MODAL_RE.search(lower):
        modal_verb = re.search(
            r"\b(?:must|should|need\s+to|needs\s+to|has\s+to|have\s+to|will)\s+(?:not\s+)?([a-zA-Z0-9_-]+)",
            lower,
        )
        return polarity, modal_verb.group(1) if modal_verb else words[0], True

    return polarity, "", False


def _extract_constraints(sentence: Sentence) -> tuple[Constraint, ...]:
    parsed = _parse_constraint(sentence.text, source_index=sentence.index)
    return (parsed,) if parsed else ()


def _instruction_core(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^(?:first|then|next|after|before|once|finally),?\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:and|or)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _assign_dependencies(units: list[UnitDraft]) -> list[UnitDraft]:
    result: list[UnitDraft] = []
    object_owner: dict[str, str] = {}
    unit_ids = _draft_ids(units)

    for index, unit in enumerate(units):
        unit_id = unit_ids[index]
        prior_same_group = [
            (i, existing)
            for i, existing in enumerate(result)
            if existing.sentence.header == unit.sentence.header
        ]
        applies_to: tuple[str, ...] = ()
        depends: list[str] = []

        if _is_cross_cutting(unit) and prior_same_group:
            applies_to = tuple(unit_ids[i] for i, _ in prior_same_group)
        elif not _is_parallel(unit):
            previous_id = unit_ids[index - 1] if index > 0 else None
            previous = result[-1] if result else None
            if unit.sentence.marker in DEPENDENCY_MARKERS and previous and previous.sentence.header == unit.sentence.header and previous_id:
                depends.append(previous_id)
            if unit.sentence.list_number and unit.sentence.list_number > 1 and previous and previous.sentence.header == unit.sentence.header and previous_id:
                depends.append(previous_id)
            for obj, owner_id in object_owner.items():
                if owner_id != unit_id and re.search(rf"\bthe\s+{re.escape(obj)}\b", unit.instruction.lower()):
                    depends.append(owner_id)

        target = _target_object(unit.instruction, unit.verb)
        if target:
            object_owner.setdefault(target, unit_id)

        result.append(replace(unit, depends_on=tuple(dict.fromkeys(depends)), applies_to=applies_to))

    return result


def _assign_children(units: list[UnitDraft], max_depth: int, depth: int) -> list[UnitDraft]:
    if depth >= max_depth - 1:
        return units

    result: list[UnitDraft] = []
    for unit in units:
        if unit.tier >= 3 and unit.word_count > 30:
            sub_dag = decompose_prompt(unit.raw_text, max_depth=max_depth, depth=depth + 1)
            children = sub_dag.root_units
            if len(children) == 1 and _normalize(children[0].instruction) == _normalize(unit.instruction):
                children = ()
            if not children:
                children = _clause_children(unit.raw_text, depth + 1)
            result.append(replace(unit, children=children))
        else:
            result.append(unit)
    return result


def _clause_children(text: str, depth: int) -> tuple[TaskUnit, ...]:
    parts = [part.strip(" .!?") for part in re.split(r"[,;:]\s+", text) if _word_count(part) >= 2]
    if len(parts) <= 1:
        return ()

    drafts: list[UnitDraft] = []
    for index, part in enumerate(parts):
        polarity, verb, imperative = _detect_imperative(part)
        if not imperative:
            words = WORD_RE.findall(part.lower())
            verb = words[0] if words else ""
        sentence = Sentence(index=index, text=part, raw_text=part)
        instruction = _instruction_core(part)
        drafts.append(
            UnitDraft(
                instruction=instruction,
                raw_text=part,
                polarity=polarity,
                verb=verb,
                constraints=_extract_constraints(sentence),
                sentence=sentence,
                tier=classify_task(instruction).tier,
                word_count=_word_count(instruction),
            )
        )
    return _materialize_units(_assign_dependencies(drafts), depth)


def _materialize_units(drafts: list[UnitDraft], depth: int) -> tuple[TaskUnit, ...]:
    ids = _draft_ids(drafts)
    return tuple(
        TaskUnit(
            id=ids[index],
            instruction=draft.instruction,
            raw_text=draft.raw_text,
            tier=draft.tier,
            polarity=draft.polarity,
            verb=draft.verb,
            constraints=draft.constraints,
            depends_on=draft.depends_on,
            applies_to=draft.applies_to,
            children=draft.children,
            word_count=draft.word_count,
            depth=depth,
        )
        for index, draft in enumerate(drafts)
    )


def _draft_ids(drafts: list[UnitDraft]) -> list[str]:
    seen: dict[str, int] = {}
    ids: list[str] = []
    for draft in drafts:
        base = _stable_id(draft.instruction)
        count = seen.get(base, 0) + 1
        seen[base] = count
        ids.append(base if count == 1 else f"{base}-{count}")
    return ids


def _stable_id(instruction: str) -> str:
    return hashlib.sha1(_normalize(instruction).encode("utf-8")).hexdigest()[:12]


def _flatten(units: tuple[TaskUnit, ...]) -> tuple[TaskUnit, ...]:
    flat: list[TaskUnit] = []
    for unit in units:
        flat.append(unit)
        flat.extend(_flatten(unit.children))
    return tuple(flat)


def _is_parallel(unit: UnitDraft) -> bool:
    lower = unit.instruction.lower()
    return any(marker in lower for marker in PARALLEL_MARKERS)


def _is_cross_cutting(unit: UnitDraft) -> bool:
    lower = unit.instruction.lower()
    if any(phrase in lower for phrase in ("all tasks", "everything", "for each", "every")):
        return True
    return bool(re.match(r"^(?:test|tests)\b", lower)) and "specific" not in lower


def _target_object(instruction: str, verb: str) -> str | None:
    if not verb:
        return None
    lower = instruction.lower()
    match = re.search(rf"\b{re.escape(verb)}\b\s+(?:the\s+|a\s+|an\s+)?([a-zA-Z0-9_-]+)", lower)
    if not match:
        return None
    target = match.group(1)
    if target in {"all", "every", "each", "it", "this", "that"}:
        return None
    return target


def _strip_order_marker(text: str) -> tuple[str | None, str]:
    match = re.match(r"^(first|then|next|after|before|once|finally),?\s+(.+)$", text.strip(), flags=re.IGNORECASE)
    if match:
        return match.group(1).lower(), match.group(2).strip()
    return None, text


def _word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())
