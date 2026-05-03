from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rulence.checks import run_check
from rulence.classifier import classify_task
from rulence.policies import (
    CHECK_TRANSCRIPT_CONTRADICTION,
    CHECK_TRANSCRIPT_DRIFT,
    CHECK_TRANSCRIPT_STALENESS,
    DEFAULT_POLICIES,
)
from rulence.preflight import preflight_task
from rulence.transcript import parse_transcript_text


def _jsonl(*turns: tuple[str, str]) -> str:
    return "\n".join(json.dumps({"role": role, "content": content}) for role, content in turns)


class TranscriptDriftCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DEFAULT_POLICIES[3]
        self.classification = classify_task("deploy on friday")

    def test_passes_when_transcript_is_empty(self) -> None:
        result = run_check(
            CHECK_TRANSCRIPT_DRIFT,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=(),
        )

        self.assertEqual(result.status, "pass")
        self.assertIn("no transcript", result.detail)

    def test_warns_on_keyword_drift_from_first_user_turn(self) -> None:
        turns = parse_transcript_text("plan a database migration with rollback")

        result = run_check(
            CHECK_TRANSCRIPT_DRIFT,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "warn")
        self.assertIn("drift", result.detail.lower())

    def test_passes_when_current_task_shares_keywords_with_first_turn(self) -> None:
        turns = parse_transcript_text("plan the deployment for friday")

        result = run_check(
            CHECK_TRANSCRIPT_DRIFT,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "pass")


class TranscriptContradictionCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DEFAULT_POLICIES[3]
        self.classification = classify_task("deploy on friday")

    def test_passes_when_transcript_is_empty(self) -> None:
        result = run_check(
            CHECK_TRANSCRIPT_CONTRADICTION,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=(),
        )

        self.assertEqual(result.status, "pass")

    def test_blocks_when_task_violates_transcript_forbid(self) -> None:
        turns = parse_transcript_text("don't deploy on friday")

        result = run_check(
            CHECK_TRANSCRIPT_CONTRADICTION,
            "deploy on friday this week",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "block")
        self.assertTrue(any("forbids" in piece for piece in result.evidence))

    def test_warns_when_transcript_requires_unmet(self) -> None:
        turns = parse_transcript_text("never migrate without backup")

        result = run_check(
            CHECK_TRANSCRIPT_CONTRADICTION,
            "migrate the production database now",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "warn")
        self.assertTrue(any("backup" in piece for piece in result.evidence))


class TranscriptStalenessCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DEFAULT_POLICIES[3]
        self.classification = classify_task("deploy on friday")

    def test_passes_when_transcript_is_empty(self) -> None:
        result = run_check(
            CHECK_TRANSCRIPT_STALENESS,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=(),
        )

        self.assertEqual(result.status, "pass")

    def test_warns_when_transcript_turns_are_unrelated_to_current_task(self) -> None:
        turns = parse_transcript_text(
            _jsonl(
                ("user", "plan a migration"),
                ("assistant", "what database?"),
                ("user", "let me pivot to a deployment"),
            )
        )

        result = run_check(
            CHECK_TRANSCRIPT_STALENESS,
            "deploy the app on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "warn")
        self.assertGreaterEqual(len(result.evidence), 1)

    def test_passes_when_every_turn_shares_keywords(self) -> None:
        turns = parse_transcript_text(
            _jsonl(
                ("user", "plan to deploy on friday"),
                ("assistant", "deploy plan looks ready for friday"),
            )
        )

        result = run_check(
            CHECK_TRANSCRIPT_STALENESS,
            "deploy on friday",
            "",
            self.classification,
            self.policy,
            transcript_turns=turns,
        )

        self.assertEqual(result.status, "pass")


class PreflightTranscriptIntegrationTests(unittest.TestCase):
    def _policy_dir(self, base: str, required_check: str, tier: int) -> str:
        path = Path(base) / f"tier-{tier}-transcript.toml"
        path.write_text(
            f'tier = {tier}\nlabel = "transcript"\n'
            f'required_checks = ["{required_check}"]\n'
            "warn_if = []\nblock_if = []\n",
            encoding="utf-8",
        )
        return base

    def test_preflight_blocks_when_transcript_forbids_current_action(self) -> None:
        task = "deploy the app on friday this week, please"
        tier = classify_task(task).tier
        with tempfile.TemporaryDirectory() as directory:
            policy_dir = self._policy_dir(directory, CHECK_TRANSCRIPT_CONTRADICTION, tier)
            transcript = "don't deploy on friday"

            result = preflight_task(task, policy_dir=policy_dir, transcript=transcript)

            self.assertEqual(result.verdict, "block")
            self.assertTrue(
                any(
                    check.name == CHECK_TRANSCRIPT_CONTRADICTION and check.status == "block"
                    for check in result.checks
                )
            )

    def test_preflight_no_transcript_lets_check_pass_silently(self) -> None:
        task = "deploy the app on friday this week, please"
        tier = classify_task(task).tier
        with tempfile.TemporaryDirectory() as directory:
            policy_dir = self._policy_dir(directory, CHECK_TRANSCRIPT_DRIFT, tier)

            result = preflight_task(task, policy_dir=policy_dir)

            drift_check = next(check for check in result.checks if check.name == CHECK_TRANSCRIPT_DRIFT)
            self.assertEqual(drift_check.status, "pass")
            self.assertIn("no transcript", drift_check.detail)


if __name__ == "__main__":
    unittest.main()
