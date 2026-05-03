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

from .assembler import AssemblyResult, ContextAssembler
from .budgeter import ContextBudgetResult, ContextBudgeter, is_required_by_default
from .bus import AgentContextBus, TaskNotFoundError, default_tasks_dir
from .compressor import CompressedContext, ContextCompressor
from .conflicts import Conflict, ConflictReport, detect_conflicts
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
from .relevance import (
    BACKEND_ROLE_WEIGHTS,
    KIND_WEIGHTS,
    RelevanceContext,
    RelevanceScore,
    RelevanceScorer,
)
from .snapshot import ContextSnapshot, snapshot_content_hash
from .store import ContextStore, default_context_dir
from .task_state import (
    TASK_STATE_VERSION,
    Blocker,
    Decision,
    TaskState,
    new_task_id,
)

__all__ = [
    "AgentContextBus",
    "AssemblyResult",
    "BACKEND_ROLE_WEIGHTS",
    "Blocker",
    "CompressedContext",
    "Conflict",
    "ConflictReport",
    "ContextAssembler",
    "ContextBudgetResult",
    "ContextBudgeter",
    "ContextCompressor",
    "ContextFragment",
    "ContextSnapshot",
    "ContextStore",
    "Decision",
    "KIND_WEIGHTS",
    "KNOWN_FRAGMENT_KINDS",
    "RelevanceContext",
    "RelevanceScore",
    "RelevanceScorer",
    "SourceRef",
    "TASK_STATE_VERSION",
    "TaskNotFoundError",
    "TaskState",
    "default_context_dir",
    "default_tasks_dir",
    "detect_conflicts",
    "fragments_from_audit_event",
    "fragments_from_final_response",
    "fragments_from_honcho_memory_item",
    "fragments_from_mempalace_memory_item",
    "fragments_from_tool_input",
    "fragments_from_tool_result",
    "fragments_from_user_prompt",
    "hash_text",
    "is_required_by_default",
    "new_fragment_id",
    "new_task_id",
    "snapshot_content_hash",
]
