"""Routing tests for MemoryRouter."""
from __future__ import annotations

import unittest

from rulence.memory import (
    MemoryBackendRole,
    MemoryProviderCapabilities,
    MemoryReadRequest,
    MemoryRouter,
    MemoryWriteRequest,
)


def _cap(name: str, role: str, *, read=True, write=False) -> MemoryProviderCapabilities:
    return MemoryProviderCapabilities(
        backend_name=name,
        backend_role=role,
        supports_read=read,
        supports_write=write,
        supports_update=False,
        supports_expire=False,
        supports_search=read,
        supports_provenance=False,
        supports_delete=False,
        supports_mcp_transport=name == "mempalace",
        supports_http_transport=name == "honcho",
    )


class ReadRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = MemoryRouter(
            capabilities={
                "honcho": _cap("honcho", MemoryBackendRole.INTERNAL),
                "mempalace": _cap("mempalace", MemoryBackendRole.EXTERNAL),
            }
        )

    def test_user_preference_routes_to_honcho(self) -> None:
        decision = self.router.route_read(
            MemoryReadRequest(query="theme", scope="user_preferences")
        )
        self.assertEqual(decision.action, "allow")
        self.assertEqual(decision.backend, "honcho")
        self.assertEqual(decision.backend_role, MemoryBackendRole.INTERNAL)

    def test_project_context_routes_to_mempalace(self) -> None:
        decision = self.router.route_read(
            MemoryReadRequest(query="repo layout", scope="project_context")
        )
        self.assertEqual(decision.action, "allow")
        self.assertEqual(decision.backend, "mempalace")
        self.assertEqual(decision.backend_role, MemoryBackendRole.EXTERNAL)

    def test_unknown_scope_warns_with_fallback(self) -> None:
        decision = self.router.route_read(
            MemoryReadRequest(query="foo", scope="something_unmapped")
        )
        self.assertEqual(decision.action, "warn")
        self.assertEqual(decision.backend_role, MemoryBackendRole.UNKNOWN)
        self.assertEqual(decision.fallback_backend, "honcho")

    def test_capability_blocks_read(self) -> None:
        router = MemoryRouter(
            capabilities={
                "honcho": _cap("honcho", MemoryBackendRole.INTERNAL, read=False),
            }
        )
        decision = router.route_read(
            MemoryReadRequest(query="x", scope="user_preferences")
        )
        self.assertEqual(decision.action, "warn")
        self.assertIsNone(decision.backend)


class WriteRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = MemoryRouter(
            capabilities={
                "honcho": _cap(
                    "honcho", MemoryBackendRole.INTERNAL, read=True, write=True
                ),
                "mempalace": _cap(
                    "mempalace", MemoryBackendRole.EXTERNAL, read=True, write=True
                ),
            }
        )

    def test_user_preference_write_routes_to_honcho(self) -> None:
        decision = self.router.route_write(
            MemoryWriteRequest(content="dark mode", scope="user_preferences")
        )
        self.assertEqual(decision.action, "allow")
        self.assertEqual(decision.backend, "honcho")

    def test_architecture_decision_routes_to_mempalace(self) -> None:
        decision = self.router.route_write(
            MemoryWriteRequest(
                content="we use protobuf",
                scope="architecture_decisions",
            )
        )
        self.assertEqual(decision.action, "allow")
        self.assertEqual(decision.backend, "mempalace")

    def test_credential_like_sensitivity_is_denied(self) -> None:
        decision = self.router.route_write(
            MemoryWriteRequest(
                content="redacted",
                scope="user_preferences",
                sensitivity="credential_like",
            )
        )
        self.assertEqual(decision.action, "deny")
        self.assertIsNone(decision.backend)

    def test_sensitive_scope_is_denied(self) -> None:
        decision = self.router.route_write(
            MemoryWriteRequest(content="anything", scope="api_keys")
        )
        self.assertEqual(decision.action, "deny")

    def test_high_sensitivity_asks(self) -> None:
        decision = self.router.route_write(
            MemoryWriteRequest(
                content="release plan",
                scope="project_context",
                sensitivity="high",
            )
        )
        self.assertEqual(decision.action, "ask")
        self.assertEqual(decision.backend, "mempalace")

    def test_capability_without_write_denies(self) -> None:
        router = MemoryRouter(
            capabilities={
                "honcho": _cap("honcho", MemoryBackendRole.INTERNAL, write=False),
            }
        )
        decision = router.route_write(
            MemoryWriteRequest(content="x", scope="user_preferences")
        )
        self.assertEqual(decision.action, "deny")
        self.assertIn("does not support writes", decision.reasons[0])


if __name__ == "__main__":
    unittest.main()
