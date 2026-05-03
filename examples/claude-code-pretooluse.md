# Claude Code PreToolUse Example

Use the installed hook command directly. Claude Code sends PreToolUse JSON on
stdin; Rulence returns Claude's structured permission decision JSON on stdout.

```bash
rulence install claude-code
```

The installed command is `rulence hook claude-code-pretooluse`. It denies blocked
tool calls and asks before warned tool calls.
