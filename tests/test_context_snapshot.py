from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rulence.context import (
    ContextFragment,
    ContextSnapshot,
    ContextStore,
    SourceRef,
    snapshot_content_hash,
)


def _fragment(
    *,
    kind: str = "user_instruction",
    text: str = "do the thing",
    memory_backend: str | None = None,
    corr_id: str | None = "ct_corr",
    fragment_id: str | None = None,
) -> ContextFragment:
    source = SourceRef(
        source_type="user_prompt",
        runner="claude_code",
        corr_id=corr_id,
        session_id="rs_session",
        memory_backend=memory_backend,
    )
    return ContextFragment.build(
        kind=kind,
        text=text,
        source=source,
        memory_backend=memory_backend,
        fragment_id=fragment_id,
    )


class ContextSnapshotTests(unittest.TestCase):
    def test_build_collects_fragment_ids_and_backends(self) -> None:
        fragments = (
            _fragment(text="prompt"),
            _fragment(
                kind="honcho_memory",
                text="user prefers tabs",
                memory_backend="honcho",
            ),
            _fragment(
                kind="mempalace_memory",
                text="auth lives in src/auth",
                memory_backend="mempalace",
            ),
        )
        snapshot = ContextSnapshot.build(
            fragments,
            corr_id="ct_corr",
            task_id="task_1",
            policy_name="builtin:tier-3",
        )
        self.assertEqual(
            list(snapshot.fragment_ids),
            [f.fragment_id for f in fragments],
        )
        self.assertEqual(
            sorted(snapshot.memory_backends_used),
            ["honcho", "mempalace"],
        )
        self.assertEqual(snapshot.corr_id, "ct_corr")
        self.assertEqual(snapshot.policy_name, "builtin:tier-3")
        self.assertGreater(snapshot.token_estimate, 0)
        self.assertEqual(
            snapshot.content_hash,
            snapshot_content_hash(snapshot.fragment_ids),
        )

    def test_to_dict_round_trips_through_json(self) -> None:
        fragments = (_fragment(text="prompt"),)
        snapshot = ContextSnapshot.build(fragments, corr_id="ct_corr")
        encoded = json.dumps(snapshot.to_dict())
        decoded = ContextSnapshot.from_dict(json.loads(encoded))
        self.assertEqual(decoded.snapshot_id, snapshot.snapshot_id)
        self.assertEqual(decoded.fragment_ids, snapshot.fragment_ids)
        self.assertEqual(decoded.content_hash, snapshot.content_hash)
        self.assertEqual(decoded.token_estimate, snapshot.token_estimate)

    def test_replay_returns_expected_fragments_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ContextStore(Path(tmp))
            a = _fragment(text="A", fragment_id="frag_a")
            b = _fragment(text="B", fragment_id="frag_b")
            c = _fragment(text="C", fragment_id="frag_c")
            for fragment in (a, b, c):
                store.add(fragment)

            snapshot = ContextSnapshot.build((a, c), corr_id="ct_corr")
            replayed = [store.get(fid) for fid in snapshot.fragment_ids]
            self.assertEqual(len(replayed), 2)
            texts = [f.text for f in replayed if f is not None]
            self.assertEqual(texts, ["A", "C"])

            # Expire one and confirm replay surfaces a None for it.
            store.expire("frag_a", "stale")
            replayed_after = [store.get(fid) for fid in snapshot.fragment_ids]
            self.assertIsNone(replayed_after[0])
            self.assertIsNotNone(replayed_after[1])

    def test_content_hash_invariant_under_id_order(self) -> None:
        ids = ("a", "b", "c")
        permuted = ("c", "a", "b")
        self.assertEqual(snapshot_content_hash(ids), snapshot_content_hash(permuted))


if __name__ == "__main__":
    unittest.main()
