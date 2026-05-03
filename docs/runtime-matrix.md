# Rulence Runtime Matrix

This matrix records what Rulence can and cannot see or enforce in each
supported runtime today. Use the right-most column ("Claim-safe wording")
when describing the integration in user-facing copy.

`y` means supported today. `n` means not supported. `advisory` means
Rulence emits a verdict or instruction, but enforcement depends on the
host runner or the model honoring it. `partial` means a narrow form is
implemented; see notes.

| Runtime | User prompt visibility | Tool call visibility | Tool result visibility | Final response visibility | Can block tool calls | Can modify tool calls | Can enforce memory policy | Can enforce token policy | Current status | Claim-safe wording |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Code | partial (transcript when supplied to hook) | y (universal PreToolUse hook payload, every tool) | partial (PostToolUse: observe + audit only; cannot replace) | partial (Stop-hook final-response review; can request one revision when supported) | y (deny/ask via PreToolUse) | n | n | n | supported PreToolUse + observed PostToolUse + Stop-hook review | "Rulence runs as a Claude Code PreToolUse hook with a universal matcher and can block or ask before any tool call. PostToolUse is observed and redacted for audit only. Stop-hook review is deterministic and may request one continuation when fail is detected; it does not guarantee correct answers." |
| Cursor | n (only when host calls preflight) | n | n | n | advisory | n | n | n | advisory rule only | "Rulence ships an advisory Cursor project rule; enforcement depends on the model." |
| n8n | n (only when workflow calls the MCP tool) | n | n | n | advisory | n | n | n | advisory MCP integration | "Rulence ships an advisory n8n MCP integration; whether the workflow consults Rulence is up to the workflow author." |
| MCP-only clients | n (only what the client passes in) | n | n | n | advisory | n | n | n | advisory unless host enforces | "Rulence exposes an MCP stdio server; enforcement requires the host to consult Rulence before each tool call." |
| Honcho integration | n/a | n/a | n/a | n/a | n/a | n/a | partial (routed reads, write denied at adapter level today) | n | partial: internal-role read-merge + arbitration | "Rulence routes Honcho memory reads through the arbiter as the canonical *internal* backend; writes are refused until the Honcho adapter exposes a write method." |
| MemPalace integration | n/a | n/a | n/a | n/a | n/a | n/a | partial (routed reads, write denied at adapter level today) | n | partial: external-role read-merge + arbitration | "Rulence routes MemPalace memory reads through the arbiter as the canonical *external* backend; writes are refused until the MempalaceMcpProvider exposes a write method." |
| Custom Python agents | depends on caller | depends on caller | depends on caller | depends on caller | depends on caller | n | n | n | depends on caller wiring | "Custom Python agents can call `preflight_task()` and act on the verdict; Rulence does not intercept on its own." |
| Custom Node agents | depends on caller | depends on caller | depends on caller | depends on caller | depends on caller | n | n | n | depends on caller wiring | "Custom Node agents can call the Rulence MCP server; Rulence does not intercept on its own." |
| Future proxy mode | aspirational | aspirational | aspirational | aspirational | aspirational | aspirational | aspirational | aspirational | roadmap | "Roadmap: a generic interception layer that could observe tool calls across runtimes. Not implemented today." |

## Notes

- "User prompt visibility" reflects whether Rulence sees the original user
  task text. With Claude Code's PreToolUse hook, Rulence sees the tool
  call payload and (when present) the transcript path; the original user
  prompt is reachable indirectly through the transcript.
- "Tool result visibility" is `n` everywhere today: Rulence runs at
  PreToolUse, not PostToolUse.
- "Can modify tool calls" is `n` everywhere: even Claude Code's hook can
  only deny or ask, not rewrite arguments.
- Memory policy enforcement is `partial` only for the read-merge and
  provider-priority feature in `retrieve_combined`. There is no
  policy-enforced read or write arbitration yet, no redaction, and no
  audit of memory operations.
- Token policy enforcement is `n` everywhere: there are no measured
  benchmarks, and Rulence does not throttle, cache, or rewrite based on
  token budget at runtime.
- Secret redaction is applied at the storage boundary for audit
  events, session traces, trace HTML, and feedback records. Coverage
  is best-effort and limited to the detector list in
  ``rulence.security.redactor``; custom or low-entropy secrets may
  still pass through.
- A deterministic ``ToolRiskClassifier`` runs before the full
  preflight on Claude Code PreToolUse events. Low-risk classifications
  audit and allow without loading policy files or running checks;
  medium/high/critical fall through to the existing preflight, with
  ``critical`` always denying and ``high`` always at-least-asking.
- Claude Code's PreToolUse matcher is now ``"*"`` for fresh installs.
  Existing M1/M3 bounded installations are preserved unless
  ``rulence install claude-code --force`` is used to migrate. The
  universal matcher applies to Claude Code only — Cursor, n8n, and
  other runners remain advisory.
- PostToolUse keeps bounded matchers (``Bash|Edit|Write``,
  ``Read|Glob|Grep``, ``WebFetch``, ``mcp__.*``) because PostToolUse
  has no fast-path classifier yet and a universal matcher there has
  not been latency-tested.
- On PostToolUse, ``rulence.review.ToolResultReviewer`` classifies the
  tool result as ``safe`` / ``warn`` / ``unsafe``, runs the secret
  redactor, deterministically compacts large outputs, and writes
  redaction count, redaction types, original/filtered token estimates,
  and a ``safe_for_model_context`` advisory into the audit record.
  Rulence cannot replace or block the tool result the model has
  already received.
- On Stop, ``rulence.review.FinalResponseReviewer`` reads audit
  records for the current ``corr_id`` and applies deterministic
  checks: secret leakage in the assistant's last turn,
  ``forbids/requires`` constraints parsed from the user prompt,
  action claims without a corresponding tool trace
  (``ran tests``, ``deployed``, ``edited file``, ``searched memory``),
  and unresolved ``ask`` decisions. On ``fail`` and only when
  ``stop_hook_active`` is False, the dispatcher returns
  ``decision: block`` once to request a revision; subsequent
  re-stops audit only. Rulence does not guarantee correct answers
  and does not prevent all hallucinations.

## How to use this matrix

When you are writing public copy, find the runtime row first. If your
intended claim is `n` for that capability, reword it. If it is `advisory`,
say so explicitly. If it is `partial`, link to the ledger entry for the
specific caveats. The verifier (`rulence claims verify`) does not parse
this matrix — it parses `docs/claims.yml` — but the wording in this matrix
should always be safe to lift directly into copy.
