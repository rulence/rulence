"""Eval: a MemPalace read for an external scope routes to the external role."""
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


class _StubMemPalace:
    def retrieve(self, query, limit=5):
        return [MemoryItem(source="mempalace", text="auth lives in src/auth", score=2)]


def run() -> dict:
    capabilities = MemoryProviderCapabilities(
        backend_name="mempalace",
        backend_role=MemoryBackendRole.EXTERNAL,
        supports_read=True,
        supports_write=False,
        supports_update=False,
        supports_expire=False,
        supports_search=True,
        supports_provenance=False,
        supports_delete=False,
        supports_mcp_transport=True,
        supports_http_transport=False,
    )
    arbiter = MemoryArbiter(
        MemoryRouter(capabilities={"mempalace": capabilities}),
        providers={"mempalace": make_handle(_StubMemPalace(), capabilities)},
    )
    result = arbiter.read("auth", scope="project_context")
    routed_correctly = (
        result.backend == "mempalace"
        and result.backend_role == MemoryBackendRole.EXTERNAL
        and not result.degraded
    )
    return {
        "status": "pass" if routed_correctly else "fail",
        "detail": f"backend={result.backend!r} role={result.backend_role!r} degraded={result.degraded}",
        "metrics": {
            "memory_routing_external_correct": bool(routed_correctly),
            "items_returned": len(result.items),
        },
    }


EVAL = Eval(
    eval_id="memory.mempalace_external_read_routes",
    category="memory",
    description="MemPalace external-scope read routes to the external backend.",
    claim_ids=("memory_arbitration_policy", "mempalace_memory_query"),
    runner=run,
)
