"""Eval: a Honcho read for an internal scope routes to the internal role.

We construct a tiny stub provider with read capability and assert the
arbiter routes a ``user_preferences`` read to it.
"""
from __future__ import annotations

from rulence.evals import Eval
from rulence.memory import (
    MemoryArbiter,
    MemoryProviderCapabilities,
    MemoryRouter,
    make_handle,
)
from rulence.memory.providers import MemoryItem
from rulence.memory.schema import MemoryBackendRole


class _StubHoncho:
    def retrieve(self, query, limit=5):
        return [MemoryItem(source="honcho", text="user prefers tabs", score=2)]


def run() -> dict:
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
    arbiter = MemoryArbiter(
        MemoryRouter(capabilities={"honcho": capabilities}),
        providers={"honcho": make_handle(_StubHoncho(), capabilities)},
    )
    result = arbiter.read("preferences", scope="user_preferences")
    routed_correctly = (
        result.backend == "honcho"
        and result.backend_role == MemoryBackendRole.INTERNAL
        and not result.degraded
    )
    return {
        "status": "pass" if routed_correctly else "fail",
        "detail": f"backend={result.backend!r} role={result.backend_role!r} degraded={result.degraded}",
        "metrics": {
            "memory_routing_internal_correct": bool(routed_correctly),
            "items_returned": len(result.items),
        },
    }


EVAL = Eval(
    eval_id="memory.honcho_internal_read_routes",
    category="memory",
    description="Honcho internal-scope read routes to the internal backend.",
    claim_ids=("memory_arbitration_policy", "honcho_memory_query"),
    runner=run,
)
