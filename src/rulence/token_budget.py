from __future__ import annotations

import math

from .models import TokenBudget


def estimate_tokens(text: str, model: str | None = None) -> tuple[int, str]:
    if not text:
        return 0, "empty"

    try:
        import tiktoken  # type: ignore
    except ImportError:
        return max(1, math.ceil(len(text) / 4)), "heuristic: ceil(chars/4)"

    try:
        encoding = tiktoken.encoding_for_model(model or "gpt-4o")
    except Exception:
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text)), f"tiktoken:{model or 'gpt-4o'}"


def build_token_budget(
    task: str,
    memory: str,
    required_checks: tuple[str, ...],
    tier: int,
    model: str | None = None,
) -> TokenBudget:
    task_tokens, task_method = estimate_tokens(task, model)
    memory_tokens, memory_method = estimate_tokens(memory, model)
    policy_overhead = 120 + 55 * len(required_checks)
    governed_context = task_tokens + min(memory_tokens, 600) + policy_overhead + (tier * 130)
    method = task_method if task_method == memory_method else f"{task_method}; {memory_method}"
    return TokenBudget(
        input_estimate=task_tokens + memory_tokens,
        governed_context_estimate=governed_context,
        policy_overhead_estimate=policy_overhead,
        method=f"{method}; governed estimate only, not a savings benchmark",
    )
