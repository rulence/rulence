"""Tests that synthetic secrets do not appear in newly written session,
trace, or feedback artifacts."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rulence.feedback import default_feedback_path, read_feedback, record_feedback
from rulence.reasoning import start_session
from rulence.sessions import SessionStore, redacted_session_dict
from rulence.trace_html import render_trace_html

# Synthetic fakes (NEVER replace with real values).
FAKE_GITHUB_TOKEN = "ghp_FAKEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
FAKE_OPENAI_KEY = "sk-FAKE0000000000000000"
FAKE_DB_URL = "postgres://admin:FAKEpass@db.example.com:5432/app"


class SessionStorageRedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.session_dir = Path(self._tmp.name)
        self.store = SessionStore(self.session_dir)

    def test_session_to_dict_helper_redacts(self) -> None:
        session = start_session(
            f"investigate this {FAKE_GITHUB_TOKEN}",
            memory=f"prior context: {FAKE_OPENAI_KEY}",
            session_id="redact-1",
        )
        d = redacted_session_dict(session)
        flattened = json.dumps(d)
        self.assertNotIn(FAKE_GITHUB_TOKEN, flattened)
        self.assertNotIn(FAKE_OPENAI_KEY, flattened)
        self.assertIn("[REDACTED:github_token]", flattened)
        self.assertIn("[REDACTED:openai_key]", flattened)

    def test_session_store_save_writes_redacted_json(self) -> None:
        session = start_session(
            f"connect to {FAKE_DB_URL}",
            session_id="redact-2",
        )
        path = self.store.save(session)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn(FAKE_DB_URL, text)
        self.assertIn("[REDACTED:db_url_with_password]", text)
        # Original session object is still intact in memory.
        self.assertIn(FAKE_DB_URL, session.task)


class TraceHtmlRedactionTests(unittest.TestCase):
    def test_trace_html_does_not_render_raw_secret(self) -> None:
        session = start_session(
            f"deploy with key {FAKE_GITHUB_TOKEN}",
            memory=f"earlier mention: {FAKE_OPENAI_KEY}",
            session_id="trace-redact-1",
        )
        html = render_trace_html(session)
        self.assertNotIn(FAKE_GITHUB_TOKEN, html)
        self.assertNotIn(FAKE_OPENAI_KEY, html)
        self.assertIn("[REDACTED:github_token]", html)


class FeedbackRedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "feedback.jsonl"

    def test_record_feedback_redacts_task_and_notes(self) -> None:
        record = record_feedback(
            task=f"investigate {FAKE_DB_URL}",
            outcome="false_positive",
            verdict="warn",
            notes=f"context dump: {FAKE_GITHUB_TOKEN}",
            path=self.path,
        )
        self.assertNotIn(FAKE_DB_URL, record["task"])
        self.assertNotIn(FAKE_GITHUB_TOKEN, record["notes"])
        self.assertIn("[REDACTED:db_url_with_password]", record["task"])
        self.assertIn("[REDACTED:github_token]", record["notes"])
        self.assertGreaterEqual(record["redaction_count"], 2)
        self.assertIn("db_url_with_password", record["redaction_types"])
        self.assertIn("github_token", record["redaction_types"])

    def test_feedback_file_does_not_contain_raw_secret(self) -> None:
        record_feedback(
            task=f"scan token {FAKE_GITHUB_TOKEN}",
            outcome="correct",
            verdict="block",
            notes="",
            path=self.path,
        )
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn(FAKE_GITHUB_TOKEN, text)
        self.assertIn("[REDACTED:github_token]", text)

    def test_read_feedback_round_trip_after_redaction(self) -> None:
        record_feedback(
            task=f"task with {FAKE_OPENAI_KEY}",
            outcome="correct",
            verdict=None,
            notes="",
            path=self.path,
        )
        records = read_feedback(self.path)
        self.assertEqual(len(records), 1)
        self.assertNotIn(FAKE_OPENAI_KEY, records[0]["task"])


if __name__ == "__main__":
    unittest.main()
