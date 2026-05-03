from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rulence.transcript import (
    MAX_TRANSCRIPT_BYTES,
    TranscriptTurn,
    parse_transcript_path,
    parse_transcript_text,
    transcript_to_text,
)


class TranscriptParserTests(unittest.TestCase):
    def test_empty_input_returns_no_turns(self) -> None:
        self.assertEqual(parse_transcript_text(""), ())
        self.assertEqual(parse_transcript_text("   "), ())

    def test_plain_text_becomes_single_user_turn(self) -> None:
        turns = parse_transcript_text("please debug the migration")

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].role, "user")
        self.assertEqual(turns[0].content, "please debug the migration")

    def test_jsonl_with_role_and_content_yields_turns_in_order(self) -> None:
        text = "\n".join(
            [
                json.dumps({"role": "user", "content": "plan a migration"}),
                json.dumps({"role": "assistant", "content": "what database?"}),
            ]
        )

        turns = parse_transcript_text(text)

        self.assertEqual([turn.role for turn in turns], ["user", "assistant"])
        self.assertEqual([turn.content for turn in turns], ["plan a migration", "what database?"])
        self.assertEqual([turn.index for turn in turns], [0, 1])

    def test_jsonl_with_content_blocks_extracts_text_only(self) -> None:
        text = json.dumps(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "id": "abc"},
                    {"type": "text", "text": "world"},
                ],
            }
        )

        turns = parse_transcript_text(text)

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].content, "hello\nworld")

    def test_claude_code_nested_message_form_is_unwrapped(self) -> None:
        text = json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": "deploy this"},
            }
        )

        turns = parse_transcript_text(text)

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].role, "user")
        self.assertEqual(turns[0].content, "deploy this")

    def test_malformed_jsonl_falls_back_to_plain_text(self) -> None:
        turns = parse_transcript_text("{not json\nstill not json")

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].role, "user")
        self.assertIn("not json", turns[0].content)

    def test_jsonl_with_only_unrecognized_payloads_returns_empty(self) -> None:
        text = json.dumps({"type": "tool_use", "id": "abc"})

        turns = parse_transcript_text(text)

        self.assertEqual(turns, ())

    def test_parse_transcript_path_reads_file(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write(json.dumps({"role": "user", "content": "hello"}))
            path = Path(handle.name)
        try:
            turns = parse_transcript_path(path)
        finally:
            path.unlink()

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].content, "hello")

    def test_parse_transcript_path_rejects_oversized_file(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("x" * (MAX_TRANSCRIPT_BYTES + 1))
            path = Path(handle.name)
        try:
            with self.assertRaises(ValueError) as ctx:
                parse_transcript_path(path)
        finally:
            path.unlink()

        self.assertIn("MAX_TRANSCRIPT_BYTES", str(ctx.exception))

    def test_parse_transcript_path_raises_when_missing(self) -> None:
        with self.assertRaises(FileNotFoundError):
            parse_transcript_path("/this/path/does/not/exist/transcript.jsonl")

    def test_transcript_to_text_renders_role_prefix(self) -> None:
        turns = (
            TranscriptTurn(index=0, role="user", content="hi"),
            TranscriptTurn(index=1, role="assistant", content="hello"),
        )

        rendered = transcript_to_text(turns)

        self.assertIn("[user] hi", rendered)
        self.assertIn("[assistant] hello", rendered)


if __name__ == "__main__":
    unittest.main()
