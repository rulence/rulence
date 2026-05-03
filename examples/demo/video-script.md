# 90-Second Demo Script

0:00-0:10
Title: Rulence - Local Policy Preflight for AI Agents.

0:10-0:25
Terminal:

```bash
pip install rulence
rulence policy install secrets aws migrations
```

Caption: One local policy layer.

0:25-0:45
Claude Code: ask `delete production credentials`.
Caption: Claude Code: BLOCKED at the PreToolUse hook.

0:45-1:00
Cursor: same task.
Caption: Cursor (advisory rule): says BLOCK. Model is expected to honor it.

1:00-1:15
n8n: same task via MCP.
Caption: n8n (advisory): MCP returns BLOCK; workflow decides whether to act.

1:15-1:25
Open `trace.html`.
Caption: Auditable local trace. Local file. Yours.

1:25-1:30
End card: rulence.dev - Local-first. No lock-in.
