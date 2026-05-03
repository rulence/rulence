"""Deterministic post-tool-use review and compaction.

This module is intentionally regex- and rule-based. No LLM calls, no
context store, no semantic summarization — just classify, redact, and
optionally compact. It runs inside the Claude Code PostToolUse hook
handler so it must remain cheap.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..security import SecretRedactor
from ..token_budget import estimate_tokens

# Token thresholds. ``COMPACT_THRESHOLD`` triggers deterministic
# compaction; ``UNSAFE_SIZE_THRESHOLD`` flags the output as unsafe for
# direct reinjection regardless of compaction.
COMPACT_THRESHOLD = 4000
UNSAFE_SIZE_THRESHOLD = 50000

# Redaction types that on their own escalate the review to ``unsafe``
# because their plaintext value should never reach the model context.
_UNSAFE_REDACTION_TYPES = frozenset(
    {"pem_private_key", "github_pat", "anthropic_key", "openai_project_key"}
)

_BINARY_BLOB_LINE = re.compile(r"^[A-Za-z0-9+/=]{200,}$")
_TRACEBACK_PATTERN = re.compile(r"Traceback \(most recent call last\):")
_JS_STACK_PATTERN = re.compile(
    r"^\s*at .+\(.+:\d+:\d+\)\s*$", re.MULTILINE
)
_TEST_FAILURE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bFAILED\b"),
    re.compile(r"\bAssertionError\b"),
    re.compile(r"^FAIL: ", re.MULTILINE),
    re.compile(r"\bExpectationFailed\b"),
)
# Lines that look "important" enough to retain when compacting:
_IMPORTANT_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:Error|ERROR|error|Failed|FAILED|FAILURE|Failure|Traceback|Exception)\b"),
    re.compile(r"\bAssertionError\b"),
    re.compile(r"^\s*at .+:\d+(?::\d+)?\b"),  # JS/Java stack frame
    re.compile(r"^\s*File \".+\", line \d+", re.MULTILINE),  # Python frame
    re.compile(r"\bexpected\b.*\bgot\b", re.IGNORECASE),
    re.compile(r"^\s*✗"),
)


@dataclass(frozen=True)
class ToolResultSummary:
    original_token_estimate: int
    compressed_token_estimate: int
    summary: str
    retained_facts: tuple[str, ...]
    omitted_sections: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_token_estimate": self.original_token_estimate,
            "compressed_token_estimate": self.compressed_token_estimate,
            "summary": self.summary,
            "retained_facts": list(self.retained_facts),
            "omitted_sections": list(self.omitted_sections),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ToolResultReview:
    status: str  # "safe" | "warn" | "unsafe"
    evidence: tuple[str, ...]
    redaction_count: int
    redaction_types: tuple[str, ...]
    original_token_estimate: int
    filtered_token_estimate: int
    safe_for_model_context: bool
    suggested_action: str  # "allow" | "warn_user" | "ask" | "block"
    summary: ToolResultSummary | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "evidence": list(self.evidence),
            "redaction_count": self.redaction_count,
            "redaction_types": list(self.redaction_types),
            "original_token_estimate": self.original_token_estimate,
            "filtered_token_estimate": self.filtered_token_estimate,
            "safe_for_model_context": self.safe_for_model_context,
            "suggested_action": self.suggested_action,
            "summary": self.summary.to_dict() if self.summary else None,
        }


def _to_text(tool_output: Any) -> str:
    if tool_output is None:
        return ""
    if isinstance(tool_output, str):
        return tool_output
    try:
        return json.dumps(tool_output, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(tool_output)


def _is_binary_like(text: str) -> bool:
    if not text:
        return False
    sample = text[:8192]
    nonprintable = sum(
        1
        for c in sample
        if (ord(c) < 32 and c not in "\n\r\t") or ord(c) > 126
    )
    return nonprintable / max(len(sample), 1) > 0.30


class ToolResultReviewer:
    def __init__(
        self,
        redactor: SecretRedactor | None = None,
        *,
        compact_threshold: int = COMPACT_THRESHOLD,
        unsafe_size_threshold: int = UNSAFE_SIZE_THRESHOLD,
        model: str | None = None,
    ) -> None:
        self.redactor = redactor or SecretRedactor()
        self.compact_threshold = compact_threshold
        self.unsafe_size_threshold = unsafe_size_threshold
        self.model = model

    def review(
        self,
        *,
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_output: Any = None,
        corr_id: str | None = None,
        policy_name: str | None = None,
    ) -> ToolResultReview:
        text = _to_text(tool_output)
        original_tokens = self._estimate(text)

        evidence: list[str] = []

        redaction = self.redactor.redact_text(text)
        redacted_text = redaction.redacted_text
        if redaction.redaction_count > 0:
            unique_types = sorted(set(redaction.redaction_types))
            evidence.append(
                f"redacted {redaction.redaction_count} secret(s): {', '.join(unique_types)}"
            )

        binary_like = _is_binary_like(redacted_text)
        if binary_like:
            evidence.append("output contains a high proportion of non-printable bytes")

        contains_traceback = bool(
            _TRACEBACK_PATTERN.search(redacted_text)
            or _JS_STACK_PATTERN.search(redacted_text)
        )
        contains_test_failure = any(
            p.search(redacted_text) for p in _TEST_FAILURE_PATTERNS
        )
        if contains_traceback:
            evidence.append("contains a stack trace")
        if contains_test_failure:
            evidence.append("contains test failure markers")

        summary: ToolResultSummary | None = None
        filtered_text = redacted_text
        if original_tokens > self.compact_threshold and not binary_like:
            summary = self._compact(redacted_text, original_tokens)
            filtered_text = summary.summary
            evidence.append(
                f"compacted {summary.original_token_estimate} -> "
                f"{summary.compressed_token_estimate} estimated tokens"
            )

        filtered_tokens = self._estimate(filtered_text)

        has_unsafe_redaction = any(
            t in _UNSAFE_REDACTION_TYPES for t in redaction.redaction_types
        )
        too_large = original_tokens > self.unsafe_size_threshold

        if has_unsafe_redaction or binary_like or too_large:
            status = "unsafe"
            suggested_action = "block"
            safe_for_context = False
        elif redaction.redaction_count > 0 or original_tokens > self.compact_threshold:
            status = "warn"
            suggested_action = "warn_user"
            # If the original carried any secret, the raw output is still
            # not safe for direct reinjection — only the redacted form is.
            safe_for_context = redaction.redaction_count == 0
        else:
            status = "safe"
            suggested_action = "allow"
            safe_for_context = True

        return ToolResultReview(
            status=status,
            evidence=tuple(evidence),
            redaction_count=redaction.redaction_count,
            redaction_types=tuple(redaction.redaction_types),
            original_token_estimate=original_tokens,
            filtered_token_estimate=filtered_tokens,
            safe_for_model_context=safe_for_context,
            suggested_action=suggested_action,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Compaction

    def _compact(self, text: str, original_tokens: int) -> ToolResultSummary:
        lines = text.splitlines()
        omitted = {
            "binary_blobs": 0,
            "duplicates": 0,
            "trimmed_middle_lines": 0,
        }
        retained_facts: list[str] = []

        # Pass 1: drop binary blobs and exact-duplicate non-empty lines.
        seen_counts: dict[str, int] = {}
        deduped: list[str] = []
        for line in lines:
            stripped = line.strip()
            if _BINARY_BLOB_LINE.match(stripped):
                omitted["binary_blobs"] += 1
                continue
            if stripped:
                seen_counts[stripped] = seen_counts.get(stripped, 0) + 1
                if seen_counts[stripped] > 1:
                    omitted["duplicates"] += 1
                    continue
            deduped.append(line)

        # Pass 2: identify "important" lines worth retaining mid-stream.
        important_indices: set[int] = set()
        for index, line in enumerate(deduped):
            for pattern in _IMPORTANT_LINE_PATTERNS:
                if pattern.search(line):
                    important_indices.add(index)
                    if len(retained_facts) < 50:
                        retained_facts.append(line.strip()[:200])
                    break

        keep_first = 30
        keep_last = 30

        if len(deduped) <= keep_first + keep_last:
            kept_lines = deduped
        else:
            head = deduped[:keep_first]
            tail = deduped[-keep_last:]
            middle_important = [
                deduped[i]
                for i in sorted(important_indices)
                if keep_first <= i < len(deduped) - keep_last
            ]
            head_set = set(range(keep_first))
            tail_set = set(range(len(deduped) - keep_last, len(deduped)))
            omitted["trimmed_middle_lines"] = max(
                0,
                len(deduped)
                - len(head)
                - len(tail)
                - len(
                    [i for i in important_indices if i not in head_set and i not in tail_set]
                ),
            )
            kept_lines = []
            seen: set[str] = set()
            for line in head + middle_important + tail:
                if line in seen:
                    continue
                seen.add(line)
                kept_lines.append(line)

        compressed = "\n".join(kept_lines)
        compressed_tokens = self._estimate(compressed)

        omitted_sections = tuple(
            f"{label}: {count}" for label, count in omitted.items() if count > 0
        )
        warnings: tuple[str, ...] = ()
        return ToolResultSummary(
            original_token_estimate=original_tokens,
            compressed_token_estimate=compressed_tokens,
            summary=compressed,
            retained_facts=tuple(retained_facts),
            omitted_sections=omitted_sections,
            warnings=warnings,
        )

    def _estimate(self, text: str) -> int:
        if not text:
            return 0
        count, _ = estimate_tokens(text, self.model)
        return count
