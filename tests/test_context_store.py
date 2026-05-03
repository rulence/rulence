from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rulence.context import ContextFragment, ContextStore, SourceRef


def _fragment(
    *,
    kind: str = "user_instruction",
    text: str = "hello world",
    fragment_id: str | None = None,
    corr_id: str | None = "ct_corr",
    session_id: str | None = "rs_session",
    runner: str | None = "claude_code",
    memory_backend: str | None = None,
    tool_name: str | None = None,
) -> ContextFragment:
    source = SourceRef(
        source_type="user_prompt",
        runner=runner,
        corr_id=corr_id,
        session_id=session_id,
        memory_backend=memory_backend,
        tool_name=tool_name,
    )
    return ContextFragment.build(
        kind=kind,
        text=text,
        source=source,
        fragment_id=fragment_id,
        memory_backend=memory_backend,
    )


class ContextStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = ContextStore(self.root)

    def test_add_and_get_round_trips(self) -> None:
        fragment = _fragment(text="hello policy")
        self.store.add(fragment)
        fetched = self.store.get(fragment.fragment_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.fragment_id, fragment.fragment_id)
        self.assertEqual(fetched.kind, "user_instruction")
        self.assertEqual(fetched.text, "hello policy")

    def test_add_persists_to_jsonl(self) -> None:
        fragment = _fragment(text="persisted")
        self.store.add(fragment)
        self.assertTrue(self.store.path.exists())
        lines = [
            line for line in self.store.path.read_text("utf-8").splitlines() if line
        ]
        self.assertEqual(len(lines), 1)
        self.assertIn("persisted", lines[0])

    def test_list_filters_by_corr_and_session(self) -> None:
        a = _fragment(corr_id="ct_a", session_id="rs_1")
        b = _fragment(corr_id="ct_b", session_id="rs_1")
        c = _fragment(corr_id="ct_a", session_id="rs_2")
        for f in (a, b, c):
            self.store.add(f)

        a_only = self.store.by_corr_id("ct_a")
        self.assertEqual(
            sorted(f.fragment_id for f in a_only),
            sorted([a.fragment_id, c.fragment_id]),
        )
        rs1 = self.store.by_session_id("rs_1")
        self.assertEqual(
            sorted(f.fragment_id for f in rs1),
            sorted([a.fragment_id, b.fragment_id]),
        )

    def test_by_memory_backend_returns_only_matching(self) -> None:
        honcho = _fragment(
            kind="honcho_memory",
            memory_backend="honcho",
            text="user prefers tabs",
        )
        mempalace = _fragment(
            kind="mempalace_memory",
            memory_backend="mempalace",
            text="auth lives in src/auth",
        )
        self.store.add(honcho)
        self.store.add(mempalace)
        self.assertEqual(
            [f.fragment_id for f in self.store.by_memory_backend("honcho")],
            [honcho.fragment_id],
        )
        self.assertEqual(
            [f.fragment_id for f in self.store.by_memory_backend("mempalace")],
            [mempalace.fragment_id],
        )

    def test_search_lexical_returns_matching_fragments(self) -> None:
        self.store.add(_fragment(text="rotate aws keys before deploy"))
        self.store.add(_fragment(text="run a database migration"))
        self.store.add(_fragment(text="ship the new ui"))
        hits = self.store.search_lexical("aws keys")
        self.assertEqual(len(hits), 1)
        self.assertIn("aws", hits[0].redacted_text)
        empty = self.store.search_lexical("nonexistent token zzzzz")
        self.assertEqual(empty, [])

    def test_search_lexical_respects_filters(self) -> None:
        self.store.add(
            _fragment(text="aws keys", corr_id="ct_x", session_id="rs_1")
        )
        self.store.add(
            _fragment(text="aws keys", corr_id="ct_y", session_id="rs_1")
        )
        hits = self.store.search_lexical("aws keys", filters={"corr_id": "ct_x"})
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].source.corr_id, "ct_x")

    def test_expire_hides_fragment(self) -> None:
        fragment = _fragment(text="will be expired")
        self.store.add(fragment)
        self.assertIsNotNone(self.store.get(fragment.fragment_id))
        existed = self.store.expire(fragment.fragment_id, "no longer relevant")
        self.assertTrue(existed)
        self.assertIsNone(self.store.get(fragment.fragment_id))
        # The fragment should be missing from list() but visible with
        # include_expired=True.
        self.assertNotIn(
            fragment.fragment_id,
            [f.fragment_id for f in self.store.list()],
        )
        self.assertIn(
            fragment.fragment_id,
            [
                f.fragment_id
                for f in self.store.list(include_expired=True)
            ],
        )

    def test_expire_records_reason_in_jsonl(self) -> None:
        fragment = _fragment()
        self.store.add(fragment)
        self.store.expire(fragment.fragment_id, "policy:context_expired")
        text = self.store.path.read_text("utf-8")
        self.assertIn("policy:context_expired", text)
        self.assertIn('"type": "expire"', text)

    def test_unknown_filter_key_matches_nothing(self) -> None:
        self.store.add(_fragment())
        self.assertEqual(self.store.list({"made_up_field": "x"}), [])

    def test_corrupt_lines_are_skipped(self) -> None:
        fragment = _fragment(text="ok line")
        self.store.add(fragment)
        # Append a deliberately malformed JSON line and a non-fragment dict.
        with self.store.path.open("a", encoding="utf-8") as handle:
            handle.write("not json at all\n")
            handle.write('{"type": "fragment", "fragment_id": "broken"}\n')
        # Reading still surfaces the good fragment and skips junk.
        ids = [f.fragment_id for f in self.store.list()]
        self.assertEqual(ids, [fragment.fragment_id])


if __name__ == "__main__":
    unittest.main()
