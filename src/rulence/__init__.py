"""Rulence MVP: local-first reasoning governance for agents."""

from .classifier import classify_task
from .context import ContextFragment, ContextSnapshot, ContextStore, SourceRef
from .preflight import preflight_task
from .reasoning import add_thought, start_session

__all__ = [
    "ContextFragment",
    "ContextSnapshot",
    "ContextStore",
    "SourceRef",
    "add_thought",
    "classify_task",
    "preflight_task",
    "start_session",
]

__version__ = "0.3.0"
