"""Build :class:`ContextFragment` objects from runtime and memory events.

Each extractor takes a single source record and returns a tuple of
fragments. Extractors run secrets through :func:`redact_text` before
the fragment is built so the persisted ``redacted_text`` and the
``content_hash`` derived from it are safe to keep around.

Constraint extraction reuses :func:`rulence.logic.parse_constraints`
so the deterministic ``forbids:`` / ``requires:`` / ``X must Y``
patterns produce extra ``constraint`` fragments alongside the parent.
Decisions are extracted only on a tight deterministic pattern (``decision: ...``)
to avoid noisy false positives.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..logic import parse_constraints
from ..security import redact_text
from .fragment import ContextFragment, SourceRef


_DECISION_LINE = re.compile(
    r"^\s*(?:decision|chose|chosen|selected|picked)\s*[:\-]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _redacted(text: str) -> str:
    """Apply the secret redactor and return the redacted text only."""
    if not text:
        return text or ""
    try:
        return redact_text(text).redacted_text
    except Exception:
        return text


def _short(text: str, limit: int = 240) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _constraint_fragments(
    parent_text: str,
    base_source: SourceRef,
    *,
    parent_kind: str,
    runner: str | None = None,
    corr_id: str | None = None,
    session_id: str | None = None,
) -> list[ContextFragment]:
    fragments: list[ContextFragment] = []
    for index, constraint in enumerate(parse_constraints(parent_text or "")):
        text = (
            f"{constraint.kind}({constraint.condition}, {constraint.target})"
        )
        source = SourceRef(
            source_type="constraint_parser",
            source_id=base_source.source_id,
            runner=runner or base_source.runner,
            event_id=base_source.event_id,
            corr_id=corr_id or base_source.corr_id,
            session_id=session_id or base_source.session_id,
            tool_name=base_source.tool_name,
            memory_backend=base_source.memory_backend,
            content_hash=base_source.content_hash,
        )
        fragments.append(
            ContextFragment.build(
                kind="constraint",
                text=text,
                redacted_text=text,
                source=source,
                metadata={
                    "constraint_kind": constraint.kind,
                    "constraint_condition": constraint.condition,
                    "constraint_target": constraint.target,
                    "parent_kind": parent_kind,
                    "constraint_index": index,
                },
            )
        )
    return fragments


def _decision_fragments(
    parent_text: str,
    base_source: SourceRef,
    *,
    parent_kind: str,
) -> list[ContextFragment]:
    fragments: list[ContextFragment] = []
    for match in _DECISION_LINE.finditer(parent_text or ""):
        decision = match.group(1).strip()
        if not decision:
            continue
        red = _redacted(decision)
        source = SourceRef(
            source_type="decision_parser",
            source_id=base_source.source_id,
            runner=base_source.runner,
            event_id=base_source.event_id,
            corr_id=base_source.corr_id,
            session_id=base_source.session_id,
            tool_name=base_source.tool_name,
            memory_backend=base_source.memory_backend,
            content_hash=base_source.content_hash,
        )
        fragments.append(
            ContextFragment.build(
                kind="decision",
                text=decision,
                redacted_text=red,
                source=source,
                metadata={"parent_kind": parent_kind},
            )
        )
    return fragments


# ----------------------------------------------------------------------
# Public extractors


def fragments_from_user_prompt(
    prompt: str,
    *,
    runner: str | None = None,
    session_id: str | None = None,
    corr_id: str | None = None,
    event_id: str | None = None,
    sensitivity: str = "low",
    metadata: dict[str, Any] | None = None,
) -> tuple[ContextFragment, ...]:
    if not isinstance(prompt, str) or not prompt.strip():
        return ()
    redacted = _redacted(prompt)
    source = SourceRef(
        source_type="user_prompt",
        runner=runner,
        event_id=event_id,
        corr_id=corr_id,
        session_id=session_id,
    )
    parent = ContextFragment.build(
        kind="user_instruction",
        text=prompt,
        redacted_text=redacted,
        source=source,
        sensitivity=sensitivity,
        metadata=dict(metadata or {}),
    )
    fragments: list[ContextFragment] = [parent]
    fragments.extend(
        _constraint_fragments(
            prompt,
            source,
            parent_kind="user_instruction",
            runner=runner,
            corr_id=corr_id,
            session_id=session_id,
        )
    )
    return tuple(fragments)


def fragments_from_tool_input(
    tool_name: str | None,
    tool_input: dict[str, Any] | str | None,
    *,
    runner: str | None = None,
    session_id: str | None = None,
    corr_id: str | None = None,
    event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[ContextFragment, ...]:
    if tool_input is None:
        return ()
    rendered = _render_tool_payload(tool_input)
    if not rendered.strip():
        return ()
    redacted = _redacted(rendered)
    source = SourceRef(
        source_type="tool_input",
        runner=runner,
        tool_name=tool_name,
        event_id=event_id,
        corr_id=corr_id,
        session_id=session_id,
    )
    fragment = ContextFragment.build(
        kind="tool_input",
        text=rendered,
        redacted_text=redacted,
        source=source,
        metadata={"tool_name": tool_name, **(metadata or {})},
    )
    return (fragment,)


def fragments_from_tool_result(
    tool_name: str | None,
    tool_output: Any,
    *,
    runner: str | None = None,
    session_id: str | None = None,
    corr_id: str | None = None,
    event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[ContextFragment, ...]:
    if tool_output is None:
        return ()
    rendered = _render_tool_payload(tool_output)
    if not rendered.strip():
        return ()
    redacted = _redacted(rendered)
    source = SourceRef(
        source_type="tool_result",
        runner=runner,
        tool_name=tool_name,
        event_id=event_id,
        corr_id=corr_id,
        session_id=session_id,
    )
    fragment = ContextFragment.build(
        kind="tool_result",
        text=rendered,
        redacted_text=redacted,
        source=source,
        metadata={"tool_name": tool_name, **(metadata or {})},
    )
    return (fragment,)


def fragments_from_honcho_memory_item(
    item: Any,
    *,
    runner: str | None = None,
    session_id: str | None = None,
    corr_id: str | None = None,
) -> tuple[ContextFragment, ...]:
    return _fragments_from_memory_item(
        item,
        backend="honcho",
        kind="honcho_memory",
        runner=runner,
        session_id=session_id,
        corr_id=corr_id,
    )


def fragments_from_mempalace_memory_item(
    item: Any,
    *,
    runner: str | None = None,
    session_id: str | None = None,
    corr_id: str | None = None,
) -> tuple[ContextFragment, ...]:
    return _fragments_from_memory_item(
        item,
        backend="mempalace",
        kind="mempalace_memory",
        runner=runner,
        session_id=session_id,
        corr_id=corr_id,
    )


def fragments_from_final_response(
    final_response: str,
    *,
    runner: str | None = None,
    session_id: str | None = None,
    corr_id: str | None = None,
    event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[ContextFragment, ...]:
    if not isinstance(final_response, str) or not final_response.strip():
        return ()
    redacted = _redacted(final_response)
    source = SourceRef(
        source_type="final_response",
        runner=runner,
        event_id=event_id,
        corr_id=corr_id,
        session_id=session_id,
    )
    parent = ContextFragment.build(
        kind="final_response",
        text=final_response,
        redacted_text=redacted,
        source=source,
        metadata=dict(metadata or {}),
    )
    fragments: list[ContextFragment] = [parent]
    fragments.extend(
        _decision_fragments(
            final_response, source, parent_kind="final_response"
        )
    )
    return tuple(fragments)


def fragments_from_audit_event(
    event: dict[str, Any],
) -> tuple[ContextFragment, ...]:
    if not isinstance(event, dict):
        return ()
    summary_parts: list[str] = []
    for key in ("event_type", "decision", "tool_name", "policy_name"):
        value = event.get(key)
        if value:
            summary_parts.append(f"{key}={value}")
    evidence = event.get("evidence")
    if isinstance(evidence, (list, tuple)) and evidence:
        summary_parts.append("evidence=" + "; ".join(str(e) for e in evidence))
    if not summary_parts:
        return ()
    text = " ".join(summary_parts)
    redacted = _redacted(text)
    source = SourceRef(
        source_type="audit_event",
        source_id=_opt_str(event.get("event_id")),
        runner=_opt_str(event.get("runner")),
        event_id=_opt_str(event.get("event_id")),
        corr_id=_opt_str(event.get("corr_id")),
        session_id=_opt_str(event.get("session_id")),
        tool_name=_opt_str(event.get("tool_name")),
        version=_opt_str(event.get("schema_version")),
        content_hash=_opt_str(event.get("raw_payload_hash")),
    )
    fragment = ContextFragment.build(
        kind="audit_observation",
        text=text,
        redacted_text=redacted,
        source=source,
        metadata={
            "event_type": event.get("event_type"),
            "decision": event.get("decision"),
        },
    )
    return (fragment,)


# ----------------------------------------------------------------------
# Helpers


def _fragments_from_memory_item(
    item: Any,
    *,
    backend: str,
    kind: str,
    runner: str | None,
    session_id: str | None,
    corr_id: str | None,
) -> tuple[ContextFragment, ...]:
    text, score, item_metadata = _memory_item_fields(item)
    if not text.strip():
        return ()
    redacted = _redacted(text)
    source = SourceRef(
        source_type="memory_item",
        runner=runner,
        corr_id=corr_id,
        session_id=session_id,
        memory_backend=backend,
    )
    metadata = {"score": score}
    if item_metadata:
        metadata["item_metadata"] = item_metadata
    fragment = ContextFragment.build(
        kind=kind,
        text=text,
        redacted_text=redacted,
        source=source,
        memory_backend=backend,
        metadata=metadata,
    )
    return (fragment,)


def _memory_item_fields(item: Any) -> tuple[str, int, dict[str, Any]]:
    if hasattr(item, "to_dict") and callable(item.to_dict):
        data = item.to_dict()
    elif isinstance(item, dict):
        data = item
    else:
        return (str(item or ""), 0, {})
    text = str(data.get("text") or data.get("content") or "")
    try:
        score = int(data.get("score", 0) or 0)
    except (TypeError, ValueError):
        score = 0
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return (text, score, metadata)


def _render_tool_payload(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (dict, list, tuple)):
        try:
            return json.dumps(payload, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return str(payload)
    return str(payload)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
