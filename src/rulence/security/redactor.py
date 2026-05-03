"""Best-effort secret detection and redaction.

The detectors below are tuned for high precision: each one matches a
recognizable structural format (a known prefix, a fixed length, a
keyword-anchored ``KEY=VALUE`` form). They will miss low-entropy custom
tokens; they will not redact ordinary source code identifiers.

This module is intentionally pure-Python and dependency-free so it can
run inside hook handlers without import overhead.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RedactionResult:
    redacted_text: str
    redaction_count: int
    redaction_types: tuple[str, ...]
    fingerprints: tuple[str, ...]


# (type_name, compiled_pattern) — order matters: more specific patterns
# run first so the most informative type label wins for any given match.
DEFAULT_DETECTORS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY-----"
            r"[\s\S]*?"
            r"-----END (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY-----"
        ),
    ),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{82,}")),
    ("github_token", re.compile(r"gh[psour]_[A-Za-z0-9]{36,255}")),
    (
        "anthropic_key",
        re.compile(r"sk-ant-(?:api03-)?[A-Za-z0-9_\-]{20,}"),
    ),
    ("openai_project_key", re.compile(r"sk-proj-[A-Za-z0-9_\-]{40,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]+")),
    (
        "aws_access_key_id",
        re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASCA)[A-Z0-9]{16}\b"),
    ),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}")),
    (
        "db_url_with_password",
        re.compile(
            r"(?i)(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|mssql|clickhouse)://"
            r"[^:/\s@]+:[^@/\s]+@[^\s'\"]+"
        ),
    ),
    (
        "basic_auth_url",
        re.compile(r"(?i)https?://[^:/\s@]+:[^@/\s]+@[^\s'\"]+"),
    ),
    # Last: a keyword-anchored KEY=value form. Specific token detectors
    # above run first so a real GitHub/AWS/etc. token already gets the
    # right type label even when it appears inside a KEY=value pair.
    (
        "env_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?access[_-]?key|access[_-]?key|"
            r"secret(?:[_-]?key)?|password|passwd|auth[_-]?token|token|"
            r"credentials?|private[_-]?key|client[_-]?secret)\s*[=:]\s*"
            r"([\"']?)([A-Za-z0-9_\-./+=]{8,})\1"
        ),
    ),
)


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


class SecretRedactor:
    def __init__(
        self,
        detectors: Iterable[tuple[str, "re.Pattern[str]"]] | None = None,
    ) -> None:
        self.detectors = tuple(detectors) if detectors is not None else DEFAULT_DETECTORS

    def redact_text(self, text: str) -> RedactionResult:
        if not isinstance(text, str) or not text:
            return RedactionResult(text or "", 0, (), ())

        types: list[str] = []
        fingerprints: list[str] = []
        new_text = text

        for type_name, pattern in self.detectors:

            def _replace(match: "re.Match[str]", t: str = type_name) -> str:
                if t == "env_secret":
                    full = match.group(0)
                    value = match.group(2)
                    types.append(t)
                    fingerprints.append(_fingerprint(value))
                    return full.replace(value, f"[REDACTED:{t}]")
                full = match.group(0)
                types.append(t)
                fingerprints.append(_fingerprint(full))
                return f"[REDACTED:{t}]"

            new_text = pattern.sub(_replace, new_text)

        return RedactionResult(
            redacted_text=new_text,
            redaction_count=len(types),
            redaction_types=tuple(types),
            fingerprints=tuple(fingerprints),
        )

    def redact_payload(
        self, payload: Any
    ) -> tuple[Any, int, tuple[str, ...]]:
        """Recursively redact strings in ``payload`` without mutating it.

        Returns ``(redacted_payload, redaction_count, redaction_types)``.
        Non-string, non-container values pass through unchanged.
        """
        types: list[str] = []

        def _walk(value: Any) -> Any:
            if isinstance(value, str):
                result = self.redact_text(value)
                types.extend(result.redaction_types)
                return result.redacted_text
            if isinstance(value, dict):
                return {k: _walk(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_walk(v) for v in value]
            if isinstance(value, tuple):
                return tuple(_walk(v) for v in value)
            return value

        new_payload = _walk(payload)
        return new_payload, len(types), tuple(types)


_DEFAULT_REDACTOR = SecretRedactor()


def redact_text(text: str) -> RedactionResult:
    return _DEFAULT_REDACTOR.redact_text(text)


def redact_payload(payload: Any) -> tuple[Any, int, tuple[str, ...]]:
    return _DEFAULT_REDACTOR.redact_payload(payload)
