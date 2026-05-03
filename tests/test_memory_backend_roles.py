"""Tests for the canonical Honcho/MemPalace backend role mapping."""
from __future__ import annotations

import unittest

from rulence.memory import (
    CANONICAL_BACKEND_ROLES,
    HonchoMemoryProvider,
    LocalFileMemoryProvider,
    MemPalaceMemoryProvider,
    MempalaceMcpProvider,
    MemoryBackendRole,
    capabilities_for,
)


class CanonicalRoleTests(unittest.TestCase):
    def test_honcho_is_internal(self) -> None:
        self.assertEqual(CANONICAL_BACKEND_ROLES["honcho"], MemoryBackendRole.INTERNAL)

    def test_mempalace_is_external(self) -> None:
        self.assertEqual(
            CANONICAL_BACKEND_ROLES["mempalace"], MemoryBackendRole.EXTERNAL
        )


class CapabilitiesForExistingProvidersTests(unittest.TestCase):
    def test_honcho_provider_capabilities(self) -> None:
        provider = HonchoMemoryProvider(base_url="http://127.0.0.1:9999")
        cap = capabilities_for(provider)
        self.assertEqual(cap.backend_name, "honcho")
        self.assertEqual(cap.backend_role, MemoryBackendRole.INTERNAL)
        self.assertTrue(cap.supports_read)
        self.assertFalse(cap.supports_write, "Honcho HTTP adapter does not write yet")
        self.assertTrue(cap.supports_http_transport)
        self.assertFalse(cap.supports_mcp_transport)

    def test_mempalace_mcp_provider_capabilities(self) -> None:
        provider = MempalaceMcpProvider()
        cap = capabilities_for(provider)
        self.assertEqual(cap.backend_name, "mempalace")
        self.assertEqual(cap.backend_role, MemoryBackendRole.EXTERNAL)
        self.assertTrue(cap.supports_read)
        self.assertFalse(
            cap.supports_write, "MempalaceMcpProvider does not write yet"
        )
        self.assertTrue(cap.supports_mcp_transport)
        self.assertFalse(cap.supports_http_transport)

    def test_mempalace_http_provider_capabilities(self) -> None:
        provider = MemPalaceMemoryProvider(base_url="http://127.0.0.1:8788")
        cap = capabilities_for(provider)
        self.assertEqual(cap.backend_name, "mempalace")
        self.assertEqual(cap.backend_role, MemoryBackendRole.EXTERNAL)
        self.assertTrue(cap.supports_http_transport)

    def test_local_file_provider_role_is_unknown(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".md") as f:
            provider = LocalFileMemoryProvider(f.name)
        cap = capabilities_for(provider)
        self.assertEqual(cap.backend_name, "local_file")
        self.assertEqual(cap.backend_role, MemoryBackendRole.UNKNOWN)
        self.assertTrue(cap.supports_read)
        self.assertFalse(cap.supports_write)


if __name__ == "__main__":
    unittest.main()
