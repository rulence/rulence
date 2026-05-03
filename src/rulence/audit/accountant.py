"""Token accounting and rollups across audit traces.

This module is intentionally an *estimate* layer. Counts come from
``rulence.token_budget.estimate_tokens`` (tiktoken when installed,
fallback heuristic otherwise). They are not provider-billed actuals.

Three value types are exposed:

* :class:`TokenEstimate` — a single estimate attached to one audit
  event (e.g. UserPromptSubmit prompt size).
* :class:`TokenRollup` — aggregated estimates for a session or
  collection of audit records, grouped several ways.
* :class:`TokenBudget` — optional thresholds. Exceeding any threshold
  produces a warning string; M7 does not enforce deny.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..token_budget import estimate_tokens

# Memory-server prefixes (lower-case server segment of mcp__SERVER__op).
_MEMORY_BACKENDS = frozenset({"memory", "honcho", "mempalace"})


@dataclass(frozen=True)
class TokenEstimate:
    event_type: str
    session_id: str
    corr_id: str | None
    model: str | None
    input_estimate: int
    output_estimate: int
    payload_kind: str
    estimator_name: str
    provider_reported_input: int | None = None
    provider_reported_output: int | None = None
    is_estimate: bool = True

    @property
    def total(self) -> int:
        return self.input_estimate + self.output_estimate

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "session_id": self.session_id,
            "corr_id": self.corr_id,
            "model": self.model,
            "input_estimate": self.input_estimate,
            "output_estimate": self.output_estimate,
            "payload_kind": self.payload_kind,
            "estimator_name": self.estimator_name,
            "provider_reported_input": self.provider_reported_input,
            "provider_reported_output": self.provider_reported_output,
            "is_estimate": self.is_estimate,
        }


@dataclass(frozen=True)
class TokenRollup:
    total_estimated_input: int
    total_estimated_output: int
    total_estimated: int
    by_event_type: dict[str, int]
    by_corr_id: dict[str, int]
    by_tool: dict[str, int]
    by_memory_backend: dict[str, int]
    rulence_overhead_estimate: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_estimated_input": self.total_estimated_input,
            "total_estimated_output": self.total_estimated_output,
            "total_estimated": self.total_estimated,
            "by_event_type": dict(self.by_event_type),
            "by_corr_id": dict(self.by_corr_id),
            "by_tool": dict(self.by_tool),
            "by_memory_backend": dict(self.by_memory_backend),
            "rulence_overhead_estimate": self.rulence_overhead_estimate,
        }


@dataclass(frozen=True)
class TokenBudget:
    """Optional per-event and per-session thresholds.

    M7 only emits warning strings when a threshold is exceeded; it
    does not enforce deny. Setting a field to ``None`` disables the
    corresponding threshold.
    """

    max_user_prompt_tokens: int | None = None
    max_tool_input_tokens: int | None = None
    max_tool_output_tokens: int | None = None
    max_session_estimated_tokens: int | None = None
    max_memory_result_tokens: int | None = None
    on_exceed: str = "warn"  # warn | ask | deny — only "warn" honored in M7

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_user_prompt_tokens": self.max_user_prompt_tokens,
            "max_tool_input_tokens": self.max_tool_input_tokens,
            "max_tool_output_tokens": self.max_tool_output_tokens,
            "max_session_estimated_tokens": self.max_session_estimated_tokens,
            "max_memory_result_tokens": self.max_memory_result_tokens,
            "on_exceed": self.on_exceed,
        }


class TokenAccountant:
    """Estimate tokens for a single payload and aggregate across records."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model

    # ------------------------------------------------------------------
    # Per-event estimation

    def estimate(
        self,
        text: str,
        *,
        kind: str,
        event_type: str,
        session_id: str,
        corr_id: str | None,
    ) -> TokenEstimate:
        if not text:
            count, method = 0, "none"
        else:
            count, method = estimate_tokens(text, self.model)

        if kind in ("user_prompt", "tool_input", "memory_query"):
            input_estimate, output_estimate = count, 0
        elif kind in ("tool_output", "final_response", "memory_result"):
            input_estimate, output_estimate = 0, count
        else:
            input_estimate, output_estimate = count, 0

        return TokenEstimate(
            event_type=event_type,
            session_id=session_id,
            corr_id=corr_id,
            model=self.model,
            input_estimate=input_estimate,
            output_estimate=output_estimate,
            payload_kind=kind,
            estimator_name=method,
            provider_reported_input=None,
            provider_reported_output=None,
            is_estimate=True,
        )

    # ------------------------------------------------------------------
    # Rollup over an existing list of audit records

    def rollup(self, audit_records: Iterable[dict[str, Any]]) -> TokenRollup:
        total_in = 0
        total_out = 0
        by_event: dict[str, int] = {}
        by_corr: dict[str, int] = {}
        by_tool: dict[str, int] = {}
        by_memory: dict[str, int] = {}
        overhead = 0

        for record in audit_records:
            meta = record.get("metadata") or {}
            ev_type = str(record.get("event_type") or "Unknown")
            corr_id = record.get("corr_id") or "_no_turn"
            tool_name = record.get("tool_name") or ""

            in_est = int(meta.get("input_estimate") or 0)
            out_est = int(meta.get("output_estimate") or 0)
            # PostToolUse from M5 also writes original_token_estimate; prefer
            # the explicit input/output_estimate when present.
            if out_est == 0 and ev_type == "PostToolUse":
                out_est = int(meta.get("original_token_estimate") or 0)

            total_in += in_est
            total_out += out_est
            by_event[ev_type] = by_event.get(ev_type, 0) + in_est + out_est
            by_corr[str(corr_id)] = by_corr.get(str(corr_id), 0) + in_est + out_est
            if tool_name:
                by_tool[tool_name] = by_tool.get(tool_name, 0) + in_est + out_est

            backend = _memory_backend_from_tool_name(tool_name)
            if backend is not None:
                by_memory[backend] = by_memory.get(backend, 0) + in_est + out_est

            # Governance overhead: tokens added by Rulence on top of the
            # actual user/model exchange. We approximate it as the
            # estimator over evidence text (small, but accumulates).
            evidence = record.get("evidence") or []
            if isinstance(evidence, list) and evidence:
                joined = "\n".join(str(e) for e in evidence)
                count, _ = estimate_tokens(joined, self.model)
                overhead += count

        return TokenRollup(
            total_estimated_input=total_in,
            total_estimated_output=total_out,
            total_estimated=total_in + total_out,
            by_event_type=by_event,
            by_corr_id=by_corr,
            by_tool=by_tool,
            by_memory_backend=by_memory,
            rulence_overhead_estimate=overhead,
        )

    # ------------------------------------------------------------------
    # Budget checks (warning-only in M7)

    def check_budget(
        self,
        records: Iterable[dict[str, Any]],
        budget: TokenBudget,
    ) -> list[str]:
        records = list(records)
        warnings: list[str] = []

        for record in records:
            meta = record.get("metadata") or {}
            ev_type = str(record.get("event_type") or "")
            in_est = int(meta.get("input_estimate") or 0)
            out_est = int(meta.get("output_estimate") or 0)
            if (
                ev_type == "UserPromptSubmit"
                and budget.max_user_prompt_tokens is not None
                and in_est > budget.max_user_prompt_tokens
            ):
                warnings.append(
                    f"UserPromptSubmit input {in_est} > "
                    f"max_user_prompt_tokens {budget.max_user_prompt_tokens}"
                )
            if (
                ev_type == "PreToolUse"
                and budget.max_tool_input_tokens is not None
                and in_est > budget.max_tool_input_tokens
            ):
                warnings.append(
                    f"PreToolUse input {in_est} > "
                    f"max_tool_input_tokens {budget.max_tool_input_tokens}"
                )
            if (
                ev_type == "PostToolUse"
                and budget.max_tool_output_tokens is not None
                and out_est > budget.max_tool_output_tokens
            ):
                warnings.append(
                    f"PostToolUse output {out_est} > "
                    f"max_tool_output_tokens {budget.max_tool_output_tokens}"
                )
            tool = record.get("tool_name") or ""
            backend = _memory_backend_from_tool_name(tool)
            if (
                backend is not None
                and budget.max_memory_result_tokens is not None
                and (in_est + out_est) > budget.max_memory_result_tokens
            ):
                warnings.append(
                    f"memory backend {backend} result {(in_est + out_est)} > "
                    f"max_memory_result_tokens {budget.max_memory_result_tokens}"
                )

        rollup = self.rollup(records)
        if (
            budget.max_session_estimated_tokens is not None
            and rollup.total_estimated > budget.max_session_estimated_tokens
        ):
            warnings.append(
                f"session total {rollup.total_estimated} > "
                f"max_session_estimated_tokens {budget.max_session_estimated_tokens}"
            )
        return warnings


def _memory_backend_from_tool_name(tool_name: str) -> str | None:
    if not tool_name or not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__", 2)
    if len(parts) != 3:
        return None
    server = parts[1].lower()
    if server in _MEMORY_BACKENDS:
        return server
    return None
