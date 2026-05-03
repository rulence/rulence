# Rulence Claims

This document tracks what Rulence is allowed to say in user-facing copy. The
canonical source is [`docs/claims.yml`](claims.yml), which is loaded and
verified by `src/rulence/claims.py`. Run:

```bash
rulence claims verify
```

to validate the ledger and scan public copy for blocked phrases.

The ledger uses five status values:

- **supported** — implemented in code today, with evidence and tests.
- **partial** — implemented but with caveats noted in the ledger entry.
- **advisory** — Rulence emits a verdict or rule, but enforcement depends on
  the host runner or the model honoring it.
- **aspirational** — roadmap; not implemented; only allowed in copy if
  explicitly marked roadmap/future/planned.
- **unsupported** — must not be claimed publicly, full stop.

## Memory positioning

Honcho is the canonical internal memory system. MemPalace is the canonical
external memory system. Rulence does not replace them. Rulence routes and
merges reads where configured (see `memory_combined_dedup`). Anything beyond
read-merge is roadmap (`memory_arbitration_policy`, `memory_redaction`).

Safe wording:

- "Rulence routes and governs memory access across Honcho and MemPalace where
  configured."
- "Rulence arbitrates Honcho and MemPalace memory access."

### Memory wording to avoid (do not use these phrases publicly)

- "Rulence is the memory system."
- "Rulence is the memory backend."
- "Rulence stores all memory."
- "Rulence replaces Honcho/MemPalace."

## Supported claims today

These are runtime-enforced or have tests covering current behavior:

- `claude_code_pretooluse_block` — real PreToolUse hook denies/asks in
  Claude Code.
- `tier_classifier` — deterministic tier classification.
- `policy_preflight` — approve/warn/block verdicts.
- `local_first_no_telemetry` — no telemetry, no model dependency, no
  database.
- `sequential_thinking_compat` — drop-in MCP tool name and schema.
- `decomposition_dag` — typed task DAG.
- `constraint_solver` — heuristic requires/forbids conflict detection.
- `starter_policies` — bundled policies for secrets, git, migrations, AWS,
  payments.
- `policy_validation` — `rulence policy validate`.
- `policy_regression_runner` — `rulence policy test`.
- `mcp_stdio_server` — local MCP stdio server.
- `memory_provider_health` — provider reachability checks.

## Partial claims

These work but with explicit caveats:

- `local_session_traces` — local JSON traces; user-writable; no enterprise
  audit features yet (no central distribution, no signing).
- `honcho_memory_query` — read-only; no policy gating; no redaction.
- `mempalace_memory_query` — read-only; no policy gating; no redaction.
- `memory_combined_dedup` — combined retrieval with provider priority, but
  this is read-merge only; no write arbitration, no policy enforcement.
- `cross_runtime_consistency` — same policy file works across runners; real
  enforcement only where the runner has a hook.

## Advisory-only claims

These describe surfaces Rulence ships but cannot enforce on its own:

- `cursor_rule_advisory` — writes a project rule for Cursor; the model
  decides whether to honor it.
- `n8n_mcp_advisory` — prints n8n MCP config; whether the workflow consults
  Rulence is up to the workflow author.
- `transcript_aware_checks` — opt-in policy; only runs when the host or
  caller supplies a transcript.

## Aspirational claims (roadmap)

Allowed in copy only when explicitly marked as roadmap/future/planned:

- `memory_arbitration_policy` — policy-enforced memory arbitration with
  write routing and redaction.
- `memory_redaction` — automatic secret/PII redaction across memory and
  trace surfaces.
- `context_intelligence` — active context summarization, pruning, or
  rewriting of the agent's working context.
- `runtime_governance_proxy` — generic interception of every tool call
  across runtimes.

What must be built before each aspirational claim can be used publicly:

- `memory_arbitration_policy`: a memory router with policy-enforced
  read/write decisions and an audit log.
- `memory_redaction`: a redaction pipeline applied to memory reads, memory
  writes, and trace persistence.
- `context_intelligence`: a context engine that owns
  summarize/prune/rewrite, with measured benchmarks before any
  efficiency claim.
- `runtime_governance_proxy`: per-runtime hooks (or a generic proxy) that
  intercept every tool call before execution.

## Unsupported claims (avoid these phrases publicly)

These claims are not true today and are not on a known path. The verifier
treats every phrase below as a blocked phrase outside roadmap sections. Do
not use these in marketing, docs, CLI help, or installer messages:

- `governs_every_agent` — "govern every agent", "governs every agent",
  "coordinates all agents", "governs all agents".
- `rulence_is_memory_backend` — "Rulence is the memory system", "Rulence is
  the memory backend", "Rulence stores all memory", "Rulence replaces
  memory", "Rulence replaces Honcho", "Rulence replaces MemPalace".
- `token_savings` — "reduces token waste", "reduces token usage", "saves
  tokens", "cuts token costs".
- `prevents_context_drift` — "prevents context drift", "stops context
  drift", "eliminates context drift".
- `modify_tool_calls` — "modifies tool calls", "rewrites tool calls".
- `cross_runtime_consistency` blocks: "guaranteed cross-agent enforcement",
  "cross-agent enforcement".

The current accurate framing is:

> "Rulence provides local policy preflight and hook-based governance for
> supported agent runtimes, starting with Claude Code."

> "Rulence routes and governs memory access across Honcho and MemPalace
> where configured."
