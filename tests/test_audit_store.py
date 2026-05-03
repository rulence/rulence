from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rulence.audit import AuditTraceStore, RulenceAuditEvent
from rulence.audit.events import SCHEMA_VERSION


def _event(**overrides) -> RulenceAuditEvent:
    defaults = dict(
        event_type="PreToolUse",
        session_id="rs_abc123",
        runner="claude_code",
        corr_id="ct_def456",
        decision="approve",
        evidence=("e1", "e2"),
        policy_name="builtin:tier-3-moderate",
        tool_name="Bash",
        redaction_count=0,
        token_estimate=42,
        payload_redacted=False,
        raw_payload_hash="0" * 64,
        metadata={"k": "v"},
    )
    defaults.update(overrides)
    return RulenceAuditEvent.now(**defaults)


class AuditTraceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = AuditTraceStore(self.root)

    def test_append_writes_one_jsonl_line(self) -> None:
        path = self.store.append(_event())
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line]
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["schema_version"], SCHEMA_VERSION)
        self.assertEqual(record["event_type"], "PreToolUse")
        self.assertEqual(record["evidence"], ["e1", "e2"])

    def test_two_appends_in_one_turn_share_a_file(self) -> None:
        self.store.append(_event(event_type="PreToolUse"))
        self.store.append(_event(event_type="PostToolUse"))
        records = self.store.read_turn("rs_abc123", "ct_def456")
        self.assertEqual(len(records), 2)
        types = [r["event_type"] for r in records]
        self.assertEqual(types, ["PreToolUse", "PostToolUse"])

    def test_no_corr_id_lands_in_no_turn_file(self) -> None:
        self.store.append(_event(corr_id=None, event_type="SessionStart"))
        path = self.root / "rs_abc123" / "_no_turn.jsonl"
        self.assertTrue(path.exists())

    def test_path_separators_are_sanitized(self) -> None:
        evil_event = _event(session_id="rs_../../etc", corr_id="ct_../bad")
        path = self.store.append(evil_event)
        # The recorded file lives below self.root, never above it.
        self.assertTrue(path.resolve().is_relative_to(self.root.resolve()))

    def test_read_session_aggregates_turns(self) -> None:
        self.store.append(_event(corr_id="ct_one", event_type="PreToolUse"))
        self.store.append(_event(corr_id="ct_two", event_type="PreToolUse"))
        records = self.store.read_session("rs_abc123")
        self.assertEqual(len(records), 2)

    def test_event_serialization_is_json_round_trip(self) -> None:
        event = _event(metadata={"nested": {"a": 1, "b": [1, 2, 3]}})
        path = self.store.append(event)
        record = json.loads(path.read_text(encoding="utf-8").strip())
        self.assertEqual(record["metadata"], {"nested": {"a": 1, "b": [1, 2, 3]}})


if __name__ == "__main__":
    unittest.main()
