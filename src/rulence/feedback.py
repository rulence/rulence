from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .security import redact_text


def default_feedback_path() -> Path:
    configured = os.environ.get("RULENCE_FEEDBACK_FILE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".rulence" / "feedback.jsonl"


def record_feedback(
    *,
    task: str,
    outcome: str,
    verdict: str | None = None,
    session_id: str | None = None,
    notes: str = "",
    path: str | Path | None = None,
) -> dict[str, Any]:
    # Redact at the feedback storage boundary so the JSONL never holds
    # raw secrets pasted into a task or note.
    redacted_task = redact_text(task)
    redacted_notes = redact_text(notes)
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task": redacted_task.redacted_text,
        "outcome": outcome,
        "verdict": verdict,
        "session_id": session_id,
        "notes": redacted_notes.redacted_text,
        "redaction_count": redacted_task.redaction_count + redacted_notes.redaction_count,
        "redaction_types": list(redacted_task.redaction_types) + list(redacted_notes.redaction_types),
    }
    target = Path(path).expanduser() if path else default_feedback_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def read_feedback(path: str | Path | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    target = Path(path).expanduser() if path else default_feedback_path()
    if not target.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    if limit is not None:
        return records[-limit:]
    return records


def summarize_feedback(path: str | Path | None = None) -> dict[str, Any]:
    records = read_feedback(path)
    by_outcome: dict[str, int] = {}
    by_verdict: dict[str, int] = {}
    for record in records:
        outcome = str(record.get("outcome") or "unknown")
        verdict = str(record.get("verdict") or "unknown")
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
    return {
        "count": len(records),
        "by_outcome": dict(sorted(by_outcome.items())),
        "by_verdict": dict(sorted(by_verdict.items())),
    }
