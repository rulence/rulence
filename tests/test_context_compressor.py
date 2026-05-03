from __future__ import annotations

import unittest

from rulence.context import (
    ContextCompressor,
    ContextFragment,
    SourceRef,
)


def _fragment(
    *,
    kind: str = "user_instruction",
    text: str = "do the work",
    fragment_id: str | None = None,
    metadata: dict | None = None,
    memory_backend: str | None = None,
    token_estimate: int | None = None,
) -> ContextFragment:
    source = SourceRef(
        source_type="user_prompt",
        runner="claude_code",
        memory_backend=memory_backend,
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


class ContextCompressorTests(unittest.TestCase):
    def test_constraints_survive_compression(self) -> None:
        c = ContextCompressor()
        fragments = [
            _fragment(kind="user_instruction", text="Add audit logs", fragment_id="u1"),
            _fragment(
                kind="constraint",
                text="forbids(secrets, logs)",
                metadata={"constraint_kind": "forbids"},
                fragment_id="c1",
            ),
        ]
        result = c.compress(fragments)
        self.assertIn("forbids(secrets, logs)", result.text)
        self.assertIn("forbids(secrets, logs)", result.preserved_constraints)
        self.assertEqual(result.validation_status, "pass")

    def test_decisions_survive_compression(self) -> None:
        c = ContextCompressor()
        fragments = [
            _fragment(
                kind="decision",
                text="rollback the migration",
                fragment_id="d1",
            )
        ]
        result = c.compress(fragments)
        self.assertIn("rollback the migration", result.text)
        self.assertIn("rollback the migration", result.preserved_decisions)
        self.assertEqual(result.validation_status, "pass")

    def test_open_blockers_survive_compression(self) -> None:
        c = ContextCompressor()
        fragments = [
            _fragment(
                kind="open_question",
                text="who approves the migration?",
                fragment_id="o1",
            )
        ]
        result = c.compress(fragments)
        self.assertIn("who approves the migration?", result.text)
        self.assertEqual(result.validation_status, "pass")

    def test_compressed_result_has_source_ids(self) -> None:
        c = ContextCompressor()
        fragments = [
            _fragment(text="alpha", fragment_id="frag_alpha"),
            _fragment(
                kind="constraint",
                text="forbids(a, b)",
                metadata={"constraint_kind": "forbids"},
                fragment_id="frag_constraint",
            ),
        ]
        result = c.compress(fragments)
        for fid in ("frag_alpha", "frag_constraint"):
            self.assertIn(fid, result.text)
            self.assertIn(fid, result.source_fragment_ids)

    def test_token_estimate_drops_after_collapsing_repeats(self) -> None:
        c = ContextCompressor()
        repeated_log = "ERROR x\n" * 60
        fragments = [
            _fragment(
                kind="tool_result",
                text=repeated_log,
                fragment_id="tr1",
                token_estimate=120,
            )
        ]
        result = c.compress(fragments)
        self.assertLess(result.token_estimate_after, result.token_estimate_before)
        # The collapsed log should appear once with a count suffix.
        self.assertIn("ERROR x  (x", result.text)

    def test_verbose_tool_output_is_truncated(self) -> None:
        c = ContextCompressor(tool_result_line_cap=4)
        long_lines = "\n".join(f"line {i}" for i in range(40))
        fragments = [
            _fragment(
                kind="tool_result",
                text=long_lines,
                fragment_id="tr_big",
                token_estimate=80,
            )
        ]
        result = c.compress(fragments)
        self.assertIn("...truncated...", result.text)
        # Token count should drop after truncation.
        self.assertLess(result.token_estimate_after, result.token_estimate_before)

    def test_memory_backend_provenance_is_preserved(self) -> None:
        c = ContextCompressor()
        fragments = [
            _fragment(
                kind="honcho_memory",
                text="user prefers tabs",
                memory_backend="honcho",
                fragment_id="h1",
            ),
            _fragment(
                kind="mempalace_memory",
                text="auth lives in src/auth",
                memory_backend="mempalace",
                fragment_id="m1",
            ),
        ]
        result = c.compress(fragments)
        self.assertIn("honcho", result.text.lower())
        self.assertIn("mempalace", result.text.lower())
        self.assertIn("h1", result.text)
        self.assertIn("m1", result.text)
        self.assertEqual(result.validation_status, "pass")

    def test_duplicate_text_is_omitted(self) -> None:
        c = ContextCompressor()
        a = _fragment(text="The same line", fragment_id="a")
        b = _fragment(text="The same line", fragment_id="b")
        result = c.compress([a, b])
        # b should be omitted (duplicate text), a should remain.
        self.assertIn("a", result.source_fragment_ids)
        self.assertNotIn("b", result.source_fragment_ids)
        self.assertIn("b", result.omitted_fragment_ids)

    def test_validation_fails_when_constraint_text_is_stripped(self) -> None:
        # Subclass the compressor with a buggy override to confirm the
        # validator catches a missing constraint.
        class BrokenCompressor(ContextCompressor):
            @staticmethod
            def _tag(fragment, body):
                # Drop everything after the prefix to simulate corruption.
                return f"[{fragment.kind}/{fragment.fragment_id}]"

        c = BrokenCompressor()
        fragments = [
            _fragment(
                kind="constraint",
                text="forbids(secrets, logs)",
                metadata={"constraint_kind": "forbids"},
                fragment_id="c1",
            )
        ]
        result = c.compress(fragments)
        self.assertEqual(result.validation_status, "fail")
        joined = " | ".join(result.validation_errors)
        self.assertIn("missing constraint", joined)


class BenchmarkReductionTests(unittest.TestCase):
    """Local benchmark: compression must reduce tokens while preserving
    constraints and decisions on a representative input."""

    def test_compression_reduces_tokens_while_preserving_constraints(self) -> None:
        c = ContextCompressor()
        repeated_log = "repeated tool output line\n" * 80
        fragments = [
            _fragment(
                kind="user_instruction",
                text="Add audit logs without leaking secrets.",
                fragment_id="instr",
            ),
            _fragment(
                kind="constraint",
                text="forbids(secrets, logs)",
                metadata={"constraint_kind": "forbids"},
                fragment_id="forbid",
            ),
            _fragment(
                kind="decision",
                text="rollback the migration",
                fragment_id="decision_1",
            ),
            _fragment(
                kind="tool_result",
                text=repeated_log,
                fragment_id="tr_repeat",
                token_estimate=200,
            ),
        ]
        result = c.compress(fragments)
        self.assertLess(result.token_estimate_after, result.token_estimate_before)
        self.assertIn("forbids(secrets, logs)", result.text)
        self.assertIn("rollback the migration", result.text)
        self.assertEqual(result.validation_status, "pass")


if __name__ == "__main__":
    unittest.main()
