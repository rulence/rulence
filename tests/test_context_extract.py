from __future__ import annotations

import unittest

from rulence.context import (
    fragments_from_audit_event,
    fragments_from_final_response,
    fragments_from_honcho_memory_item,
    fragments_from_mempalace_memory_item,
    fragments_from_tool_input,
    fragments_from_tool_result,
    fragments_from_user_prompt,
)
from rulence.memory import MemoryItem


class FragmentsFromUserPromptTests(unittest.TestCase):
    def test_user_prompt_yields_user_instruction_fragment(self) -> None:
        fragments = fragments_from_user_prompt(
            "Add audit logs.",
            runner="claude_code",
            session_id="rs_session",
            corr_id="ct_corr",
        )
        self.assertGreaterEqual(len(fragments), 1)
        self.assertEqual(fragments[0].kind, "user_instruction")
        self.assertEqual(fragments[0].source.source_type, "user_prompt")
        self.assertEqual(fragments[0].source.corr_id, "ct_corr")
        self.assertEqual(fragments[0].source.runner, "claude_code")

    def test_forbids_constraint_yields_constraint_fragment(self) -> None:
        fragments = fragments_from_user_prompt(
            "forbids: secrets -> logs\nWrite an audit log entry.",
            runner="claude_code",
            corr_id="ct_corr",
        )
        kinds = [f.kind for f in fragments]
        self.assertIn("user_instruction", kinds)
        self.assertIn("constraint", kinds)
        constraint = next(f for f in fragments if f.kind == "constraint")
        self.assertEqual(
            constraint.metadata["constraint_kind"],
            "forbids",
        )
        self.assertEqual(constraint.metadata["constraint_target"], "logs")
        self.assertEqual(constraint.source.source_type, "constraint_parser")

    def test_requires_constraint_yields_constraint_fragment(self) -> None:
        fragments = fragments_from_user_prompt(
            "requires(deploy, approval)",
        )
        kinds = [f.kind for f in fragments]
        self.assertIn("constraint", kinds)
        constraint = next(f for f in fragments if f.kind == "constraint")
        self.assertEqual(constraint.metadata["constraint_kind"], "requires")

    def test_empty_prompt_returns_no_fragments(self) -> None:
        self.assertEqual(fragments_from_user_prompt(""), ())
        self.assertEqual(fragments_from_user_prompt("   "), ())

    def test_redaction_runs_for_user_prompt_with_secret(self) -> None:
        # An OpenAI-shaped fake key. The redactor should mask it.
        prompt = "Use this key: sk-AAAAAAAAAAAAAAAAAAAA1234567890BBBB"
        fragments = fragments_from_user_prompt(prompt)
        instruction = fragments[0]
        self.assertNotIn("sk-AAAAAAAAAAAAAAAAAAAA", instruction.redacted_text)
        self.assertIn("REDACTED", instruction.redacted_text)


class FragmentsFromToolInputTests(unittest.TestCase):
    def test_tool_input_dict_yields_tool_input_fragment(self) -> None:
        fragments = fragments_from_tool_input(
            "Bash",
            {"command": "rm -rf /tmp/x"},
            runner="claude_code",
            corr_id="ct_corr",
        )
        self.assertEqual(len(fragments), 1)
        fragment = fragments[0]
        self.assertEqual(fragment.kind, "tool_input")
        self.assertEqual(fragment.source.tool_name, "Bash")
        self.assertEqual(fragment.source.runner, "claude_code")
        self.assertIn("rm -rf", fragment.text)

    def test_tool_input_none_returns_empty(self) -> None:
        self.assertEqual(fragments_from_tool_input("Bash", None), ())


class FragmentsFromToolResultTests(unittest.TestCase):
    def test_tool_result_yields_tool_result_fragment(self) -> None:
        fragments = fragments_from_tool_result(
            "Bash",
            "stdout: file removed",
            runner="claude_code",
            corr_id="ct_corr",
        )
        self.assertEqual(len(fragments), 1)
        fragment = fragments[0]
        self.assertEqual(fragment.kind, "tool_result")
        self.assertEqual(fragment.source.tool_name, "Bash")
        self.assertIn("stdout: file removed", fragment.text)

    def test_tool_result_dict_renders_to_json_text(self) -> None:
        fragments = fragments_from_tool_result(
            "Read",
            {"content": "hello", "lines": 1},
        )
        self.assertEqual(len(fragments), 1)
        text = fragments[0].text
        self.assertIn("hello", text)


class FragmentsFromMemoryItemTests(unittest.TestCase):
    def test_honcho_item_yields_honcho_memory_fragment(self) -> None:
        item = MemoryItem(
            source="honcho", text="user prefers tabs", score=3, metadata={"id": "h1"}
        )
        fragments = fragments_from_honcho_memory_item(
            item, runner="claude_code", corr_id="ct_corr"
        )
        self.assertEqual(len(fragments), 1)
        fragment = fragments[0]
        self.assertEqual(fragment.kind, "honcho_memory")
        self.assertEqual(fragment.memory_backend, "honcho")
        self.assertEqual(fragment.source.memory_backend, "honcho")
        self.assertEqual(fragment.metadata["score"], 3)
        self.assertIn("user prefers tabs", fragment.text)

    def test_mempalace_item_yields_mempalace_memory_fragment(self) -> None:
        item = MemoryItem(
            source="mempalace",
            text="auth lives in src/auth",
            score=2,
            metadata={"wing": "code", "room": "auth"},
        )
        fragments = fragments_from_mempalace_memory_item(item)
        self.assertEqual(len(fragments), 1)
        fragment = fragments[0]
        self.assertEqual(fragment.kind, "mempalace_memory")
        self.assertEqual(fragment.memory_backend, "mempalace")
        self.assertIn("auth", fragment.text)

    def test_dict_memory_item_works(self) -> None:
        fragments = fragments_from_honcho_memory_item(
            {"text": "remember this", "score": 1, "metadata": {"a": 1}}
        )
        self.assertEqual(len(fragments), 1)
        self.assertIn("remember this", fragments[0].text)

    def test_empty_memory_text_yields_no_fragment(self) -> None:
        fragments = fragments_from_honcho_memory_item(MemoryItem("honcho", "", 0))
        self.assertEqual(fragments, ())


class FragmentsFromFinalResponseTests(unittest.TestCase):
    def test_final_response_yields_final_response_fragment(self) -> None:
        fragments = fragments_from_final_response(
            "All done.\nDecision: rollback the migration.",
            runner="claude_code",
        )
        kinds = [f.kind for f in fragments]
        self.assertIn("final_response", kinds)
        # Decision pattern is deterministic; this case must be picked up.
        self.assertIn("decision", kinds)
        decision = next(f for f in fragments if f.kind == "decision")
        self.assertIn("rollback", decision.text.lower())

    def test_final_response_without_decision_pattern(self) -> None:
        fragments = fragments_from_final_response("Wrapped up the task.")
        kinds = [f.kind for f in fragments]
        self.assertIn("final_response", kinds)
        self.assertNotIn("decision", kinds)


class FragmentsFromAuditEventTests(unittest.TestCase):
    def test_audit_event_yields_audit_observation(self) -> None:
        event = {
            "event_type": "PreToolUse",
            "event_id": "ev1",
            "corr_id": "ct_corr",
            "session_id": "rs_session",
            "runner": "claude_code",
            "decision": "approve",
            "evidence": ["safe-path"],
            "tool_name": "Bash",
            "schema_version": 2,
            "raw_payload_hash": "deadbeef",
        }
        fragments = fragments_from_audit_event(event)
        self.assertEqual(len(fragments), 1)
        fragment = fragments[0]
        self.assertEqual(fragment.kind, "audit_observation")
        self.assertEqual(fragment.source.event_id, "ev1")
        self.assertEqual(fragment.source.corr_id, "ct_corr")
        self.assertEqual(fragment.source.tool_name, "Bash")
        self.assertEqual(fragment.metadata["event_type"], "PreToolUse")

    def test_audit_event_without_summary_fields_yields_nothing(self) -> None:
        self.assertEqual(fragments_from_audit_event({}), ())


class MemoryArbiterContextIntegrationTests(unittest.TestCase):
    def test_arbiter_emits_honcho_fragments_into_context_store(self) -> None:
        import tempfile
        from pathlib import Path

        from rulence.context import ContextStore
        from rulence.memory import (
            MemoryArbiter,
            MemoryProviderCapabilities,
            MemoryRouter,
            make_handle,
        )
        from rulence.memory.providers import MemoryItem
        from rulence.memory.schema import MemoryBackendRole

        class _StubProvider:
            def retrieve(self, query, limit=5):
                return [
                    MemoryItem(
                        source="honcho", text="user prefers tabs", score=2
                    ),
                    MemoryItem(
                        source="honcho",
                        text="user uses zsh",
                        score=1,
                    ),
                ]

        capabilities = MemoryProviderCapabilities(
            backend_name="honcho",
            backend_role=MemoryBackendRole.INTERNAL,
            supports_read=True,
            supports_write=False,
            supports_update=False,
            supports_expire=False,
            supports_search=True,
            supports_provenance=False,
            supports_delete=False,
            supports_mcp_transport=False,
            supports_http_transport=True,
        )
        provider = _StubProvider()
        handle = make_handle(provider, capabilities)
        router = MemoryRouter(capabilities={"honcho": capabilities})

        with tempfile.TemporaryDirectory() as tmp:
            store = ContextStore(Path(tmp))
            arbiter = MemoryArbiter(
                router,
                providers={"honcho": handle},
                context_store=store,
            )
            result = arbiter.read(
                "preferences",
                scope="user_preferences",
                corr_id="ct_corr",
            )
            self.assertFalse(result.degraded)
            self.assertEqual(len(result.items), 2)
            fragments = store.by_memory_backend("honcho")
            self.assertEqual(len(fragments), 2)
            for fragment in fragments:
                self.assertEqual(fragment.kind, "honcho_memory")
                self.assertEqual(fragment.source.corr_id, "ct_corr")


if __name__ == "__main__":
    unittest.main()
