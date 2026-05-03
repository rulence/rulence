"""Reproducible local evals for Rulence claims.

Evals are tiny deterministic benchmark cases that exercise governance,
memory routing, context assembly, token visibility, and cross-agent
handoff. They are intentionally small, local, and dependency-free so
they can run on a laptop in under a second.

Each eval lives under ``evals/<category>/<name>.py`` (at the repo root,
not inside the package) and exposes one ``EVAL`` object plus a ``run``
function. The :class:`EvalRunner` discovers them, runs each one, and
produces a :class:`EvalReport` that downstream tools (the claim
verifier, dashboards, the CLI) can consume.

This module is the public surface; concrete eval cases ship in the
top-level ``evals/`` directory of the repository.
"""
from __future__ import annotations

from .runner import (
    DEFAULT_EVALS_ROOT,
    Eval,
    EvalReport,
    EvalResult,
    EvalRunner,
    default_evals_root,
    discover_evals,
    run_all,
)

__all__ = [
    "DEFAULT_EVALS_ROOT",
    "Eval",
    "EvalReport",
    "EvalResult",
    "EvalRunner",
    "default_evals_root",
    "discover_evals",
    "run_all",
]
