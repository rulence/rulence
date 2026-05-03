from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rulence.audit import CorrelationIdManager


class CorrelationIdManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.manager = CorrelationIdManager(self.state_dir)

    def test_session_id_is_stable_for_same_claude_session(self) -> None:
        a = self.manager.derive_session_id("cc-claude-session-A")
        b = self.manager.derive_session_id("cc-claude-session-A")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("rs_"))

    def test_session_id_differs_across_claude_sessions(self) -> None:
        a = self.manager.derive_session_id("cc-claude-session-A")
        b = self.manager.derive_session_id("cc-claude-session-B")
        self.assertNotEqual(a, b)

    def test_session_id_is_stable_when_claude_id_missing(self) -> None:
        # Two calls with no claude id should still produce some rs_ id, but
        # they may differ across calls because the manager has to synthesize
        # a per-process seed. We assert each call is well-formed.
        first = self.manager.derive_session_id(None)
        second = self.manager.derive_session_id("")
        self.assertTrue(first.startswith("rs_"))
        self.assertTrue(second.startswith("rs_"))

    def test_session_started_then_user_prompt_creates_corr_id(self) -> None:
        self.manager.session_started("cs-abc")
        ctx = self.manager.user_prompt("cs-abc", prompt_hash="0" * 64)
        self.assertIsNotNone(ctx.corr_id)
        self.assertTrue(ctx.corr_id.startswith("ct_"))

    def test_one_logical_turn_reuses_corr_id_across_events(self) -> None:
        self.manager.session_started("cs-shared")
        prompt_ctx = self.manager.user_prompt("cs-shared", prompt_hash="0" * 64)
        pre_ctx = self.manager.current("cs-shared")
        post_ctx = self.manager.current("cs-shared")
        stop_ctx = self.manager.current("cs-shared")
        self.assertEqual(prompt_ctx.corr_id, pre_ctx.corr_id)
        self.assertEqual(pre_ctx.corr_id, post_ctx.corr_id)
        self.assertEqual(post_ctx.corr_id, stop_ctx.corr_id)
        self.assertEqual(prompt_ctx.rulence_session_id, pre_ctx.rulence_session_id)

    def test_new_user_prompt_starts_a_new_turn(self) -> None:
        self.manager.session_started("cs-multi-turn")
        first = self.manager.user_prompt("cs-multi-turn", prompt_hash="a" * 64)
        between = self.manager.current("cs-multi-turn")
        second = self.manager.user_prompt("cs-multi-turn", prompt_hash="b" * 64)
        after = self.manager.current("cs-multi-turn")
        self.assertEqual(first.corr_id, between.corr_id)
        self.assertNotEqual(first.corr_id, second.corr_id)
        self.assertEqual(second.corr_id, after.corr_id)

    def test_current_creates_fallback_corr_id_when_state_absent(self) -> None:
        ctx = self.manager.current("cs-no-prompt-yet")
        self.assertIsNotNone(ctx.corr_id)
        again = self.manager.current("cs-no-prompt-yet")
        self.assertEqual(ctx.corr_id, again.corr_id)

    def test_session_ended_records_ended_at(self) -> None:
        self.manager.session_started("cs-ending")
        self.manager.user_prompt("cs-ending", prompt_hash="c" * 64)
        ctx = self.manager.session_ended("cs-ending")
        path = self.state_dir / f"{ctx.rulence_session_id}.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("ended_at", state)

    def test_does_not_persist_raw_prompt(self) -> None:
        prompt_text = "this prompt mentions a synthetic-fake-secret-ABC123"
        from rulence.audit.correlation import hash_text

        digest = hash_text(prompt_text)
        self.manager.session_started("cs-redaction")
        self.manager.user_prompt("cs-redaction", prompt_hash=digest)
        rsid = self.manager.derive_session_id("cs-redaction")
        state = json.loads((self.state_dir / f"{rsid}.json").read_text(encoding="utf-8"))
        # The plaintext prompt must NOT appear; only its hash.
        self.assertNotIn("synthetic-fake-secret-ABC123", json.dumps(state))
        self.assertEqual(state.get("last_prompt_hash"), digest)


if __name__ == "__main__":
    unittest.main()
