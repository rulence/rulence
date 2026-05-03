"""Tool-result review primitives.

PostToolUse review surface introduced in M5. The :class:`ToolResultReviewer`
classifies a tool's output (safe / warn / unsafe), runs the redactor over
it, and optionally compacts large outputs into a deterministic summary.

Important runtime contract: Claude Code's PostToolUse hook can observe
and append context, but it cannot replace the tool result the model sees.
This module therefore *detects*, *redacts for audit*, *warns*, and
*marks unsafe*. It does **not** prevent reinjection of the raw tool
result into model context.
"""
from __future__ import annotations

from .tool_result import (
    ToolResultReview,
    ToolResultReviewer,
    ToolResultSummary,
)

__all__ = ["ToolResultReview", "ToolResultReviewer", "ToolResultSummary"]
