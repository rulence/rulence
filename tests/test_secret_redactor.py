"""Tests for the secret redactor.

Every value in this file is a synthetic fake. None of the strings below
are valid credentials anywhere; they only carry the *shape* of a real
secret so the detector can match them.
"""
from __future__ import annotations

import unittest

from rulence.security import RedactionResult, SecretRedactor, redact_payload, redact_text

# --- synthetic fakes (NEVER paste real secrets here) -------------------

FAKE_GITHUB_TOKEN = "ghp_FAKEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # 40 chars
FAKE_GITHUB_PAT = "github_pat_" + "FAKE" + ("0" * 78)  # 4 + 78 = 82 after prefix
FAKE_OPENAI_KEY = "sk-FAKE0000000000000000"  # 23 chars
FAKE_OPENAI_PROJECT_KEY = "sk-proj-FAKE" + ("0" * 36)  # 40 chars after prefix
FAKE_ANTHROPIC_KEY = "sk-ant-api03-FAKE0000000000000000"  # 20 after prefix
FAKE_AWS_KEY = "AKIAFAKEFAKE12345678"  # AKIA + 16 chars
FAKE_BEARER = "Bearer FAKEFAKEFAKEFAKE12"  # 17 chars after Bearer
FAKE_DB_URL = "postgres://admin:FAKEpass@db.example.com:5432/app"
FAKE_BASIC_AUTH_URL = "https://user:FAKEpass@example.com/foo"
FAKE_SLACK_TOKEN = "xoxb-1234-FAKE-fakefake"
FAKE_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE\n"
    "FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE\n"
    "-----END PRIVATE KEY-----"
)
FAKE_ENV_LINE = "API_KEY=FAKE-secret-12345"


def _all_fakes():
    return [
        ("github_token", FAKE_GITHUB_TOKEN),
        ("github_pat", FAKE_GITHUB_PAT),
        ("openai_key", FAKE_OPENAI_KEY),
        ("openai_project_key", FAKE_OPENAI_PROJECT_KEY),
        ("anthropic_key", FAKE_ANTHROPIC_KEY),
        ("aws_access_key_id", FAKE_AWS_KEY),
        ("bearer_token", FAKE_BEARER),
        ("db_url_with_password", FAKE_DB_URL),
        ("basic_auth_url", FAKE_BASIC_AUTH_URL),
        ("slack_token", FAKE_SLACK_TOKEN),
        ("pem_private_key", FAKE_PEM),
        ("env_secret", FAKE_ENV_LINE),
    ]


class SecretRedactorDetectorTests(unittest.TestCase):
    def test_returns_redaction_result_type(self) -> None:
        result = redact_text(FAKE_GITHUB_TOKEN)
        self.assertIsInstance(result, RedactionResult)
        self.assertEqual(result.redaction_count, 1)
        self.assertEqual(result.redaction_types, ("github_token",))
        self.assertEqual(len(result.fingerprints), 1)
        self.assertTrue(result.fingerprints[0].startswith("sha256:"))
        self.assertNotIn(FAKE_GITHUB_TOKEN, result.redacted_text)
        self.assertIn("[REDACTED:github_token]", result.redacted_text)

    def test_each_detector_redacts_its_synthetic_fake(self) -> None:
        for expected_type, fake in _all_fakes():
            with self.subTest(detector=expected_type):
                result = redact_text(fake)
                self.assertGreater(result.redaction_count, 0)
                self.assertIn(expected_type, result.redaction_types)
                self.assertNotIn(fake, result.redacted_text)
                self.assertIn(f"[REDACTED:{expected_type}]", result.redacted_text)

    def test_does_not_redact_ordinary_source_code_identifiers(self) -> None:
        text = "x = 42\nresult = compute_total(items)\nfor key in mapping: pass"
        result = redact_text(text)
        self.assertEqual(result.redaction_count, 0)
        self.assertEqual(result.redacted_text, text)

    def test_does_not_redact_short_password_assignments(self) -> None:
        # Below the 8-char threshold for the env_secret detector.
        text = "password=abc"
        result = redact_text(text)
        self.assertEqual(result.redaction_count, 0)

    def test_redaction_keeps_key_visible_in_env_secret(self) -> None:
        result = redact_text(FAKE_ENV_LINE)
        self.assertIn("API_KEY=", result.redacted_text)
        self.assertIn("[REDACTED:env_secret]", result.redacted_text)

    def test_db_url_redaction_keeps_no_password(self) -> None:
        result = redact_text(FAKE_DB_URL)
        self.assertNotIn("FAKEpass", result.redacted_text)
        self.assertIn("[REDACTED:db_url_with_password]", result.redacted_text)

    def test_pem_block_is_redacted_across_lines(self) -> None:
        result = redact_text(FAKE_PEM)
        self.assertNotIn("BEGIN PRIVATE KEY", result.redacted_text)
        self.assertNotIn("END PRIVATE KEY", result.redacted_text)
        self.assertEqual(result.redaction_count, 1)
        self.assertEqual(result.redaction_types, ("pem_private_key",))

    def test_more_specific_type_wins_over_env_secret(self) -> None:
        # A KEY=value where the value is itself a real-looking GitHub
        # token. The github_token detector runs first; env_secret skips
        # the already-redacted placeholder.
        text = f"GITHUB_TOKEN={FAKE_GITHUB_TOKEN}"
        result = redact_text(text)
        self.assertIn("github_token", result.redaction_types)
        self.assertNotIn(FAKE_GITHUB_TOKEN, result.redacted_text)

    def test_redact_text_handles_empty_and_non_string(self) -> None:
        self.assertEqual(redact_text("").redacted_text, "")
        # Non-string -> returns it unchanged with a 0 count.
        result = SecretRedactor().redact_text(123)  # type: ignore[arg-type]
        self.assertEqual(result.redaction_count, 0)


class RedactPayloadTests(unittest.TestCase):
    def test_dict_redaction_walks_nested_strings(self) -> None:
        payload = {
            "tool_name": "Bash",
            "tool_input": {
                "command": f"echo {FAKE_GITHUB_TOKEN}",
                "env": {"OPENAI_API_KEY": FAKE_OPENAI_KEY},
            },
            "evidence": [
                "harmless",
                f"saw {FAKE_AWS_KEY}",
            ],
        }
        new, count, types = redact_payload(payload)
        self.assertGreaterEqual(count, 3)
        self.assertIn("github_token", types)
        self.assertIn("openai_key", types)
        self.assertIn("aws_access_key_id", types)
        # Original payload must not be mutated.
        self.assertIn(FAKE_GITHUB_TOKEN, payload["tool_input"]["command"])
        # New payload must not contain any of the originals.
        flattened = repr(new)
        self.assertNotIn(FAKE_GITHUB_TOKEN, flattened)
        self.assertNotIn(FAKE_OPENAI_KEY, flattened)
        self.assertNotIn(FAKE_AWS_KEY, flattened)

    def test_list_redaction(self) -> None:
        payload = ["clean", FAKE_GITHUB_TOKEN, ["nested", FAKE_OPENAI_KEY]]
        new, count, types = redact_payload(payload)
        self.assertEqual(count, 2)
        self.assertIn("github_token", types)
        self.assertIn("openai_key", types)
        flattened = repr(new)
        self.assertNotIn(FAKE_GITHUB_TOKEN, flattened)
        self.assertNotIn(FAKE_OPENAI_KEY, flattened)

    def test_non_container_passes_through(self) -> None:
        new, count, _ = redact_payload(123)
        self.assertEqual(new, 123)
        self.assertEqual(count, 0)
        new, count, _ = redact_payload(None)
        self.assertIsNone(new)
        self.assertEqual(count, 0)

    def test_tuple_input_returns_tuple(self) -> None:
        new, count, _ = redact_payload((FAKE_GITHUB_TOKEN, "ok"))
        self.assertIsInstance(new, tuple)
        self.assertEqual(count, 1)
        self.assertNotIn(FAKE_GITHUB_TOKEN, new[0])


if __name__ == "__main__":
    unittest.main()
