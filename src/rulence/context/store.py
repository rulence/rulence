"""Append-only JSONL governance index for context fragments.

Fragments live at::

    <root>/fragments.jsonl

Two record types share the same file:

* ``{"type": "fragment", ...}`` — one ``ContextFragment.to_dict()``.
* ``{"type": "expire", "fragment_id": ..., "reason": ..., "expired_at": ...}``
  — a tombstone marker. ``ContextStore.list`` and ``get`` honor
  tombstones; ``expire`` writes a new tombstone instead of mutating
  the prior fragment record.

This is intentionally not a real database. Memory governance and
provenance live elsewhere (Honcho, MemPalace). The store exists so a
runtime audit can replay which fragments informed a session and so
:class:`ContextSnapshot` ids resolve back to fragment dicts.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .fragment import ContextFragment

DEFAULT_FRAGMENT_FILE = "fragments.jsonl"


def default_context_dir() -> Path:
    configured = os.environ.get("RULENCE_CONTEXT_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".rulence" / "context"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_'-]+")


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_PATTERN.findall(text or "")}


class ContextStore:
    """Append-only JSONL governance index for context fragments.

    The store is process-local, file-based, and intentionally simple.
    Reads scan the JSONL on demand. A small in-process lock guards
    appends so concurrent calls inside one process do not interleave
    JSON lines; cross-process ordering is not guaranteed.
    """

    def __init__(
        self,
        directory: str | Path | None = None,
        *,
        filename: str = DEFAULT_FRAGMENT_FILE,
    ) -> None:
        self.directory = (
            Path(directory).expanduser() if directory else default_context_dir()
        )
        self.path = self.directory / filename
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Mutations

    def add(self, fragment: ContextFragment) -> ContextFragment:
        if not isinstance(fragment, ContextFragment):
            raise TypeError("ContextStore.add expects a ContextFragment")
        record = {"type": "fragment", **fragment.to_dict()}
        self._append(record)
        return fragment

    def expire(self, fragment_id: str, reason: str) -> bool:
        """Append a tombstone for ``fragment_id``.

        Returns True if a fragment with this id exists at the time of
        the call (before the tombstone is added). The tombstone is
        appended unconditionally so a later replay can show the
        expiration attempt and its reason.
        """
        existed = self._raw_get(fragment_id) is not None
        record = {
            "type": "expire",
            "fragment_id": fragment_id,
            "reason": reason,
            "expired_at": _now_iso(),
        }
        self._append(record)
        return existed

    # ------------------------------------------------------------------
    # Lookups

    def get(self, fragment_id: str) -> ContextFragment | None:
        """Return the fragment with this id if present and not expired."""
        if fragment_id in self._expired_ids():
            return None
        return self._raw_get(fragment_id)

    def list(
        self,
        filters: dict[str, Any] | None = None,
        *,
        include_expired: bool = False,
    ) -> list[ContextFragment]:
        return list(
            self._iter_fragments(filters=filters, include_expired=include_expired)
        )

    def by_corr_id(self, corr_id: str) -> list[ContextFragment]:
        return self.list({"corr_id": corr_id})

    def by_session_id(self, session_id: str) -> list[ContextFragment]:
        return self.list({"session_id": session_id})

    def by_memory_backend(self, backend: str) -> list[ContextFragment]:
        return self.list({"memory_backend": backend})

    def search_lexical(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        *,
        limit: int = 20,
    ) -> list[ContextFragment]:
        """Return fragments whose redacted_text overlaps the query terms.

        This is plain bag-of-words overlap. There are no embeddings,
        no semantic similarity, no ranking model — only deterministic
        token intersection. Score ties break on insertion order.
        """
        terms = _tokenize(query)
        if not terms:
            return []
        scored: list[tuple[int, int, ContextFragment]] = []
        for index, fragment in enumerate(
            self._iter_fragments(filters=filters, include_expired=False)
        ):
            tokens = _tokenize(fragment.redacted_text or fragment.text or "")
            score = len(terms & tokens)
            if score:
                scored.append((score, -index, fragment))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [fragment for _, _, fragment in scored[:limit]]

    # ------------------------------------------------------------------
    # Internals

    def _iter_fragments(
        self,
        *,
        filters: dict[str, Any] | None,
        include_expired: bool,
    ) -> Iterable[ContextFragment]:
        expired = set() if include_expired else self._expired_ids()
        predicate = _build_filter_predicate(filters or {})
        for fragment in self._iter_all_fragments():
            if not include_expired and fragment.fragment_id in expired:
                continue
            if predicate(fragment):
                yield fragment

    def _raw_get(self, fragment_id: str) -> ContextFragment | None:
        last: ContextFragment | None = None
        for fragment in self._iter_all_fragments():
            if fragment.fragment_id == fragment_id:
                last = fragment
        return last

    def _iter_all_fragments(self) -> Iterable[ContextFragment]:
        for record in self._iter_records():
            if record.get("type") != "fragment":
                continue
            payload = {k: v for k, v in record.items() if k != "type"}
            try:
                yield ContextFragment.from_dict(payload)
            except (ValueError, TypeError):
                continue

    def _expired_ids(self) -> set[str]:
        ids: set[str] = set()
        for record in self._iter_records():
            if record.get("type") == "expire":
                fid = record.get("fragment_id")
                if isinstance(fid, str):
                    ids.add(fid)
        return ids

    def _iter_records(self) -> Iterable[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
        return records

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, sort_keys=True)
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def _build_filter_predicate(
    filters: dict[str, Any],
) -> Callable[[ContextFragment], bool]:
    if not filters:
        return lambda _fragment: True

    def matches(fragment: ContextFragment) -> bool:
        for key, expected in filters.items():
            if not _matches_field(fragment, key, expected):
                return False
        return True

    return matches


def _matches_field(fragment: ContextFragment, key: str, expected: Any) -> bool:
    if key == "kind":
        return fragment.kind == expected
    if key == "memory_backend":
        return fragment.memory_backend == expected
    if key == "sensitivity":
        return fragment.sensitivity == expected
    if key == "corr_id":
        return fragment.source.corr_id == expected
    if key == "session_id":
        return fragment.source.session_id == expected
    if key == "runner":
        return fragment.source.runner == expected
    if key == "tool_name":
        return fragment.source.tool_name == expected
    if key == "event_id":
        return fragment.source.event_id == expected
    if key == "source_type":
        return fragment.source.source_type == expected
    if key == "fragment_id":
        return fragment.fragment_id == expected
    # Unknown filter keys never match: callers should pick from the
    # documented set rather than mining metadata blindly.
    return False
