"""Deterministic local tool-call risk classification.

This module implements the M3 fast path. Given a tool name and its
input dict, it returns a :class:`ToolRiskClassification` describing
how risky the call looks, what checks should run, and whether the
caller should bypass the (heavier) full preflight.

It is intentionally pure-Python and dependency-free: no policy files
are read, no memory providers are touched, no subprocesses are
spawned. The classification is structural — it pattern-matches the
``command``, ``file_path``, ``url``, or MCP tool name to a tight set
of regexes. Anything that does not match a known shape falls back to
``medium``, which the dispatcher routes to the full preflight pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

RiskLevel = str  # "low" | "medium" | "high" | "critical"


@dataclass(frozen=True)
class ToolRiskClassification:
    risk_level: RiskLevel
    risk_reasons: tuple[str, ...]
    recommended_checks: tuple[str, ...]
    should_run_full_preflight: bool
    requires_memory_check: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "risk_reasons": list(self.risk_reasons),
            "recommended_checks": list(self.recommended_checks),
            "should_run_full_preflight": self.should_run_full_preflight,
            "requires_memory_check": self.requires_memory_check,
        }


# ---------------------------------------------------------------------------
# Bash patterns

_BASH_CRITICAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # rm with BOTH recursive and force flags (single bunch, in either order)
    (re.compile(r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*[fF][a-zA-Z]*\b"), "rm -rf"),
    (re.compile(r"\brm\s+-[a-zA-Z]*[fF][a-zA-Z]*[rR][a-zA-Z]*\b"), "rm -fr"),
    # rm -r -f and rm -f -r (separated flag bunches)
    (re.compile(r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*\s+-[a-zA-Z]*[fF][a-zA-Z]*\b"), "rm -r -f"),
    (re.compile(r"\brm\s+-[a-zA-Z]*[fF][a-zA-Z]*\s+-[a-zA-Z]*[rR][a-zA-Z]*\b"), "rm -f -r"),
    # rm with long-form recursive + force flags
    (re.compile(r"\brm\s+--recursive\s+(?:\S+\s+)*--force\b"), "rm --recursive --force"),
    (re.compile(r"\brm\s+--force\s+(?:\S+\s+)*--recursive\b"), "rm --force --recursive"),
    # git push --force / -f
    (re.compile(r"\bgit\s+push\s+(?:[^|;&\n]*\s)?(?:--force\b|--force-with-lease\b|-f\b)"), "git push --force"),
    # disk-level operations
    (re.compile(r"\bdd\s+(?:if|of)=", re.IGNORECASE), "dd to/from disk"),
    (re.compile(r"\bmkfs(?:\.\w+)?\s"), "mkfs filesystem creation"),
    # Destructive SQL
    (re.compile(r"\b(?:drop|truncate)\s+(?:database|table|schema)\b", re.IGNORECASE), "DROP/TRUNCATE"),
    # Fork bomb (classic shape)
    (re.compile(r":\(\)\s*\{[^}]*\|[^}]*&\s*\}\s*;\s*:"), "fork bomb"),
)

_BASH_HIGH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsudo\b"), "sudo escalation"),
    (re.compile(r"\bchmod\s+(?:-R\s+)?[0-7]?7[0-7]{2}\b"), "world-writable chmod"),
    (re.compile(r"\bchmod\s+-R\b"), "recursive chmod"),
    (re.compile(r"\bchown\s+-R\b"), "recursive chown"),
    (re.compile(r"\bcurl\s+[^|]*\|\s*(?:sh|bash|zsh|ksh)\b"), "curl pipe-to-shell"),
    (re.compile(r"\bwget\s+[^|]*\|\s*(?:sh|bash|zsh|ksh)\b"), "wget pipe-to-shell"),
    (re.compile(r">\s*/dev/(?:sda|sdb|nvme|disk|hda)"), "redirect to block device"),
    (re.compile(r"\biptables\s+-F\b"), "firewall flush"),
    # Recursive rm whose flag bunch does not also contain a force flag.
    # The critical patterns above already returned for combined -rf/-fr;
    # this one only fires when those did not.
    (re.compile(r"\brm\s+(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b"), "rm -r"),
)

# A small allowlist of obviously-safe Bash invocations. Only matches when
# the command STARTS with one of these forms; anything else falls through
# to medium (which triggers the full preflight).
_BASH_SAFE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^\s*(?:ls|pwd|echo|cat|head|tail|wc|which|whoami|date|uptime|"
        r"df|du|free|ps|env|printenv|history|alias|hostname|uname|id)\b"
    ),
    re.compile(r"^\s*(?:npm|yarn|pnpm)\s+(?:test|run\s+(?:test|lint|build|typecheck|check|format))\b"),
    re.compile(r"^\s*(?:git)\s+(?:status|log|diff|branch|show|fetch|describe|remote|stash\s+list|config\s+--get|rev-parse)\b"),
    re.compile(r"^\s*(?:python3?|node|deno|bun|ruby|go|rustc|cargo)\s+--version\b"),
    re.compile(r"^\s*(?:pip|pip3)\s+(?:list|show|--version)\b"),
)


# ---------------------------------------------------------------------------
# Path patterns shared by Read/Write/Edit

_HIGH_RISK_PATH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:^|/)\.env(?:\.\w+)?(?:$|/)"), "dotenv file"),
    (re.compile(r"(?:^|/)id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?(?:$|/)"), "ssh private key"),
    (re.compile(r"\.(?:pem|key|p12|pfx)$"), "private key file"),
    (re.compile(r"(?:^|/)\.ssh(?:/|$)"), "ssh dir"),
    (re.compile(r"(?:^|/)\.aws(?:/|$)"), "aws config"),
    (re.compile(r"(?:^|/)\.gnupg(?:/|$)"), "gnupg dir"),
    (re.compile(r"^/etc/(?:shadow|passwd|sudoers)(?:$|/)"), "system auth file"),
    (re.compile(r"(?:^|/)credentials?(?:$|[/.])"), "credentials path"),
    (re.compile(r"(?:^|/)secrets?(?:$|[/.])"), "secrets path"),
)

_MEDIUM_RISK_PATH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:^|/)migrations?(?:/|$)"), "migrations directory"),
    (re.compile(r"(?:^|/)alembic/versions?(?:/|$)"), "alembic versions"),
)


# ---------------------------------------------------------------------------
# WebFetch and MCP heuristics

_WEBFETCH_LOCAL_HOST = re.compile(
    r"^https?://(?:localhost|127\.0\.0\.1|::1|0\.0\.0\.0)(?::\d+)?(?:/|$)",
    re.IGNORECASE,
)

# MCP server prefixes recognized as memory backends.
_MCP_MEMORY_SERVERS = frozenset({"memory", "honcho", "mempalace"})

# MCP servers recognized as external write surfaces.
_MCP_EXTERNAL_VCS_SERVERS = frozenset({"github", "gitlab", "bitbucket"})

# Operation keywords. Order matters: more specific verbs first so a tool
# like ``set_password`` is tagged write rather than read.
_MCP_WRITE_VERBS = (
    "create", "store", "write", "update", "upsert", "set",
    "put", "delete", "remove", "drop", "truncate", "patch",
)
_MCP_READ_VERBS = (
    "search", "list", "read", "get", "fetch", "query", "find", "describe",
)


# ---------------------------------------------------------------------------


def _classification(
    risk_level: str,
    reasons: tuple[str, ...] | list[str],
    checks: tuple[str, ...] | list[str],
    *,
    requires_memory: bool = False,
) -> ToolRiskClassification:
    return ToolRiskClassification(
        risk_level=risk_level,
        risk_reasons=tuple(reasons),
        recommended_checks=tuple(checks),
        should_run_full_preflight=risk_level != "low",
        requires_memory_check=requires_memory,
    )


def parse_mcp_tool_name(tool_name: str) -> tuple[str | None, str | None]:
    """Split ``mcp__<server>__<tool>`` into ``(server, tool)``.

    Returns ``(None, None)`` if the input does not look like an MCP tool
    name. Server and tool segments are returned in their original case;
    callers that compare to constants should lowercase first.
    """
    if not isinstance(tool_name, str) or not tool_name.startswith("mcp__"):
        return None, None
    parts = tool_name.split("__", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None, None
    return parts[1], parts[2]


def _classify_op_kind(op: str) -> str:
    """Return ``"write"``, ``"read"``, or ``"unknown"`` for an MCP op."""
    op_lower = op.lower()
    for verb in _MCP_WRITE_VERBS:
        if verb in op_lower:
            return "write"
    for verb in _MCP_READ_VERBS:
        if verb in op_lower:
            return "read"
    return "unknown"


class ToolRiskClassifier:
    """Deterministic, fast risk classifier for Claude Code tool calls."""

    def classify(
        self,
        *,
        tool_name: str | None,
        tool_input: dict[str, Any] | None = None,
        current_task: str | None = None,
        runner: str = "",
        policy_name: str | None = None,
    ) -> ToolRiskClassification:
        tool_input = tool_input or {}
        name = (tool_name or "").strip()

        if name == "Bash":
            return self._classify_bash(tool_input)
        if name == "Read":
            return self._classify_read(tool_input)
        if name in ("Write", "Edit"):
            return self._classify_write(tool_input, name)
        if name in ("Glob", "Grep"):
            return self._classify_glob_grep(tool_input)
        if name == "WebFetch":
            return self._classify_webfetch(tool_input)
        if name.startswith("mcp__"):
            return self._classify_mcp(name, tool_input, policy_name)

        # Unknown / un-named tool -> medium so the full preflight can decide.
        if not name:
            return _classification(
                "medium", ("tool:unnamed",), ("tool_preflight",)
            )
        return _classification(
            "medium", (f"tool:unknown:{name}",), ("tool_preflight",)
        )

    # ------------------------------------------------------------------
    # Per-tool handlers

    def _classify_bash(self, tool_input: dict[str, Any]) -> ToolRiskClassification:
        command = str(tool_input.get("command", ""))
        if not command.strip():
            return _classification(
                "medium", ("bash:empty_command",), ("tool_preflight",)
            )

        for pattern, label in _BASH_CRITICAL_PATTERNS:
            if pattern.search(command):
                return _classification(
                    "critical",
                    (f"bash:{label}",),
                    ("memory_check", "tool_preflight", "constraint_solver", "approval_gate"),
                    requires_memory=True,
                )
        for pattern, label in _BASH_HIGH_PATTERNS:
            if pattern.search(command):
                return _classification(
                    "high",
                    (f"bash:{label}",),
                    ("memory_check", "tool_preflight", "constraint_solver"),
                    requires_memory=True,
                )

        for pattern in _BASH_SAFE_PATTERNS:
            if pattern.search(command):
                return _classification("low", (), ())

        # Anything else: route through full preflight.
        return _classification(
            "medium", ("bash:unrecognized",), ("tool_preflight",)
        )

    def _classify_read(self, tool_input: dict[str, Any]) -> ToolRiskClassification:
        path = str(tool_input.get("file_path") or tool_input.get("path") or "")
        for pattern, label in _HIGH_RISK_PATH_PATTERNS:
            if pattern.search(path):
                return _classification(
                    "high",
                    (f"read:{label}",),
                    ("memory_check", "tool_preflight", "constraint_solver"),
                    requires_memory=True,
                )
        return _classification("low", (), ())

    def _classify_write(
        self, tool_input: dict[str, Any], tool_name: str
    ) -> ToolRiskClassification:
        path = str(tool_input.get("file_path") or tool_input.get("path") or "")
        op = tool_name.lower()
        for pattern, label in _HIGH_RISK_PATH_PATTERNS:
            if pattern.search(path):
                return _classification(
                    "high",
                    (f"{op}:{label}",),
                    ("memory_check", "tool_preflight", "constraint_solver"),
                    requires_memory=True,
                )
        for pattern, label in _MEDIUM_RISK_PATH_PATTERNS:
            if pattern.search(path):
                return _classification(
                    "medium",
                    (f"{op}:{label}",),
                    ("tool_preflight",),
                )
        # Plain source-file edit: low.
        return _classification("low", (), ())

    def _classify_glob_grep(self, tool_input: dict[str, Any]) -> ToolRiskClassification:
        # Read-only file-system queries; cheap by definition.
        return _classification("low", (), ())

    def _classify_webfetch(self, tool_input: dict[str, Any]) -> ToolRiskClassification:
        url = str(tool_input.get("url", ""))
        if _WEBFETCH_LOCAL_HOST.search(url):
            return _classification("low", (), ())
        return _classification(
            "medium", ("webfetch:external_url",), ("tool_preflight",)
        )

    def _classify_mcp(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        policy_name: str | None,
    ) -> ToolRiskClassification:
        server, op = parse_mcp_tool_name(tool_name)
        if server is None or op is None:
            return _classification(
                "medium",
                (f"mcp:malformed_name:{tool_name}",),
                ("tool_preflight",),
            )
        server_lower = server.lower()
        op_kind = _classify_op_kind(op)

        if server_lower in _MCP_MEMORY_SERVERS:
            if op_kind == "write":
                return _classification(
                    "high",
                    (f"mcp:memory_write:{server_lower}.{op}",),
                    ("memory_check", "tool_preflight", "constraint_solver"),
                    requires_memory=True,
                )
            if op_kind == "read":
                return _classification(
                    "medium",
                    (f"mcp:memory_read:{server_lower}.{op}",),
                    ("tool_preflight",),
                )
            return _classification(
                "medium",
                (f"mcp:memory:{server_lower}.{op}",),
                ("tool_preflight",),
            )

        if server_lower in _MCP_EXTERNAL_VCS_SERVERS:
            if op_kind == "write":
                return _classification(
                    "medium",
                    (f"mcp:external_write:{server_lower}.{op}",),
                    ("tool_preflight",),
                )
            if op_kind == "read":
                return _classification("low", (), ())
            return _classification(
                "medium",
                (f"mcp:external:{server_lower}.{op}",),
                ("tool_preflight",),
            )

        # Unknown MCP server: fall back to medium.
        return _classification(
            "medium",
            (f"mcp:unknown_server:{server_lower}.{op}",),
            ("tool_preflight",),
        )
