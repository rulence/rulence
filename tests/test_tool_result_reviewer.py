"""Unit tests for ToolResultReviewer.

All tool outputs here are synthetic. No real credentials are present.
"""
from __future__ import annotations

import unittest

from rulence.review import ToolResultReview, ToolResultReviewer, ToolResultSummary

# --- synthetic fakes ---------------------------------------------------

FAKE_GITHUB_TOKEN = "ghp_FAKEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
FAKE_OPENAI_KEY = "sk-FAKE0000000000000000"
FAKE_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE\n"
    "-----END PRIVATE KEY-----"
)


class SafeOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reviewer = ToolResultReviewer()

    def test_short_safe_text_is_safe(self) -> None:
        review = self.reviewer.review(tool_name="Read", tool_output="hello world")
        self.assertEqual(review.status, "safe")
        self.assertEqual(review.suggested_action, "allow")
        self.assertTrue(review.safe_for_model_context)
        self.assertEqual(review.redaction_count, 0)
        self.assertIsNone(review.summary)

    def test_dict_output_is_serialized_for_review(self) -> None:
        review = self.reviewer.review(
            tool_name="Read",
            tool_output={"ok": True, "rows": [1, 2, 3]},
        )
        self.assertEqual(review.status, "safe")
        self.assertGreater(review.original_token_estimate, 0)


class SecretLeakageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reviewer = ToolResultReviewer()

    def test_github_token_in_output_warns(self) -> None:
        review = self.reviewer.review(
            tool_name="Bash",
            tool_output=f"deploy log: token={FAKE_GITHUB_TOKEN}",
        )
        self.assertEqual(review.status, "warn")
        self.assertGreaterEqual(review.redaction_count, 1)
        self.assertIn("github_token", review.redaction_types)
        self.assertFalse(review.safe_for_model_context)

    def test_pem_block_makes_output_unsafe(self) -> None:
        review = self.reviewer.review(tool_name="Bash", tool_output=FAKE_PEM)
        self.assertEqual(review.status, "unsafe")
        self.assertEqual(review.suggested_action, "block")
        self.assertFalse(review.safe_for_model_context)
        self.assertIn("pem_private_key", review.redaction_types)


class LargeOutputCompactionTests(unittest.TestCase):
    def test_large_output_is_compacted_and_warned(self) -> None:
        reviewer = ToolResultReviewer(compact_threshold=100, unsafe_size_threshold=100_000)
        big = "\n".join(f"log line {i}" for i in range(2000))
        review = reviewer.review(tool_name="Bash", tool_output=big)
        self.assertIn(review.status, ("warn", "unsafe"))
        self.assertIsNotNone(review.summary)
        assert review.summary is not None  # for type checker
        self.assertLess(
            review.summary.compressed_token_estimate,
            review.summary.original_token_estimate,
        )
        self.assertTrue(review.summary.omitted_sections)
        self.assertIn("original_token_estimate", review.to_dict())

    def test_excessively_large_output_is_unsafe(self) -> None:
        reviewer = ToolResultReviewer(unsafe_size_threshold=100)
        review = reviewer.review(
            tool_name="Bash",
            tool_output="x " * 1000,  # easily over a 100-token threshold
        )
        self.assertEqual(review.status, "unsafe")
        self.assertEqual(review.suggested_action, "block")
        self.assertFalse(review.safe_for_model_context)


class BinaryOutputTests(unittest.TestCase):
    def test_binary_like_output_is_unsafe(self) -> None:
        reviewer = ToolResultReviewer()
        # Many high-byte and control characters above the 30% threshold.
        binary = "".join(chr(i % 256) for i in range(2000))
        review = reviewer.review(tool_name="Read", tool_output=binary)
        self.assertEqual(review.status, "unsafe")
        self.assertFalse(review.safe_for_model_context)
        self.assertIn(
            "output contains a high proportion of non-printable bytes",
            review.evidence,
        )


class StackTraceAndTestFailureTests(unittest.TestCase):
    def test_python_traceback_is_recorded_in_evidence(self) -> None:
        reviewer = ToolResultReviewer()
        traceback_output = (
            "Traceback (most recent call last):\n"
            "  File \"app.py\", line 10, in main\n"
            "    raise ValueError('boom')\n"
            "ValueError: boom\n"
        )
        review = reviewer.review(tool_name="Bash", tool_output=traceback_output)
        self.assertIn("contains a stack trace", review.evidence)

    def test_test_failure_lines_preserved_in_summary(self) -> None:
        reviewer = ToolResultReviewer(compact_threshold=10)
        long_passing = "\n".join(f"PASS test_{i}" for i in range(200))
        body = (
            long_passing
            + "\nFAILED tests/test_x.py::test_critical "
            + "AssertionError: expected 1 but got 2\n"
        )
        review = reviewer.review(tool_name="Bash", tool_output=body)
        self.assertIn("contains test failure markers", review.evidence)
        self.assertIsNotNone(review.summary)
        assert review.summary is not None
        joined = " ".join(review.summary.retained_facts) + " " + review.summary.summary
        self.assertIn("AssertionError", joined)


class TokenEstimateTests(unittest.TestCase):
    def test_token_estimates_are_present_in_review(self) -> None:
        reviewer = ToolResultReviewer()
        review = reviewer.review(tool_name="Bash", tool_output="some output")
        d = review.to_dict()
        self.assertIn("original_token_estimate", d)
        self.assertIn("filtered_token_estimate", d)
        self.assertIsInstance(d["original_token_estimate"], int)
        self.assertIsInstance(d["filtered_token_estimate"], int)


class ReviewSerializationTests(unittest.TestCase):
    def test_to_dict_round_trips_to_json(self) -> None:
        import json

        review = ToolResultReviewer().review(
            tool_name="Bash",
            tool_output="hello",
        )
        encoded = json.dumps(review.to_dict())
        decoded = json.loads(encoded)
        self.assertEqual(decoded["status"], review.status)
        self.assertEqual(decoded["safe_for_model_context"], review.safe_for_model_context)


if __name__ == "__main__":
    unittest.main()
