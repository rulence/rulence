from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rulence.claims import (
    Claim,
    ClaimsLedger,
    Violation,
    load_claims,
    parse_yaml,
    scan_file,
    validate_ledger,
    verify_repo,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _claim(**overrides):
    base = {
        "claim_id": "synthetic_test_claim",
        "claim_text": "synthetic claim used by tests only",
        "category": "test",
        "status": "supported",
        "current_evidence": ["src/rulence/claims.py"],
        "required_components": [],
        "acceptance_tests": ["tests.test_claims_verifier"],
        "public_copy_allowed": True,
        "allowed_phrasings": [],
        "blocked_phrases": [],
        "notes": "",
    }
    base.update(overrides)
    return base


def _ledger(claims_data):
    return ClaimsLedger(version=1, claims=tuple(Claim(raw=raw) for raw in claims_data), path=Path("<synthetic>"))


class ClaimsLedgerStructureTests(unittest.TestCase):
    def test_real_claims_yml_parses(self):
        ledger = load_claims(REPO_ROOT / "docs" / "claims.yml")
        self.assertGreater(len(ledger.claims), 0)
        self.assertEqual(ledger.version, 1)

    def test_every_claim_has_required_fields(self):
        ledger = load_claims(REPO_ROOT / "docs" / "claims.yml")
        violations = validate_ledger(ledger)
        self.assertEqual(violations, [], f"unexpected ledger violations: {violations}")

    def test_invalid_status_fails(self):
        ledger = _ledger([_claim(status="bogus")])
        violations = validate_ledger(ledger)
        messages = " | ".join(v.message for v in violations)
        self.assertIn("invalid status", messages)

    def test_unsupported_public_claim_fails(self):
        ledger = _ledger([
            _claim(
                claim_id="bad_unsupported",
                status="unsupported",
                public_copy_allowed=True,
                current_evidence=[],
            )
        ])
        violations = validate_ledger(ledger)
        messages = " | ".join(v.message for v in violations)
        self.assertIn("unsupported", messages)
        self.assertIn("public_copy_allowed", messages)

    def test_aspirational_public_claim_fails_without_roadmap_qualifier(self):
        ledger = _ledger([
            _claim(
                claim_id="bad_aspirational",
                status="aspirational",
                public_copy_allowed=True,
                claim_text="memory governance with full coverage",
                current_evidence=[],
            )
        ])
        violations = validate_ledger(ledger)
        messages = " | ".join(v.message for v in violations)
        self.assertIn("qualifier", messages)

    def test_aspirational_public_claim_passes_with_roadmap_qualifier(self):
        ledger = _ledger([
            _claim(
                claim_id="ok_aspirational",
                status="aspirational",
                public_copy_allowed=True,
                claim_text="planned roadmap feature for memory governance",
                current_evidence=[],
            )
        ])
        violations = validate_ledger(ledger)
        self.assertEqual(violations, [])

    def test_supported_requires_evidence(self):
        ledger = _ledger([_claim(status="supported", current_evidence=[])])
        violations = validate_ledger(ledger)
        messages = " | ".join(v.message for v in violations)
        self.assertIn("must cite current_evidence", messages)

    def test_duplicate_claim_id_flagged(self):
        ledger = _ledger([
            _claim(claim_id="dup", status="supported"),
            _claim(claim_id="dup", status="supported"),
        ])
        violations = validate_ledger(ledger)
        messages = " | ".join(v.message for v in violations)
        self.assertIn("duplicate claim_id", messages)

    def test_public_copy_allowed_must_be_bool(self):
        ledger = _ledger([_claim(public_copy_allowed="yes")])
        violations = validate_ledger(ledger)
        messages = " | ".join(v.message for v in violations)
        self.assertIn("must be a boolean", messages)


class CopyScannerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = _ledger([
            _claim(
                claim_id="governs_every_agent",
                status="unsupported",
                public_copy_allowed=False,
                blocked_phrases=["govern every agent"],
                current_evidence=[],
            ),
        ])

    def _write(self, name: str, text: str, suffix: str = ".md") -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="rulence-claims-"))
        path = tmp / f"{name}{suffix}"
        path.write_text(text, encoding="utf-8")
        return path

    def test_blocked_phrase_in_readme_fails(self):
        path = self._write("README", "# Intro\n\nRulence will govern every agent.\n")
        violations = scan_file(path, self.ledger)
        self.assertEqual(len(violations), 1)
        self.assertIn("govern every agent", violations[0].message)

    def test_roadmap_qualified_phrase_passes(self):
        path = self._write(
            "README",
            "# Intro\n\nReal stuff today.\n\n## Roadmap\n\nOne day Rulence might govern every agent.\n",
        )
        violations = scan_file(path, self.ledger)
        self.assertEqual(violations, [])

    def test_aspirational_section_passes(self):
        path = self._write(
            "Plans",
            "# Plans\n\n## Aspirational features\n\nWe could govern every agent.\n",
        )
        violations = scan_file(path, self.ledger)
        self.assertEqual(violations, [])

    def test_nonroadmap_section_under_roadmap_does_not_carry_over(self):
        path = self._write(
            "Mixed",
            "# Mixed\n\n## Roadmap\n\nFuture items.\n\n## Today\n\nWe govern every agent.\n",
        )
        violations = scan_file(path, self.ledger)
        self.assertEqual(len(violations), 1)
        # The blocked phrase is on the line "We govern every agent." which is
        # the 9th line in the fixture above.
        self.assertEqual(violations[0].line, 9)

    def test_python_file_blocked_phrase_fails_unconditionally(self):
        path = self._write(
            "fake_module",
            '"""Docstring saying govern every agent."""\n',
            suffix=".py",
        )
        violations = scan_file(path, self.ledger)
        self.assertEqual(len(violations), 1)


class RealRepoTests(unittest.TestCase):
    def test_runtime_matrix_file_exists(self):
        self.assertTrue((REPO_ROOT / "docs" / "runtime-matrix.md").exists())

    def test_claims_md_file_exists(self):
        self.assertTrue((REPO_ROOT / "docs" / "claims.md").exists())

    def test_claims_yml_file_exists(self):
        self.assertTrue((REPO_ROOT / "docs" / "claims.yml").exists())

    def test_full_repo_verification_clean(self):
        violations = verify_repo(REPO_ROOT)
        self.assertEqual(
            violations,
            [],
            "real repo public copy must not contain blocked phrases: "
            + json.dumps([v.to_dict() for v in violations], indent=2),
        )


class YamlParserTests(unittest.TestCase):
    def test_scalar_types(self):
        result = parse_yaml(
            "version: 1\nfoo: bar\nbool: true\nflag: false\nempty:\nnone_val: null\n"
        )
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["foo"], "bar")
        self.assertIs(result["bool"], True)
        self.assertIs(result["flag"], False)
        self.assertIsNone(result["empty"])
        self.assertIsNone(result["none_val"])

    def test_quoted_strings_with_colon(self):
        result = parse_yaml('claim_text: "hello: world"\n')
        self.assertEqual(result["claim_text"], "hello: world")

    def test_inline_empty_list_and_map(self):
        result = parse_yaml("a: []\nb: {}\n")
        self.assertEqual(result["a"], [])
        self.assertEqual(result["b"], {})

    def test_block_sequence_of_mappings(self):
        text = (
            "items:\n"
            "  - claim_id: foo\n"
            "    status: supported\n"
            "  - claim_id: bar\n"
            "    status: partial\n"
        )
        result = parse_yaml(text)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["claim_id"], "foo")
        self.assertEqual(result["items"][1]["status"], "partial")

    def test_nested_list_inside_sequence_item(self):
        text = (
            "items:\n"
            "  - claim_id: foo\n"
            "    blocked_phrases:\n"
            "      - one\n"
            "      - two\n"
            "    notes: ok\n"
        )
        result = parse_yaml(text)
        item = result["items"][0]
        self.assertEqual(item["blocked_phrases"], ["one", "two"])
        self.assertEqual(item["notes"], "ok")

    def test_tabs_rejected(self):
        with self.assertRaises(ValueError):
            parse_yaml("foo:\tbar\n")

    def test_unterminated_quote_rejected(self):
        with self.assertRaises(ValueError):
            parse_yaml('foo: "unterminated\n')

    def test_comment_outside_quotes_stripped(self):
        result = parse_yaml('foo: bar  # trailing comment\nbaz: "with # hash"\n')
        self.assertEqual(result["foo"], "bar")
        self.assertEqual(result["baz"], "with # hash")


if __name__ == "__main__":
    unittest.main()
