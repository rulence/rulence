from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

# Claude Code settings hooks have changed over time; keep this isolated so the
# installer can be updated without touching policy/runtime behavior.
CLAUDE_HOOK = {
    "matcher": "Bash|Edit|Write",
    "hooks": [
        {
            "type": "command",
            "command": "rulence hook claude-code-pretooluse",
        }
    ],
}

CURSOR_RULE = """# Rulence Governance Rule

Before executing any tool call that modifies code, deletes files, runs commands,
or accesses secrets, call:

  rulence preflight "<task description>" --json

If the verdict is "block", stop and surface the blocks to the user.
If the verdict is "warn", surface warnings before proceeding.
"""

N8N_CONFIG = {
    "mcpServers": {
        "rulence": {
            "command": "rulence",
            "args": ["mcp"],
        }
    }
}


def install_claude_code(force: bool = False) -> dict[str, Any]:
    settings = Path.home() / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    hooks = data.setdefault("hooks", {})
    pre_tool_use = hooks.setdefault("PreToolUse", [])

    if any("rulence" in json.dumps(hook) for hook in pre_tool_use) and not force:
        return {"status": "already_installed", "path": str(settings)}

    backup = None
    if settings.exists():
        backup = settings.with_suffix(f".json.rulence-{datetime.now():%Y%m%d-%H%M%S}.bak")
        shutil.copy2(settings, backup)

    pre_tool_use.append(CLAUDE_HOOK)
    settings.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "status": "installed",
        "path": str(settings),
        "backup": str(backup) if backup else None,
    }


def install_cursor(target_dir: str | None = None) -> dict[str, Any]:
    base = Path(target_dir or ".").expanduser().resolve()
    rules = base / ".cursor" / "rules" / "rulence.md"
    rules.parent.mkdir(parents=True, exist_ok=True)
    rules.write_text(CURSOR_RULE, encoding="utf-8")
    return {"status": "installed", "path": str(rules)}


def install_n8n() -> dict[str, Any]:
    return {
        "status": "print_only",
        "config": N8N_CONFIG,
        "instructions": "Paste this into n8n MCP server settings.",
    }
