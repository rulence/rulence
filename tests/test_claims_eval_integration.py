from __future__ import annotations

import unittest
from pathlib import Path

from rulence.claims import Claim, ClaimsLedger, validate_with_evals
from rulence.evals.runner import EvalReport, EvalResult


def _claim(claim_id: str, **overrides):
    base = {
        "claim_id": claim_id,
        "claim_text": "synthetic claim",
        "category": "context",
        "status": "supported",
        "current_evidence": ["src/rulence/context"],
        "required_components": [],
        "acceptance_tests": ["tests.test_context_assembler"],
        "public_copy_allowed": True,
        "allowed_phrasings": [],
        "blocked_phrases": [],
        "notes": "",
    }
    base.update(overrides)
    return Claim(raw=base)


def _ledger(*claims) -> ClaimsLedger:
    return ClaimsLedger(version=1, claims=tuple(claims), path=Path("<synthetic>"))


def _report(*pairs) -> EvalReport:
    """Build a synthetic EvalReport.

    ``pairs`` is an iterable of ``(eval_id, category, status, claim_ids)``
    tuples.
    """
    results = tuple(
        EvalResult(
            eval_id=pair[0],
            category=pair[1],
            claim_ids=tuple(pair[3]) if len(pair) > 3 else (),
            status=pair[2],
        )
        for pair in pairs
    )
    summary = {"pass": 0, "fail": 0, "skip": 0, "error": 0, "total": len(results)}
    for r in results:
        summary[r.status] = summary.get(r.status, 0) + 1
    return EvalReport(
        started_at="2026-05-03T00:00:00Z",
        finished_at="2026-05-03T00:00:01Z",
        results=results,
        summary=summary,
    )


class ValidateWithEvalsTests(unittest.TestCase):
    def test_supported_context_claim_with_passing_eval_is_clean(self) -> None:
        ledger = _ledger(_claim("context_assembly", category="context"))
        report = _report(
            ("context.ok", "context", "pass", ("context_assembly",)),
        )
        violations = validate_with_evals(ledger, report)
        self.assertEqual(violations, [])

    def test_supported_context_claim_without_passing_eval_fails(self) -> None:
        ledger = _ledger(_claim("context_assembly", category="context"))
        report = _report()  # empty
        violations = validate_with_evals(ledger, report)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].claim_id, "context_assembly")
        self.assertIn("context", violations[0].message)

    def test_unsupported_claim_is_skipped(self) -> None:
        ledger = _ledger(
            _claim(
                "context_intelligence",
                category="context",
                status="aspirational",
                public_copy_allowed=False,
            )
        )
        violations = validate_with_evals(ledger, _report())
        self.assertEqual(violations, [])

    def test_memory_claim_without_memory_eval_fails(self) -> None:
        ledger = _ledger(
            _claim("memory_arbitration_policy", category="memory")
        )
        # A passing context eval is not enough — the verifier requires
        # a memory category eval.
        report = _report(
            ("context.ok", "context", "pass", ("memory_arbitration_policy",)),
        )
        violations = validate_with_evals(ledger, report)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].claim_id, "memory_arbitration_policy")
        self.assertIn("memory", violations[0].message)

    def test_failing_eval_does_not_count_as_evidence(self) -> None:
        ledger = _ledger(_claim("context_assembly", category="context"))
        report = _report(
            ("context.fail", "context", "fail", ("context_assembly",)),
        )
        violations = validate_with_evals(ledger, report)
        self.assertEqual(len(violations), 1)

    def test_explicit_eval_evidence_satisfies_requirement(self) -> None:
        ledger = _ledger(
            _claim(
                "context_assembly",
                category="context",
                eval_evidence=["context.compression_reduces_tokens"],
            )
        )
        # This passing eval has no claim_ids attached, but its eval_id
        # matches the explicit evidence list.
        report = _report(
            ("context.compression_reduces_tokens", "context", "pass", ()),
        )
        violations = validate_with_evals(ledger, report)
        self.assertEqual(violations, [])

    def test_real_repo_evals_satisfy_supported_claims(self) -> None:
        from rulence.claims import load_claims
        from rulence.evals import EvalRunner

        repo = Path(__file__).resolve().parents[1]
        ledger = load_claims(repo / "docs" / "claims.yml")
        report = EvalRunner().run()
        violations = validate_with_evals(ledger, report)
        # The claim ledger we ship today has at least one supported
        # claim per required eval category, and the eval suite passes
        # on a clean tree, so this list should be empty.
        self.assertEqual(
            violations,
            [],
            "real repo claims missing eval evidence: "
            + "; ".join(v.message for v in violations),
        )


if __name__ == "__main__":
    unittest.main()
