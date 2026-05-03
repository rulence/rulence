"""Unit tests for FinalResponseReviewer.

Synthetic fakes only.
"""
from __future__ import annotations

import unittest

from rulence.logic import Constraint
from rulence.review import FinalResponseReviewer

FAKE_GITHUB_TOKEN = "ghp_FAKEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _audit(event_type: str, **fields) -> dict:
    base = {
        "event_type": event_type,
        "tool_name": None,
        "decision": None,
        "metadata": {},
    }
    base.update(fields)
    return base


class CleanResponseTests(unittest.TestCase):
    def test_clean_response_passes(self) -> None:
        result = FinalResponseReviewer().review(
            final_response="Done. Let me know if you'd like me to do anything else.",
            audit_records=[],
            constraints=(),
        )
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.suggested_action, "allow")
        self.assertEqual(result.evidence, ())


class SecretLeakageTests(unittest.TestCase):
    def test_secret_in_final_fails(self) -> None:
        result = FinalResponseReviewer().review(
            final_response=f"Use this token: {FAKE_GITHUB_TOKEN}",
            audit_records=[],
            constraints=(),
        )
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.suggested_action, "block")
        self.assertEqual(len(result.secret_findings), 1)
        self.assertIn("github_token", result.secret_findings[0])


class ConstraintTests(unittest.TestCase):
    def test_forbids_violation_fails(self) -> None:
        constraints = (Constraint(kind="forbids", condition="deploy", target="friday"),)
        result = FinalResponseReviewer().review(
            final_response="I deployed the application on Friday afternoon.",
            audit_records=[],
            constraints=constraints,
        )
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.suggested_action, "revise")
        self.assertEqual(len(result.violated_constraints), 1)
        self.assertIn("forbids(deploy, friday)", result.violated_constraints[0])

    def test_requires_omission_warns(self) -> None:
        constraints = (Constraint(kind="requires", condition="deploy", target="tests"),)
        result = FinalResponseReviewer().review(
            final_response="I deployed the application to production.",
            audit_records=[],
            constraints=constraints,
        )
        self.assertEqual(result.status, "warn")
        self.assertEqual(len(result.missing_required_actions), 1)
        self.assertIn("requires(deploy, tests)", result.missing_required_actions[0])

    def test_requires_satisfied_passes(self) -> None:
        constraints = (Constraint(kind="requires", condition="deploy", target="tests"),)
        result = FinalResponseReviewer().review(
            final_response="I deployed the app after running the tests.",
            audit_records=[
                _audit("PreToolUse", tool_name="Bash"),
            ],
            constraints=constraints,
        )
        self.assertEqual(result.status, "pass")


class ActionClaimWithoutTraceTests(unittest.TestCase):
    def test_claim_running_tests_without_bash_warns(self) -> None:
        result = FinalResponseReviewer().review(
            final_response="I ran the tests and everything passed.",
            audit_records=[],  # no Bash trace
            constraints=(),
        )
        self.assertEqual(result.status, "warn")
        self.assertTrue(
            any("running tests" in m for m in result.trace_mismatches),
            result.trace_mismatches,
        )

    def test_claim_running_tests_with_bash_passes(self) -> None:
        result = FinalResponseReviewer().review(
            final_response="I ran the tests and everything passed.",
            audit_records=[_audit("PreToolUse", tool_name="Bash")],
            constraints=(),
        )
        self.assertEqual(result.status, "pass")

    def test_claim_file_edit_without_edit_trace_warns(self) -> None:
        result = FinalResponseReviewer().review(
            final_response="I updated the config file with the new setting.",
            audit_records=[_audit("PreToolUse", tool_name="Bash")],
            constraints=(),
        )
        self.assertEqual(result.status, "warn")
        self.assertTrue(
            any("file edit" in m for m in result.trace_mismatches),
            result.trace_mismatches,
        )

    def test_claim_file_edit_with_edit_trace_passes(self) -> None:
        result = FinalResponseReviewer().review(
            final_response="I updated the config file with the new setting.",
            audit_records=[_audit("PreToolUse", tool_name="Edit")],
            constraints=(),
        )
        self.assertEqual(result.status, "pass")

    def test_claim_memory_search_without_mcp_search_warns(self) -> None:
        result = FinalResponseReviewer().review(
            final_response="I searched the memory for relevant context.",
            audit_records=[_audit("PreToolUse", tool_name="Bash")],
            constraints=(),
        )
        self.assertEqual(result.status, "warn")
        self.assertTrue(
            any("memory search" in m for m in result.trace_mismatches),
            result.trace_mismatches,
        )

    def test_claim_memory_search_with_mcp_search_passes(self) -> None:
        result = FinalResponseReviewer().review(
            final_response="I searched the memory for relevant context.",
            audit_records=[_audit("PreToolUse", tool_name="mcp__honcho__search")],
            constraints=(),
        )
        self.assertEqual(result.status, "pass")


class UnresolvedAskTests(unittest.TestCase):
    def test_prior_ask_without_acknowledgement_warns(self) -> None:
        result = FinalResponseReviewer().review(
            final_response="The task is complete.",
            audit_records=[_audit("PreToolUse", decision="ask", tool_name="Bash")],
            constraints=(),
        )
        self.assertEqual(result.status, "warn")
        self.assertTrue(
            any("asked for confirmation" in m for m in result.missing_required_actions),
            result.missing_required_actions,
        )

    def test_prior_ask_with_acknowledgement_passes(self) -> None:
        result = FinalResponseReviewer().review(
            final_response="Should I proceed with the deployment? Please confirm.",
            audit_records=[_audit("PreToolUse", decision="ask", tool_name="Bash")],
            constraints=(),
        )
        self.assertEqual(result.status, "pass")


class ConstraintParsingFromPromptTests(unittest.TestCase):
    def test_parses_constraints_from_user_prompt_when_explicit_constraints_omitted(
        self,
    ) -> None:
        # The reviewer parses requires/forbids out of the user prompt
        # via rulence.logic.parse_constraints when constraints=None.
        result = FinalResponseReviewer().review(
            final_response="I went ahead and deployed it on Friday.",
            audit_records=[],
            user_prompt="forbids: deploy -> friday\n",
            constraints=None,
        )
        self.assertEqual(result.status, "fail")


class SerializationTests(unittest.TestCase):
    def test_to_dict_is_json_friendly(self) -> None:
        import json

        result = FinalResponseReviewer().review(
            final_response="ok",
            audit_records=[],
            constraints=(),
        )
        json.dumps(result.to_dict())  # must not raise


if __name__ == "__main__":
    unittest.main()
