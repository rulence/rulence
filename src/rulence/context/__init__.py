"""Rulence context intelligence primitives (M9).

Context storage in Rulence is for governance, selection, provenance,
snapshots, and budgeting. It does not replace Honcho or MemPalace —
those remain the canonical internal and external memory backends.
The :class:`ContextFragment` model captures discrete pieces of context
derived from audit events and from configured memory backends, the
:class:`ContextStore` indexes them for replay, and :class:`ContextSnapshot`
records the fragments that informed a particular task.

This milestone (M9) intentionally excludes embeddings, semantic search,
compression, and a multi-agent bus.
"""
from __future__ import annotations

from .extract import (
    fragments_from_audit_event,
    fragments_from_final_response,
    fragments_from_honcho_memory_item,
    fragments_from_mempalace_memory_item,
    fragments_from_tool_input,
    fragments_from_tool_result,
    fragments_from_user_prompt,
)
from .fragment import (
    KNOWN_FRAGMENT_KINDS,
    ContextFragment,
    SourceRef,
    hash_text,
    new_fragment_id,
)
from .snapshot import ContextSnapshot, snapshot_content_hash
from .store import ContextStore, default_context_dir

__all__ = [
    "ContextFragment",
    "ContextSnapshot",
    "ContextStore",
    "KNOWN_FRAGMENT_KINDS",
    "SourceRef",
    "default_context_dir",
    "fragments_from_audit_event",
    "fragments_from_final_response",
    "fragments_from_honcho_memory_item",
    "fragments_from_mempalace_memory_item",
    "fragments_from_tool_input",
    "fragments_from_tool_result",
    "fragments_from_user_prompt",
    "hash_text",
    "new_fragment_id",
    "snapshot_content_hash",
]
