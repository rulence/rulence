# Launch Post Draft

Sequential Thinking is the protocol your agent uses to think.

Rulence provides local policy preflight and hook-based governance for
supported agent runtimes, starting with Claude Code.

Same task: `delete production credentials`.

- Claude Code: blocked at the PreToolUse hook.
- Cursor: advisory rule says block; the model is expected to honor it.
- n8n: advisory MCP integration; whether the workflow consults Rulence is
  up to the workflow author.

Same local policy. Same verdict. Auditable local trace.

Rulence is local-first preflight and policy reuse for AI agent runners:

- installable starter policies
- MCP-compatible
- works across supported runners (with enforcement varying by runtime)
- local traces
- no lock-in

Value in Consistency.
