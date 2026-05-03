from __future__ import annotations

import unittest

from rulence.governance import ACTIONS, SEVERITIES, GovernanceDecision


class GovernanceDecisionTests(unittest.TestCase):
    def test_allow_fast_path_is_low_by_default(self) -> None:
        decision = GovernanceDecision.allow_fast_path()
        self.assertEqual(decision.action, "allow")
        self.assertEqual(decision.severity, "low")
        self.assertEqual(decision.risk_level, "low")
        self.assertTrue(decision.audit_metadata["fast_path"])

    def test_from_preflight_block_maps_to_deny(self) -> None:
        decision = GovernanceDecision.from_preflight(
            "block",
            evidence=("destructive_with_weak_context",),
            policy_refs=("builtin:tier-5-high-risk",),
            risk_level="critical",
            risk_reasons=("bash:rm -rf",),
            required_followups=("stop before taking action",),
        )
        self.assertEqual(decision.action, "deny")
        self.assertEqual(decision.severity, "critical")
        self.assertEqual(decision.evidence, ("destructive_with_weak_context",))
        self.assertFalse(decision.audit_metadata["fast_path"])

    def test_from_preflight_warn_maps_to_ask(self) -> None:
        decision = GovernanceDecision.from_preflight(
            "warn",
            evidence=("memory_missing",),
            policy_refs=("builtin:tier-3-moderate",),
            risk_level="medium",
        )
        self.assertEqual(decision.action, "ask")

    def test_from_preflight_approve_maps_to_allow(self) -> None:
        decision = GovernanceDecision.from_preflight(
            "approve",
            evidence=(),
            policy_refs=("builtin:tier-2-mild",),
            risk_level="low",
        )
        self.assertEqual(decision.action, "allow")

    def test_invalid_action_raises(self) -> None:
        with self.assertRaises(ValueError):
            GovernanceDecision(
                action="bogus",
                severity="low",
                evidence=(),
                policy_refs=(),
                risk_level="low",
                risk_reasons=(),
                required_followups=(),
                audit_metadata={},
            )

    def test_invalid_severity_raises(self) -> None:
        with self.assertRaises(ValueError):
            GovernanceDecision(
                action="allow",
                severity="bogus",
                evidence=(),
                policy_refs=(),
                risk_level="low",
                risk_reasons=(),
                required_followups=(),
                audit_metadata={},
            )

    def test_invalid_risk_level_raises(self) -> None:
        with self.assertRaises(ValueError):
            GovernanceDecision(
                action="allow",
                severity="low",
                evidence=(),
                policy_refs=(),
                risk_level="bogus",
                risk_reasons=(),
                required_followups=(),
                audit_metadata={},
            )

    def test_to_dict_round_trip_is_json_friendly(self) -> None:
        import json

        decision = GovernanceDecision.from_preflight(
            "block",
            evidence=("a", "b"),
            policy_refs=("p1",),
            risk_level="high",
            risk_reasons=("r1",),
            required_followups=("f1",),
        )
        encoded = json.dumps(decision.to_dict())
        decoded = json.loads(encoded)
        self.assertEqual(decoded["action"], "deny")
        self.assertEqual(decoded["evidence"], ["a", "b"])
        self.assertEqual(decoded["policy_refs"], ["p1"])

    def test_constants_are_ordered(self) -> None:
        self.assertEqual(ACTIONS, ("allow", "warn", "ask", "deny"))
        self.assertEqual(SEVERITIES, ("low", "medium", "high", "critical"))


if __name__ == "__main__":
    unittest.main()
