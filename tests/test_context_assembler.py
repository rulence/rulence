from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rulence.context import (
    ContextAssembler,
    ContextFragment,
    ContextStore,
    SourceRef,
    fragments_from_user_prompt,
)
from rulence.memory import MemoryItem


def _fragment(
    *,
    kind: str = "user_instruction",
    text: str = "do the work",
    fragment_id: str | None = None,
    metadata: dict | None = None,
    memory_backend: str | None = None,
    corr_id: str | None = "ct_corr",
    token_estimate: int | None = None,
) -> ContextFragment:
    source = SourceRef(
        source_type="user_prompt",
        runner="claude_code",
        memory_backend=memory_backend,
        corr_id=corr_id,
    )
    return ContextFragment.build(
        kind=kind,
        text=text,
        source=source,
        memory_backend=memory_backend,
        fragment_id=fragment_id,
        metadata=metadata or {},
        token_estimate=token_estimate,
    )


class ContextAssemblerTests(unittest.TestCase):
    def test_assembles_from_extra_fragments_without_a_store(self) -> None:
        a = _fragment(text="auth module fixes", fragment_id="a")
        b = _fragment(text="random pizza chatter", fragment_id="b")
        asm = ContextAssembler()
        result = asm.assemble(
            "auth module fixes",
            extra_fragments=(a, b),
            max_tokens=2000,
        )
        ids = list(result.snapshot.fragment_ids)
        # Both fit under the budget, both required (user_instruction).
        self.assertIn("a", ids)
        self.assertIn("b", ids)
        # a should rank above b due to lexical overlap.
        ranked_ids = [score.fragment_id for score in result.scores]
        self.assertEqual(ranked_ids[0], "a")

    def test_assembly_pulls_from_store_filtered_by_corr_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ContextStore(Path(tmp))
            for fragment in fragments_from_user_prompt(
                "forbids: secrets -> logs\nAdd audit logs",
                corr_id="ct_match",
            ):
                store.add(fragment)
            # Unrelated fragment in another corr_id.
            store.add(_fragment(text="other stuff", corr_id="ct_other"))

            asm = ContextAssembler(store=store)
            result = asm.assemble(
                "Add audit logs",
                corr_id="ct_match",
                max_tokens=2000,
            )
            ids = result.snapshot.fragment_ids
            self.assertGreater(len(ids), 0)
            # No fragment from the other corr_id leaked through.
            for fragment in result.budget.included_fragments:
                self.assertEqual(fragment.source.corr_id, "ct_match")

    def test_duplicate_fragments_are_removed(self) -> None:
        a = _fragment(text="duplicate text", fragment_id="a")
        b = _fragment(text="duplicate text", fragment_id="b")
        asm = ContextAssembler()
        result = asm.assemble(
            "duplicate text", extra_fragments=(a, b), max_tokens=1000
        )
        included_ids = [f.fragment_id for f in result.budget.included_fragments]
        # Only one of the two duplicates should make the cut.
        self.assertEqual(len(included_ids), 1)

    def test_over_budget_fits_required_and_drops_optional(self) -> None:
        forbid = _fragment(
            kind="constraint",
            text="forbids(secrets, logs)",
            metadata={"constraint_kind": "forbids"},
            fragment_id="forbid",
            token_estimate=20,
        )
        big_a = _fragment(
            kind="tool_result", text="aaa", fragment_id="big_a", token_estimate=200
        )
        big_b = _fragment(
            kind="tool_result", text="bbb", fragment_id="big_b", token_estimate=200
        )
        asm = ContextAssembler()
        result = asm.assemble(
            "do the thing",
            extra_fragments=(forbid, big_a, big_b),
            max_tokens=100,
            compress=False,
        )
        included = [f.fragment_id for f in result.budget.included_fragments]
        self.assertIn("forbid", included)
        # At least one big tool_result should be dropped.
        self.assertGreaterEqual(len(result.budget.dropped_fragments), 1)
        for fragment in result.budget.dropped_fragments:
            self.assertIn(fragment.fragment_id, result.budget.drop_reasons)

    def test_assembler_includes_honcho_and_mempalace_items(self) -> None:
        asm = ContextAssembler()
        honcho = MemoryItem(source="honcho", text="user prefers tabs", score=2)
        mempalace = MemoryItem(
            source="mempalace", text="auth lives in src/auth", score=2
        )
        result = asm.assemble(
            "auth module changes",
            honcho_items=(honcho,),
            mempalace_items=(mempalace,),
            max_tokens=2000,
        )
        kinds = [
            fragment.kind for fragment in result.budget.included_fragments
        ]
        self.assertIn("honcho_memory", kinds)
        self.assertIn("mempalace_memory", kinds)
        # Snapshot records both backends.
        self.assertIn("honcho", result.snapshot.memory_backends_used)
        self.assertIn("mempalace", result.snapshot.memory_backends_used)

    def test_compress_attaches_compressed_text(self) -> None:
        asm = ContextAssembler()
        forbid = _fragment(
            kind="constraint",
            text="forbids(secrets, logs)",
            metadata={"constraint_kind": "forbids"},
            fragment_id="forbid",
        )
        instr = _fragment(
            kind="user_instruction",
            text="add audit logs",
            fragment_id="instr",
        )
        result = asm.assemble(
            "add audit logs",
            extra_fragments=(forbid, instr),
            max_tokens=2000,
        )
        self.assertIsNotNone(result.compressed)
        self.assertIn("forbid", result.compressed.text)
        self.assertIn("instr", result.compressed.text)
        self.assertIn("forbids(secrets, logs)", result.compressed.preserved_constraints)
        self.assertEqual(result.compressed.validation_status, "pass")

    def test_dropped_fragment_ids_are_returned(self) -> None:
        asm = ContextAssembler()
        big_a = _fragment(
            kind="tool_result", text="a", token_estimate=200, fragment_id="big_a"
        )
        result = asm.assemble(
            "anything",
            extra_fragments=(big_a,),
            max_tokens=10,
            compress=False,
        )
        self.assertIn("big_a", result.dropped_fragment_ids)


if __name__ == "__main__":
    unittest.main()
