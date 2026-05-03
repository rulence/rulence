"""Rulence security primitives.

Public surface intentionally small:

* :class:`SecretRedactor` — pattern-based detector that replaces likely
  secrets with typed placeholders and returns a fingerprint summary.
* :class:`RedactionResult` — value type returned by ``redact_text``.
* :func:`redact_text` / :func:`redact_payload` — module-level helpers
  using a default :class:`SecretRedactor` instance.

Redaction is **best-effort** and is not a complete DLP system. It is
designed to keep common secret formats out of audit, session, trace,
and feedback storage. It does not promise to catch every possible
secret, especially low-entropy custom tokens.
"""
from __future__ import annotations

from .redactor import (
    DEFAULT_DETECTORS,
    RedactionResult,
    SecretRedactor,
    redact_payload,
    redact_text,
)

__all__ = [
    "DEFAULT_DETECTORS",
    "RedactionResult",
    "SecretRedactor",
    "redact_payload",
    "redact_text",
]
