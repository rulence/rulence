from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

# Unified Claude Code hook command. The handler dispatches by
# ``hook_event_name`` so a single binary serves every event we install.
RULENCE_HOOK_COMMAND = "rulence hook claude-code"

# Legacy command kept as a recognized fingerprint so the installer can
# detect older Rulence hook entries and avoid duplicate installs. The CLI
# still answers ``rulence hook claude-code-pretooluse`` for backwards
# compatibility with settings.json files written before M1.
LEGACY_PRETOOLUSE_COMMAND = "rulence hook claude-code-pretooluse"

# Post-M4: PreToolUse uses a single universal matcher. The fast-path tool
# risk classifier in src/rulence/tool_risk.py keeps low-risk hook latency
# bounded, so universal coverage is now safe.
UNIVERSAL_PRETOOLUSE_MATCHER = "*"

# Bounded matchers for PostToolUse. PostToolUse remains bounded in M4
# because PostToolUse processing has no fast-path classifier yet and a
# universal "*" matcher there has not been latency-tested.
BOUNDED_TOOL_MATCHERS: tuple[str, ...] = (
    "Bash|Edit|Write",
    "Read|Glob|Grep",
    "WebFetch",
    "mcp__.*",
)

# Events that carry a ``matcher`` regex against ``tool_name``.
MATCHED_EVENTS: tuple[str, ...] = ("PreToolUse", "PostToolUse")

# Lifecycle events that are not gated by tool name.
UNMATCHED_EVENTS: tuple[str, ...] = (
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "SessionEnd",
)


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


def _hook_with_matcher(matcher: str, command: str = RULENCE_HOOK_COMMAND) -> dict[str, Any]:
    return {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": command}],
    }


def _hook_no_matcher(command: str = RULENCE_HOOK_COMMAND) -> dict[str, Any]:
    return {
        "hooks": [{"type": "command", "command": command}],
    }


def _is_rulence_entry(entry: Any) -> bool:
    """Return True for any hook entry whose payload references Rulence."""
    if not isinstance(entry, dict):
        return False
    serialized = json.dumps(entry)
    return (
        RULENCE_HOOK_COMMAND in serialized
        or LEGACY_PRETOOLUSE_COMMAND in serialized
    )


def _entry_matcher(entry: Any) -> str | None:
    if isinstance(entry, dict):
        matcher = entry.get("matcher")
        if isinstance(matcher, str):
            return matcher
    return None


def _install_event_with_matchers(
    entries: list[Any],
    event_name: str,
    matchers: tuple[str, ...],
    *,
    force: bool,
    summary: dict[str, list[str]],
) -> None:
    for matcher in matchers:
        existing_indices = [
            i
            for i, entry in enumerate(entries)
            if _is_rulence_entry(entry) and _entry_matcher(entry) == matcher
        ]
        if existing_indices and not force:
            summary["events_skipped"].append(f"{event_name}:{matcher}")
            continue
        if existing_indices and force:
            for idx in sorted(existing_indices, reverse=True):
                entries.pop(idx)
            entries.append(_hook_with_matcher(matcher))
            summary["events_replaced"].append(f"{event_name}:{matcher}")
            continue
        entries.append(_hook_with_matcher(matcher))
        summary["events_installed"].append(f"{event_name}:{matcher}")


def _install_universal_pretooluse(
    entries: list[Any],
    *,
    force: bool,
    summary: dict[str, list[str]],
) -> None:
    """Install a single PreToolUse hook with matcher '*'.

    Migration semantics:

    * Fresh install (no existing Rulence PreToolUse entry): append the
      universal entry.
    * Existing Rulence entries (e.g. M1 bounded matchers like
      ``Bash|Edit|Write``) without ``--force``: leave them alone and
      skip — the user's existing bounded coverage keeps working.
    * Existing Rulence entries with ``--force``: remove every Rulence
      entry and install one universal entry, leaving unrelated user
      hooks intact.
    """
    rulence_indices = [
        i for i, entry in enumerate(entries) if _is_rulence_entry(entry)
    ]
    has_universal = any(
        _entry_matcher(entries[i]) == UNIVERSAL_PRETOOLUSE_MATCHER
        for i in rulence_indices
    )

    if rulence_indices and not force:
        if has_universal and len(rulence_indices) == 1:
            summary["events_skipped"].append(
                f"PreToolUse:{UNIVERSAL_PRETOOLUSE_MATCHER}"
            )
        else:
            summary["events_skipped"].append("PreToolUse:legacy_bounded")
        return

    if rulence_indices and force:
        for idx in sorted(rulence_indices, reverse=True):
            entries.pop(idx)
        entries.append(_hook_with_matcher(UNIVERSAL_PRETOOLUSE_MATCHER))
        summary["events_replaced"].append(
            f"PreToolUse:{UNIVERSAL_PRETOOLUSE_MATCHER}"
        )
        return

    entries.append(_hook_with_matcher(UNIVERSAL_PRETOOLUSE_MATCHER))
    summary["events_installed"].append(
        f"PreToolUse:{UNIVERSAL_PRETOOLUSE_MATCHER}"
    )


def _install_event_without_matcher(
    entries: list[Any],
    event_name: str,
    *,
    force: bool,
    summary: dict[str, list[str]],
) -> None:
    existing_indices = [
        i for i, entry in enumerate(entries) if _is_rulence_entry(entry)
    ]
    if existing_indices and not force:
        summary["events_skipped"].append(event_name)
        return
    if existing_indices and force:
        for idx in sorted(existing_indices, reverse=True):
            entries.pop(idx)
        entries.append(_hook_no_matcher())
        summary["events_replaced"].append(event_name)
        return
    entries.append(_hook_no_matcher())
    summary["events_installed"].append(event_name)


def install_claude_code(force: bool = False) -> dict[str, Any]:
    settings = Path.home() / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{settings} is not valid JSON ({exc.msg} at line {exc.lineno}). "
                "Move it aside or fix it, then re-run."
            ) from exc
    else:
        data = {}

    backup: Path | None = None
    if settings.exists():
        backup = settings.with_suffix(
            f".json.rulence-{datetime.now():%Y%m%d-%H%M%S}.bak"
        )
        shutil.copy2(settings, backup)

    hooks_config = data.setdefault("hooks", {})
    summary: dict[str, list[str]] = {
        "events_installed": [],
        "events_skipped": [],
        "events_replaced": [],
    }

    for event_name in MATCHED_EVENTS:
        entries = hooks_config.setdefault(event_name, [])
        if event_name == "PreToolUse":
            _install_universal_pretooluse(entries, force=force, summary=summary)
        else:
            # PostToolUse keeps bounded matchers in M4 because PostToolUse
            # has no fast-path classifier yet and "*" there has not been
            # latency-tested.
            _install_event_with_matchers(
                entries,
                event_name,
                BOUNDED_TOOL_MATCHERS,
                force=force,
                summary=summary,
            )

    for event_name in UNMATCHED_EVENTS:
        entries = hooks_config.setdefault(event_name, [])
        _install_event_without_matcher(
            entries, event_name, force=force, summary=summary
        )

    changes_made = bool(summary["events_installed"] or summary["events_replaced"])
    if not changes_made and backup is not None:
        # Nothing was added; keep the user's settings (and the backup) intact
        # but still report the no-op.
        return {
            "status": "already_installed",
            "path": str(settings),
            "backup": str(backup),
            "events_installed": summary["events_installed"],
            "events_skipped": summary["events_skipped"],
            "events_replaced": summary["events_replaced"],
        }

    settings.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    status = "installed" if changes_made else "already_installed"
    return {
        "status": status,
        "path": str(settings),
        "backup": str(backup) if backup else None,
        "events_installed": summary["events_installed"],
        "events_skipped": summary["events_skipped"],
        "events_replaced": summary["events_replaced"],
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
