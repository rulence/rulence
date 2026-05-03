from __future__ import annotations

import json
import unittest

from rulence.context import (
    KNOWN_FRAGMENT_KINDS,
    ContextFragment,
    SourceRef,
    hash_text,
)


def _source(**overrides) -> SourceRef:
    defaults = dict(
        source_type="user_prompt",
        runner="claude_code",
        corr_id="ct_corr",
        session_id="rs_session",
    )
    defaults.update(overrides)
    return SourceRef(**defaults)


class ContextFragmentTests(unittest.TestCase):
    def test_known_kinds_cover_spec_set(self) -> None:
        for required in (
            "user_instruction",
            "tool_input",
            "tool_result",
            "honcho_memory",
            "mempalace_memory",
            "constraint",
            "decision",
            "final_response",
            "audit_observation",
        ):
            self.assertIn(required, KNOWN_FRAGMENT_KINDS)

    def test_build_assigns_fragment_id_and_hash(self) -> None:
        fragment = ContextFragment.build(
            kind="user_instruction",
            text="forbids: secrets -> logs",
            source=_source(),
        )
        self.assertTrue(fragment.fragment_id.startswith("frag_"))
        self.assertTrue(fragment.content_hash.startswith("sha256:"))
        # The default redacted_text equals text and hash is over redacted_text.
        self.assertEqual(fragment.redacted_text, fragment.text)
        self.assertEqual(fragment.content_hash, hash_text(fragment.redacted_text))

    def test_build_rejects_empty_kind(self) -> None:
        with self.assertRaises(ValueError):
            ContextFragment.build(kind="", text="foo", source=_source())

    def test_build_rejects_non_string_text(self) -> None:
        with self.assertRaises(ValueError):
            ContextFragment.build(
                kind="user_instruction",
                text=123,  # type: ignore[arg-type]
                source=_source(),
            )

    def test_to_dict_round_trips_through_json(self) -> None:
        fragment = ContextFragment.build(
            kind="tool_input",
            text="bash: rm -rf /tmp/x",
            source=_source(
                source_type="tool_input",
                tool_name="Bash",
            ),
            metadata={"tool_name": "Bash"},
            sensitivity="medium",
            confidence=0.9,
        )
        encoded = json.dumps(fragment.to_dict())
        decoded = ContextFragment.from_dict(json.loads(encoded))
        self.assertEqual(decoded.fragment_id, fragment.fragment_id)
        self.assertEqual(decoded.kind, fragment.kind)
        self.assertEqual(decoded.text, fragment.text)
        self.assertEqual(decoded.redacted_text, fragment.redacted_text)
        self.assertEqual(decoded.content_hash, fragment.content_hash)
        self.assertEqual(decoded.source.tool_name, "Bash")
        self.assertEqual(decoded.metadata["tool_name"], "Bash")
        self.assertEqual(decoded.sensitivity, "medium")
        self.assertAlmostEqual(decoded.confidence, 0.9)

    def test_from_dict_requires_core_fields(self) -> None:
        with self.assertRaises(ValueError):
            ContextFragment.from_dict({"text": "abc"})
        with self.assertRaises(ValueError):
            ContextFragment.from_dict(
                {
                    "fragment_id": "frag_xyz",
                    "kind": "user_instruction",
                    "text": "abc",
                    # missing 'source'
                }
            )

    def test_content_hash_is_stable_for_equal_redacted_text(self) -> None:
        a = ContextFragment.build(
            kind="user_instruction",
            text="raw text",
            redacted_text="redacted",
            source=_source(),
        )
        b = ContextFragment.build(
            kind="user_instruction",
            text="totally different raw text",
            redacted_text="redacted",
            source=_source(corr_id="ct_other"),
        )
        # Different fragment_ids but same redacted_text means same hash.
        self.assertNotEqual(a.fragment_id, b.fragment_id)
        self.assertEqual(a.content_hash, b.content_hash)

    def test_token_estimate_is_non_negative_integer(self) -> None:
        fragment = ContextFragment.build(
            kind="user_instruction",
            text="hello world " * 20,
            source=_source(),
        )
        self.assertIsInstance(fragment.token_estimate, int)
        self.assertGreaterEqual(fragment.token_estimate, 0)

    def test_source_ref_round_trip(self) -> None:
        source = SourceRef(
            source_type="memory_item",
            memory_backend="honcho",
            corr_id="c",
            session_id="s",
            runner="claude_code",
            content_hash="sha256:abc",
        )
        round_tripped = SourceRef.from_dict(source.to_dict())
        self.assertEqual(round_tripped, source)


if __name__ == "__main__":
    unittest.main()
