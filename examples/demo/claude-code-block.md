# Claude Code Block Demo

Task:

```text
delete production credentials
```

Setup:

```bash
rulence policy install secrets aws
rulence install claude-code
```

Expected result:

```text
verdict: block
tier: Tier 5 / high-risk
blocks:
  - high-risk tier requires explicit user approval
  - sensitive_or_destructive_action
```

For the video, show `~/.claude/settings.json`, then ask Claude Code to perform
the task. The PreToolUse hook should surface the Rulence block before action.
