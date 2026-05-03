"""Deterministic context compression.

The compressor turns a list of :class:`ContextFragment` objects into a
compact text representation that the assembler can hand off as the
governed context for the next step. Compression is purely deterministic:

* Constraints, decisions, open questions, blockers, file paths, test
  results, and memory backend provenance are preserved verbatim.
* Repeated log lines collapse to one occurrence with a count suffix.
* Verbose tool output past a per-fragment line cap is truncated with an
  explicit ``...truncated...`` marker.

There is no LLM summarizer in this milestone. ``CompressedContext``
ships with a ``validation_status`` field plus a list of preservation
checks the caller can audit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .fragment import ContextFragment

# Tool result fragments past this many lines collapse into a head/tail
# pair with a truncation marker. The cap is generous enough that small
# tool outputs survive intact.
TOOL_RESULT_LINE_CAP = 12

# Path-like tokens. Used for "preserve file paths" behavior.
_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:\\|\.{0,2}/|/)?(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"|[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8}"
)


@dataclass(frozen=True)
class CompressedContext:
    text: str
    source_fragment_ids: tuple[str, ...]
    preserved_constraints: tuple[str, ...]
    preserved_decisions: tuple[str, ...]
    omitted_fragment_ids: tuple[str, ...]
    token_estimate_before: int
    token_estimate_after: int
    validation_status: str
    validation_errors: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source_fragment_ids": list(self.source_fragment_ids),
            "preserved_constraints": list(self.preserved_constraints),
            "preserved_decisions": list(self.preserved_decisions),
            "omitted_fragment_ids": list(self.omitted_fragment_ids),
            "token_estimate_before": self.token_estimate_before,
            "token_estimate_after": self.token_estimate_after,
            "validation_status": self.validation_status,
            "validation_errors": list(self.validation_errors),
            "metadata": dict(self.metadata),
        }


def _section_header(title: str) -> str:
    return f"## {title}"


def _collapse_repeats(text: str) -> str:
    """Collapse identical consecutive lines into one with a count suffix."""
    if not text:
        return text
    lines = text.splitlines()
    if not lines:
        return text
    out: list[str] = []
    last: str | None = None
    repeat = 0
    for line in lines:
        if line == last:
            repeat += 1
            continue
        if last is not None:
            out.append(_with_repeat(last, repeat))
        last = line
        repeat = 1
    if last is not None:
        out.append(_with_repeat(last, repeat))
    return "\n".join(out)


def _with_repeat(line: str, count: int) -> str:
    if count <= 1:
        return line
    return f"{line}  (x{count})"


def _truncate_lines(text: str, cap: int) -> tuple[str, bool]:
    if not text:
        return text, False
    lines = text.splitlines()
    if len(lines) <= cap:
        return text, False
    head = lines[: max(1, cap // 2)]
    tail = lines[-max(1, cap // 2):]
    return "\n".join([*head, "...truncated...", *tail]), True


def _extract_paths(text: str) -> list[str]:
    if not text:
        return []
    return [match for match in _PATH_PATTERN.findall(text)]


def _has_phrase(text: str, needle: str) -> bool:
    return needle.lower() in text.lower()


class ContextCompressor:
    def __init__(self, *, tool_result_line_cap: int = TOOL_RESULT_LINE_CAP) -> None:
        self.tool_result_line_cap = tool_result_line_cap

    def compress(
        self,
        fragments: Iterable[ContextFragment],
    ) -> CompressedContext:
        ordered = list(fragments)
        token_estimate_before = sum(int(f.token_estimate or 0) for f in ordered)

        sections: dict[str, list[str]] = {
            "User instructions": [],
            "Constraints": [],
            "Decisions": [],
            "Open questions": [],
            "Memory (Honcho)": [],
            "Memory (MemPalace)": [],
            "Tool inputs": [],
            "Tool results": [],
            "Audit observations": [],
            "Other": [],
        }

        omitted_ids: list[str] = []
        preserved_constraints: list[str] = []
        preserved_decisions: list[str] = []
        memory_provenance: list[str] = []
        seen_text: set[str] = set()

        for fragment in ordered:
            text = (fragment.redacted_text or fragment.text or "").strip()
            if not text:
                omitted_ids.append(fragment.fragment_id)
                continue
            normalized = re.sub(r"\s+", " ", text.lower())
            # Skip exact text duplicates inside the same compressed result.
            if normalized in seen_text:
                omitted_ids.append(fragment.fragment_id)
                continue
            seen_text.add(normalized)

            if fragment.kind == "constraint":
                preserved_constraints.append(text)
                sections["Constraints"].append(self._tag(fragment, text))
            elif fragment.kind == "decision":
                preserved_decisions.append(text)
                sections["Decisions"].append(self._tag(fragment, text))
            elif fragment.kind == "open_question":
                sections["Open questions"].append(self._tag(fragment, text))
            elif fragment.kind == "user_instruction":
                sections["User instructions"].append(self._tag(fragment, text))
            elif fragment.kind == "honcho_memory":
                memory_provenance.append(
                    f"honcho:{fragment.fragment_id}"
                )
                sections["Memory (Honcho)"].append(
                    f"[honcho/{fragment.fragment_id}] {text}"
                )
            elif fragment.kind == "mempalace_memory":
                memory_provenance.append(
                    f"mempalace:{fragment.fragment_id}"
                )
                sections["Memory (MemPalace)"].append(
                    f"[mempalace/{fragment.fragment_id}] {text}"
                )
            elif fragment.kind == "tool_input":
                sections["Tool inputs"].append(self._tag(fragment, text))
            elif fragment.kind == "tool_result":
                compressed_text, _ = _truncate_lines(
                    _collapse_repeats(text), self.tool_result_line_cap
                )
                sections["Tool results"].append(self._tag(fragment, compressed_text))
            elif fragment.kind == "audit_observation":
                sections["Audit observations"].append(self._tag(fragment, text))
            elif fragment.kind == "final_response":
                sections["User instructions"].append(self._tag(fragment, text))
            else:
                sections["Other"].append(self._tag(fragment, text))

        body_parts: list[str] = []
        for title, lines in sections.items():
            if not lines:
                continue
            body_parts.append(_section_header(title))
            body_parts.extend(lines)
            body_parts.append("")
        if memory_provenance:
            body_parts.append(_section_header("Memory provenance"))
            body_parts.extend(sorted(set(memory_provenance)))
        text = "\n".join(part for part in body_parts if part is not None).strip()

        from ..token_budget import estimate_tokens

        token_after, _ = estimate_tokens(text)

        included_ids = tuple(
            f.fragment_id for f in ordered if f.fragment_id not in set(omitted_ids)
        )
        validation_status, validation_errors = self.validate(
            text=text,
            included_fragments=[f for f in ordered if f.fragment_id in included_ids],
            preserved_constraints=preserved_constraints,
            preserved_decisions=preserved_decisions,
        )

        return CompressedContext(
            text=text,
            source_fragment_ids=included_ids,
            preserved_constraints=tuple(preserved_constraints),
            preserved_decisions=tuple(preserved_decisions),
            omitted_fragment_ids=tuple(omitted_ids),
            token_estimate_before=token_estimate_before,
            token_estimate_after=int(token_after),
            validation_status=validation_status,
            validation_errors=tuple(validation_errors),
            metadata={
                "memory_provenance": sorted(set(memory_provenance)),
            },
        )

    # ------------------------------------------------------------------
    # Validation

    def validate(
        self,
        *,
        text: str,
        included_fragments: list[ContextFragment],
        preserved_constraints: list[str],
        preserved_decisions: list[str],
    ) -> tuple[str, list[str]]:
        errors: list[str] = []

        for constraint_text in preserved_constraints:
            if not _has_phrase(text, constraint_text):
                errors.append(f"missing constraint: {constraint_text}")

        for decision_text in preserved_decisions:
            if not _has_phrase(text, decision_text):
                errors.append(f"missing decision: {decision_text}")

        for fragment in included_fragments:
            if fragment.kind == "open_question":
                if not _has_phrase(text, (fragment.redacted_text or fragment.text)):
                    errors.append(
                        f"missing open question for fragment {fragment.fragment_id}"
                    )
            metadata = fragment.metadata or {}
            if metadata.get("blocker") is True:
                if not _has_phrase(text, fragment.redacted_text or fragment.text):
                    errors.append(
                        f"missing blocker fragment {fragment.fragment_id}"
                    )

        # Memory backend provenance: every included memory item must
        # have its backend label preserved in the compressed body.
        for fragment in included_fragments:
            if fragment.kind == "honcho_memory" and not _has_phrase(text, "honcho"):
                errors.append(
                    f"missing honcho provenance for {fragment.fragment_id}"
                )
            if (
                fragment.kind == "mempalace_memory"
                and not _has_phrase(text, "mempalace")
            ):
                errors.append(
                    f"missing mempalace provenance for {fragment.fragment_id}"
                )

        # Source IDs must be reachable from the compressed result. The
        # compressor tags every preserved fragment with its id, so a
        # missing tag indicates a bug, not a user-driven omission.
        for fragment in included_fragments:
            if fragment.fragment_id not in text:
                errors.append(
                    f"missing source id tag for {fragment.fragment_id}"
                )

        if errors:
            return "fail", errors
        return "pass", []

    # ------------------------------------------------------------------
    # Internal helpers

    @staticmethod
    def _tag(fragment: ContextFragment, body: str) -> str:
        backend = fragment.memory_backend or fragment.source.memory_backend
        prefix = f"[{fragment.kind}/{fragment.fragment_id}]"
        if backend:
            prefix = f"[{fragment.kind}/{fragment.fragment_id}/{backend}]"
        # Preserve any path-like tokens by leaving the body unchanged.
        # This loop only exists to make path preservation auditable from
        # the compressor's logs.
        _ = _extract_paths(body)
        return f"{prefix} {body}"
