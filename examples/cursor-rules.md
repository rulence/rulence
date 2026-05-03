# Cursor Rule Example

Add this to a project rule so Cursor-compatible agents invoke Rulence before
tool-heavy work:

```text
Before running terminal commands, editing files, dispatching external requests,
or taking destructive actions, call the local Rulence MCP server.

Use:
- rulence_classify for task tiering.
- rulence_preflight before tool calls.
- rulence_start_thinking and rulence_think for multi-step work.
- sequentialthinking only when a Sequential Thinking-compatible schema is needed.

If Rulence returns:
- approve: proceed and keep the policy trace.
- warn: show the warning and adjust the plan before acting.
- block: stop and ask the user for explicit approval or missing context.
```
