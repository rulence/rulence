from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from ._jsonrpc import read_message, write_message
from .classifier import classify_task
from .decomposer import decompose_prompt
from .transcript import MAX_TRANSCRIPT_BYTES
from .memory import memory_health, mempalace_rooms, mempalace_wings
from .preflight import preflight_task
from .reasoning import add_thought, start_session, summarize_session
from .sessions import SessionStore


SERVER_INFO = {"name": "rulence", "version": "0.3.0"}
SEQUENTIAL_SESSION_ID = "__sequentialthinking__"


class InvalidParamsError(ValueError):
    """Raised when an MCP tool call has missing or invalid parameters."""


def main() -> int:
    while True:
        try:
            message, framing = read_message(sys.stdin.buffer)
        except ValueError as exc:
            write_message(sys.stdout.buffer, _error(None, -32700, str(exc)), "headers")
            continue
        if message is None:
            return 0
        response = handle_request(message)
        if response is not None:
            write_message(sys.stdout.buffer, response, framing)


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")

    try:
        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                },
            )

        if method == "notifications/initialized":
            return None

        if method == "tools/list":
            return _result(request_id, {"tools": _tools()})

        if method == "tools/call":
            params = message.get("params", {})
            return _result(request_id, _call_tool(params.get("name"), params.get("arguments", {})))

        return _error(request_id, -32601, f"method not found: {method}")
    except InvalidParamsError as exc:
        return _error(request_id, -32602, str(exc))
    except Exception as exc:  # pragma: no cover - defensive server boundary
        return _error(request_id, -32603, str(exc))


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    store = SessionStore()
    if name == "rulence_classify":
        task = _required_text(arguments, "task")
        payload = classify_task(task).to_dict()
    elif name == "rulence_decompose":
        prompt = _required_text(arguments, "prompt")
        payload = decompose_prompt(
            prompt,
            max_depth=max(1, min(_optional_int(arguments, "max_depth") or 3, 5)),
        ).to_dict()
    elif name == "rulence_preflight":
        task = _required_text(arguments, "task")
        payload = preflight_task(
            task,
            memory=str(arguments.get("memory", "")),
            policy_dir=arguments.get("policy_dir"),
            transcript=_optional_transcript(arguments),
        ).to_dict()
    elif name == "rulence_start_thinking":
        task = _required_text(arguments, "task")
        session = start_session(
            task,
            memory=str(arguments.get("memory", "")),
            policy_dir=arguments.get("policy_dir"),
            session_id=arguments.get("session_id"),
            transcript=_optional_transcript(arguments),
        )
        store.save(session)
        payload = session.to_dict()
    elif name == "rulence_think":
        session_id = _required_text(arguments, "session_id")
        if not store.exists(session_id):
            raise InvalidParamsError(f"unknown session_id: {session_id}")
        session = add_thought(
            store.load(session_id),
            _required_text(arguments, "thought"),
            thought_number=_optional_int(arguments, "thought_number"),
            total_thoughts=_optional_int(arguments, "total_thoughts"),
            next_thought_needed=bool(arguments.get("next_thought_needed", False)),
            is_revision=bool(arguments.get("is_revision", False)),
            revises_thought=_optional_int(arguments, "revises_thought"),
            branch_from_thought=_optional_int(arguments, "branch_from_thought"),
            branch_id=arguments.get("branch_id"),
            needs_more_thoughts=bool(arguments.get("needs_more_thoughts", False)),
        )
        store.save(session)
        payload = session.to_dict()
    elif name == "rulence_trace":
        session_id = _required_text(arguments, "session_id")
        if not store.exists(session_id):
            raise InvalidParamsError(f"unknown session_id: {session_id}")
        session = store.load(session_id)
        payload = {
            "summary": summarize_session(session),
            "session": session.to_dict(),
        }
    elif name == "rulence_memory_health":
        payload = memory_health(str(arguments.get("provider", "all")))
    elif name == "rulence_memory_wings":
        payload = {"wings": mempalace_wings()}
    elif name == "rulence_memory_rooms":
        payload = {"rooms": mempalace_rooms(_required_text(arguments, "wing"))}
    elif name == "rulence_context_snapshot":
        payload = _context_snapshot_tool(arguments)
    elif name == "rulence_context_handoff":
        payload = _context_handoff_tool(arguments)
    elif name == "rulence_context_assemble":
        payload = _context_assemble_tool(arguments)
    elif name == "sequentialthinking":
        return _sequentialthinking(arguments)
    else:
        raise InvalidParamsError(f"unknown tool: {name}")

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, sort_keys=True),
            }
        ],
        "structuredContent": payload,
    }


def _tools() -> list[dict[str, Any]]:
    task_schema = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Task the agent is about to perform."},
        },
        "required": ["task"],
    }
    return [
        {
            "name": "sequentialthinking",
            "description": "Drop-in Sequential Thinking compatible tool with Rulence governance metadata.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "Your current thinking step."},
                    "nextThoughtNeeded": {"type": "boolean", "description": "Whether another thought step is needed."},
                    "thoughtNumber": {"type": "integer", "minimum": 1, "description": "Current thought number."},
                    "totalThoughts": {"type": "integer", "minimum": 1, "description": "Estimated total thoughts needed."},
                    "isRevision": {"type": "boolean", "description": "Whether this revises previous thinking."},
                    "revisesThought": {"type": "integer", "minimum": 1, "description": "Which thought is being reconsidered."},
                    "branchFromThought": {"type": "integer", "minimum": 1, "description": "Branching point thought number."},
                    "branchId": {"type": "string", "description": "Branch identifier."},
                    "needsMoreThoughts": {"type": "boolean", "description": "If more thoughts are needed."},
                },
                "required": ["thought", "nextThoughtNeeded", "thoughtNumber", "totalThoughts"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "thoughtNumber": {"type": "integer"},
                    "totalThoughts": {"type": "integer"},
                    "nextThoughtNeeded": {"type": "boolean"},
                    "branches": {"type": "array", "items": {"type": "string"}},
                    "thoughtHistoryLength": {"type": "integer"},
                    "rulence": {"type": "object"},
                },
                "required": ["thoughtNumber", "totalThoughts", "nextThoughtNeeded", "branches", "thoughtHistoryLength"],
            },
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        },
        {
            "name": "rulence_classify",
            "description": "Classify an agent task into a Rulence difficulty/risk tier.",
            "inputSchema": task_schema,
        },
        {
            "name": "rulence_decompose",
            "description": "Logically break a large prompt into a typed task DAG with constraints and dependencies.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Prompt or task spec to decompose."},
                    "max_depth": {"type": "integer", "default": 3, "minimum": 1, "maximum": 5},
                },
                "required": ["prompt"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "root_units": {"type": "array"},
                    "flat": {"type": "array"},
                    "all_constraints": {"type": "array"},
                    "word_count_original": {"type": "integer"},
                    "word_count_decomposed": {"type": "integer"},
                },
            },
        },
        {
            "name": "rulence_preflight",
            "description": "Run local Rulence policy checks before an agent proceeds.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task the agent is about to perform."},
                    "memory": {"type": "string", "description": "Optional local memory/context."},
                    "policy_dir": {"type": "string", "description": "Optional local policy directory."},
                    "transcript": {"type": "string", "description": "Optional conversation transcript (JSONL or plain text) for transcript-aware checks."},
                    "transcript_path": {"type": "string", "description": "Optional path to a transcript file. Read server-side; ignored if transcript is also supplied."},
                },
                "required": ["task"],
            },
        },
        {
            "name": "rulence_start_thinking",
            "description": "Start a governed sequential reasoning session for a task.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task the agent is about to reason through."},
                    "memory": {"type": "string", "description": "Optional local memory/context."},
                    "policy_dir": {"type": "string", "description": "Optional local policy directory."},
                    "session_id": {"type": "string", "description": "Optional caller-provided session id."},
                    "transcript": {"type": "string", "description": "Optional conversation transcript (JSONL or plain text) for transcript-aware checks."},
                    "transcript_path": {"type": "string", "description": "Optional path to a transcript file. Read server-side; ignored if transcript is also supplied."},
                },
                "required": ["task"],
            },
        },
        {
            "name": "rulence_think",
            "description": "Append, revise, or branch a thought in a governed Rulence reasoning session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Rulence reasoning session id."},
                    "thought": {"type": "string", "description": "Current reasoning step."},
                    "thought_number": {"type": "integer", "description": "Current thought number."},
                    "total_thoughts": {"type": "integer", "description": "Estimated total thoughts."},
                    "next_thought_needed": {"type": "boolean", "description": "Whether another thought is needed."},
                    "is_revision": {"type": "boolean", "description": "Whether this revises a prior thought."},
                    "revises_thought": {"type": "integer", "description": "Thought number being revised."},
                    "branch_from_thought": {"type": "integer", "description": "Thought number to branch from."},
                    "branch_id": {"type": "string", "description": "Branch identifier."},
                    "needs_more_thoughts": {"type": "boolean", "description": "Whether the estimate was too low."},
                },
                "required": ["session_id", "thought"],
            },
        },
        {
            "name": "rulence_trace",
            "description": "Return a Rulence reasoning session trace and summary.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Rulence reasoning session id."}
                },
                "required": ["session_id"],
            },
        },
        {
            "name": "rulence_memory_health",
            "description": "Check Rulence memory provider reachability.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string", "enum": ["mempalace", "honcho", "local", "all"]},
                },
            },
        },
        {
            "name": "rulence_memory_wings",
            "description": "List MemPalace wings through the configured MCP server.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "rulence_memory_rooms",
            "description": "List rooms in a MemPalace wing through the configured MCP server.",
            "inputSchema": {
                "type": "object",
                "properties": {"wing": {"type": "string"}},
                "required": ["wing"],
            },
        },
        {
            "name": "rulence_context_snapshot",
            "description": (
                "Assemble and persist a governed ContextSnapshot for a task. "
                "Reuses existing TaskState and ContextStore. Does not replace "
                "Honcho or MemPalace."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "max_tokens": {"type": "integer", "minimum": 1, "default": 4000},
                    "compress": {"type": "boolean", "default": True},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "rulence_context_handoff",
            "description": (
                "Record a snapshot-based handoff from one supported agent to "
                "another. Honcho and MemPalace remain the canonical memory "
                "backends; this records the local governance event only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "from_agent": {"type": "string"},
                    "to_agent": {"type": "string"},
                    "snapshot_id": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["task_id", "from_agent", "to_agent"],
            },
        },
        {
            "name": "rulence_context_assemble",
            "description": (
                "Assemble (and optionally compress) the governed context for a "
                "task, returning the snapshot, budget, and compressed text."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "runner": {"type": "string"},
                    "max_tokens": {"type": "integer", "minimum": 1, "default": 4000},
                    "compress": {"type": "boolean", "default": True},
                },
                "required": ["task_id"],
            },
        },
    ]


def _context_bus():
    from .context import AgentContextBus, ContextStore

    return AgentContextBus(store=ContextStore())


def _context_snapshot_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    from .context import TaskNotFoundError

    task_id = _required_text(arguments, "task_id")
    max_tokens = _optional_int(arguments, "max_tokens") or 4000
    compress = _coerce_bool(arguments.get("compress", True))
    bus = _context_bus()
    try:
        snapshot = bus.create_snapshot(
            task_id, max_tokens=max_tokens, compress=compress
        )
    except TaskNotFoundError as exc:
        raise InvalidParamsError(str(exc)) from exc
    return snapshot.to_dict()


def _context_handoff_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    from .context import TaskNotFoundError

    task_id = _required_text(arguments, "task_id")
    from_agent = _required_text(arguments, "from_agent")
    to_agent = _required_text(arguments, "to_agent")
    snapshot_id = arguments.get("snapshot_id") or None
    note = arguments.get("note") or None
    bus = _context_bus()
    try:
        return bus.handoff(
            from_agent=from_agent,
            to_agent=to_agent,
            task_id=task_id,
            snapshot_id=snapshot_id,
            note=note,
        )
    except TaskNotFoundError as exc:
        raise InvalidParamsError(str(exc)) from exc
    except ValueError as exc:
        raise InvalidParamsError(str(exc)) from exc


def _context_assemble_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    from .context import TaskNotFoundError

    task_id = _required_text(arguments, "task_id")
    max_tokens = _optional_int(arguments, "max_tokens") or 4000
    compress = _coerce_bool(arguments.get("compress", True))
    runner = arguments.get("runner") or None
    bus = _context_bus()
    try:
        state = bus.load_state(task_id)
    except TaskNotFoundError as exc:
        raise InvalidParamsError(str(exc)) from exc
    result = bus.assembler.assemble(
        state.user_goal,
        corr_id=task_id,
        max_tokens=max_tokens,
        policy_tier=int(state.metadata.get("policy_tier", 1) or 1),
        current_constraints=state.active_constraints,
        compress=compress,
        task_id=task_id,
    )
    bus.publish(
        task_id,
        event_type="context_assembled",
        agent=runner,
        data={
            "snapshot_id": result.snapshot.snapshot_id,
            "fragment_count": len(result.snapshot.fragment_ids),
            "runner": runner,
        },
    )
    return result.to_dict()


def _sequentialthinking(arguments: dict[str, Any]) -> dict[str, Any]:
    thought = _required_text(arguments, "thought")
    thought_number = _optional_int(arguments, "thoughtNumber") or 1
    total_thoughts = max(_optional_int(arguments, "totalThoughts") or 1, thought_number)
    next_thought_needed = _coerce_bool(arguments.get("nextThoughtNeeded", False))

    store = SessionStore()
    session = store.load(SEQUENTIAL_SESSION_ID) if store.exists(SEQUENTIAL_SESSION_ID) else start_session(
        thought,
        policy_dir=os.environ.get("RULENCE_POLICY_DIR"),
        session_id=SEQUENTIAL_SESSION_ID,
    )

    session = add_thought(
        session,
        thought,
        thought_number=thought_number,
        total_thoughts=total_thoughts,
        next_thought_needed=next_thought_needed,
        is_revision=_coerce_bool(arguments.get("isRevision", False)),
        revises_thought=_optional_int(arguments, "revisesThought"),
        branch_from_thought=_optional_int(arguments, "branchFromThought"),
        branch_id=arguments.get("branchId"),
        needs_more_thoughts=_coerce_bool(arguments.get("needsMoreThoughts", False)),
    )
    store.save(session)

    step = session.steps[-1]
    branches = sorted({existing.branch_id for existing in session.steps if existing.branch_id})
    payload = {
        "thoughtNumber": step.thought_number,
        "totalThoughts": step.total_thoughts,
        "nextThoughtNeeded": step.next_thought_needed,
        "branches": branches,
        "thoughtHistoryLength": len(session.steps),
        "rulence": {
            "sessionId": session.session_id,
            "tier": session.preflight.classification.label,
            "preflightVerdict": session.preflight.verdict,
            "status": session.status,
            "warnings": list(session.warnings),
            "stepChecks": [check.to_dict() for check in step.checks],
            "actionBlocked": session.preflight.verdict == "block",
            "decompositionAvailable": session.preflight.decomposition is not None,
            "decomposition": session.preflight.decomposition.to_dict() if session.preflight.decomposition else None,
        },
    }
    _log_thought(step.thought_number, step.total_thoughts, thought, step.is_revision, step.revises_thought, step.branch_from_thought, step.branch_id)
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}], "structuredContent": payload}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _log_thought(
    thought_number: int,
    total_thoughts: int,
    thought: str,
    is_revision: bool,
    revises_thought: int | None,
    branch_from_thought: int | None,
    branch_id: str | None,
) -> None:
    if os.environ.get("DISABLE_THOUGHT_LOGGING", "").lower() == "true":
        return
    if is_revision:
        label = f"Revision {thought_number}/{total_thoughts} revising {revises_thought}"
    elif branch_from_thought:
        label = f"Branch {thought_number}/{total_thoughts} from {branch_from_thought} id={branch_id}"
    else:
        label = f"Thought {thought_number}/{total_thoughts}"
    print(f"[rulence:{label}] {thought}", file=sys.stderr)


def _optional_transcript(arguments: dict[str, Any]) -> str | None:
    inline = arguments.get("transcript")
    if isinstance(inline, str) and inline.strip():
        return inline
    raw_path = arguments.get("transcript_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path).expanduser()
    if not path.exists() or not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _required_text(arguments: dict[str, Any], key: str) -> str:
    value = str(arguments.get(key, ""))
    if not value.strip():
        raise InvalidParamsError(f"tool argument '{key}' is required")
    return value


def _optional_int(arguments: dict[str, Any], key: str) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidParamsError(f"tool argument '{key}' must be an integer") from exc


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    raise SystemExit(main())
