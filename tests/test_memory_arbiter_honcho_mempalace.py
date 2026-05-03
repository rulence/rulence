"""MemoryArbiter integration tests with synthetic Honcho / MemPalace adapters.

These tests stand up *fake* providers — no network calls, no real
backend interactions. The fakes only implement the contract the
arbiter needs: ``retrieve(query, limit)`` for reads and ``write(text,
scope, provenance)`` for writes (when supported).
"""
from __future__ import annotations

import unittest

from rulence.memory import (
    MemoryArbiter,
    MemoryBackendRole,
    MemoryItem,
    MemoryProviderCapabilities,
    MemoryRouter,
    make_handle,
)

FAKE_GITHUB_TOKEN = "ghp_FAKEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


class _FakeReadOnlyHoncho:
    name = "honcho"

    def __init__(self) -> None:
        self.queries: list[tuple[str, int]] = []

    def retrieve(self, query: str, limit: int = 5) -> list[MemoryItem]:
        self.queries.append((query, limit))
        return [MemoryItem(source="honcho", text=f"answer for {query}", score=1)]


class _FakeWritableMemPalace:
    name = "mempalace"

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, dict]] = []

    def retrieve(self, query: str, limit: int = 5) -> list[MemoryItem]:
        return [MemoryItem(source="mempalace", text=f"memory: {query}", score=2)]

    def write(self, text: str, *, scope: str, provenance: dict) -> bool:
        self.writes.append((text, scope, provenance))
        return True


def _honcho_caps(*, write: bool = False) -> MemoryProviderCapabilities:
    return MemoryProviderCapabilities(
        backend_name="honcho",
        backend_role=MemoryBackendRole.INTERNAL,
        supports_read=True,
        supports_write=write,
        supports_update=False,
        supports_expire=False,
        supports_search=True,
        supports_provenance=write,
        supports_delete=False,
        supports_mcp_transport=False,
        supports_http_transport=True,
    )


def _mempalace_caps(*, write: bool = True) -> MemoryProviderCapabilities:
    return MemoryProviderCapabilities(
        backend_name="mempalace",
        backend_role=MemoryBackendRole.EXTERNAL,
        supports_read=True,
        supports_write=write,
        supports_update=False,
        supports_expire=False,
        supports_search=True,
        supports_provenance=True,
        supports_delete=False,
        supports_mcp_transport=True,
        supports_http_transport=False,
    )


class ArbiterReadTests(unittest.TestCase):
    def test_read_user_preference_uses_honcho(self) -> None:
        honcho = _FakeReadOnlyHoncho()
        mempalace = _FakeWritableMemPalace()
        arbiter = MemoryArbiter(
            providers={
                "honcho": make_handle(honcho, _honcho_caps()),
                "mempalace": make_handle(mempalace, _mempalace_caps()),
            }
        )
        result = arbiter.read("dark mode", scope="user_preferences")
        self.assertEqual(result.backend, "honcho")
        self.assertEqual(result.backend_role, MemoryBackendRole.INTERNAL)
        self.assertFalse(result.degraded)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(honcho.queries, [("dark mode", 5)])

    def test_read_project_context_uses_mempalace(self) -> None:
        honcho = _FakeReadOnlyHoncho()
        mempalace = _FakeWritableMemPalace()
        arbiter = MemoryArbiter(
            providers={
                "honcho": make_handle(honcho, _honcho_caps()),
                "mempalace": make_handle(mempalace, _mempalace_caps()),
            }
        )
        result = arbiter.read("repo layout", scope="project_context")
        self.assertEqual(result.backend, "mempalace")
        self.assertEqual(result.backend_role, MemoryBackendRole.EXTERNAL)

    def test_read_with_no_provider_is_degraded(self) -> None:
        # No providers configured; arbiter should fail safely.
        arbiter = MemoryArbiter()
        result = arbiter.read("anything", scope="user_preferences")
        self.assertTrue(result.degraded)
        self.assertEqual(result.items, ())
        self.assertIn("not configured", result.error or "")


class ArbiterWriteTests(unittest.TestCase):
    def test_write_routes_to_mempalace_for_project_context(self) -> None:
        mempalace = _FakeWritableMemPalace()
        arbiter = MemoryArbiter(
            providers={
                "mempalace": make_handle(mempalace, _mempalace_caps(write=True)),
            }
        )
        result = arbiter.write(
            "we use protobuf for the wire format",
            scope="architecture_decisions",
            session_id="rs_x",
            corr_id="ct_a",
            runner="claude_code",
            tool="Edit",
            source_event_id="e1",
        )
        self.assertTrue(result.written)
        self.assertEqual(result.backend, "mempalace")
        self.assertEqual(result.backend_role, MemoryBackendRole.EXTERNAL)
        self.assertIsNotNone(result.provenance)
        # Provenance is recorded with content hash, not raw content.
        provenance = result.provenance
        self.assertIsNotNone(provenance)
        self.assertTrue(provenance.content_hash.startswith("sha256:"))
        self.assertEqual(provenance.source_corr_id, "ct_a")
        self.assertEqual(provenance.source_session_id, "rs_x")
        self.assertEqual(provenance.source_runner, "claude_code")
        # The fake backend received exactly one write.
        self.assertEqual(len(mempalace.writes), 1)

    def test_write_redacts_secret_and_then_denies(self) -> None:
        mempalace = _FakeWritableMemPalace()
        arbiter = MemoryArbiter(
            providers={
                "mempalace": make_handle(mempalace, _mempalace_caps(write=True)),
            }
        )
        result = arbiter.write(
            f"we leaked {FAKE_GITHUB_TOKEN}",
            scope="project_context",
        )
        # Original content carried a secret. The arbiter escalates to
        # credential_like and denies the write.
        self.assertFalse(result.written)
        self.assertIsNone(result.provenance)
        self.assertEqual(result.redaction_count, 1)
        self.assertIn("credential", (result.reason or "").lower())
        self.assertEqual(mempalace.writes, [], "no write should reach the backend")

    def test_write_to_backend_without_write_capability_fails_safely(self) -> None:
        honcho = _FakeReadOnlyHoncho()
        arbiter = MemoryArbiter(
            providers={
                "honcho": make_handle(honcho, _honcho_caps(write=False)),
            }
        )
        result = arbiter.write("dark mode", scope="user_preferences")
        self.assertFalse(result.written)
        self.assertIn("does not support writes", result.reason or "")
        # The provider must not have been called for write.

    def test_write_with_no_backend_for_scope_is_degraded(self) -> None:
        arbiter = MemoryArbiter()
        result = arbiter.write("rev", scope="some_unmapped_scope")
        self.assertFalse(result.written)
        self.assertTrue(result.degraded)

    def test_write_attaches_provenance_with_redaction_count(self) -> None:
        mempalace = _FakeWritableMemPalace()
        arbiter = MemoryArbiter(
            providers={
                "mempalace": make_handle(mempalace, _mempalace_caps(write=True)),
            }
        )
        result = arbiter.write(
            "we decided to use a queue",
            scope="architecture_decisions",
        )
        self.assertEqual(result.redaction_count, 0)
        self.assertIsNotNone(result.provenance)
        provenance = result.provenance
        self.assertIsNotNone(provenance)
        self.assertEqual(provenance.redaction_count, 0)
        self.assertEqual(provenance.source_memory_backend, "mempalace")


if __name__ == "__main__":
    unittest.main()
