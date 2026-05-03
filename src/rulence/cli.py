from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error as urllib_error

from .classifier import classify_task
from .config import write_default_memory_config
from .decomposer import decompose_prompt
from .doctor import run_doctor
from .feedback import read_feedback, record_feedback, summarize_feedback
from .mcp_client import McpError
from .memory import (
    MemoryProviderError,
    memory_health,
    memory_items_to_context,
    mempalace_rooms,
    mempalace_wings,
    retrieve_combined,
    retrieve_memory,
)

MEMORY_PROVIDER_ERRORS = (McpError, MemoryProviderError, urllib_error.URLError, OSError)
from .models import ReasoningSession
from .policy_cases import run_policy_cases
from .policies import (
    DEFAULT_POLICIES,
    default_policy_dir,
    install_builtin_policy,
    list_builtin_policies,
    validate_policy_dir,
    write_default_policies,
)
from .preflight import preflight_task
from .reasoning import add_thought, start_session, summarize_session
from .sessions import SessionStore

SEQUENTIAL_SESSION_ID = "__sequentialthinking__"


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (FileNotFoundError, IsADirectoryError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rulence", description="Local-first reasoning governance for agents.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    classify_parser = subparsers.add_parser("classify", help="Classify task difficulty/risk tier.")
    classify_parser.add_argument("task", help="Task text to classify.")
    classify_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    decompose_parser = subparsers.add_parser("decompose", help="Break a prompt into a typed task DAG.")
    decompose_parser.add_argument("source", nargs="?", default="-", help="Prompt text, file path, or '-' for stdin.")
    decompose_parser.add_argument("--max-depth", type=int, default=3, help="Maximum decomposition depth, 1-5.")
    decompose_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    preflight_parser = subparsers.add_parser("preflight", help="Run policy-driven preflight checks.")
    preflight_parser.add_argument("task", help="Task text to preflight.")
    preflight_parser.add_argument("--memory", default="", help="Optional local memory/context text.")
    preflight_parser.add_argument("--memory-file", help="Read local memory/context from a file.")
    preflight_parser.add_argument("--policy-dir", help="Directory containing local Rulence policy files.")
    preflight_parser.add_argument("--model", help="Optional model name for tokenizer-aware budgeting.")
    preflight_parser.add_argument("--memory-provider", choices=["local", "honcho", "mempalace"], help="Retrieve memory from a provider before preflight.")
    preflight_parser.add_argument("--memory-providers", help="Comma-separated providers for combined retrieval, e.g. local,honcho,mempalace.")
    preflight_parser.add_argument("--memory-transport", choices=["mcp", "rest", "auto"], default="auto", help="Force MCP or REST for providers that support both.")
    preflight_parser.add_argument("--memory-path", help="Path for local file/directory memory provider.")
    preflight_parser.add_argument("--memory-url", help="Base URL for Honcho/MemPalace compatible memory provider.")
    preflight_parser.add_argument("--memory-limit", type=int, default=5, help="Maximum memory chunks to retrieve.")
    preflight_parser.add_argument("--transcript", help="Path to a conversation transcript (JSONL or plain text) for transcript-aware checks.")
    preflight_parser.add_argument("--transcript-stdin", action="store_true", help="Read the conversation transcript from stdin.")
    preflight_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    start_parser = subparsers.add_parser("start", help="Start a governed reasoning session.")
    start_parser.add_argument("task", help="Task text to reason about.")
    start_parser.add_argument("--memory", default="", help="Optional local memory/context text.")
    start_parser.add_argument("--memory-file", help="Read local memory/context from a file.")
    start_parser.add_argument("--policy-dir", help="Directory containing local Rulence policy files.")
    start_parser.add_argument("--model", help="Optional model name for tokenizer-aware budgeting.")
    start_parser.add_argument("--memory-provider", choices=["local", "honcho", "mempalace"], help="Retrieve memory from a provider before thinking.")
    start_parser.add_argument("--memory-providers", help="Comma-separated providers for combined retrieval, e.g. local,honcho,mempalace.")
    start_parser.add_argument("--memory-transport", choices=["mcp", "rest", "auto"], default="auto", help="Force MCP or REST for providers that support both.")
    start_parser.add_argument("--memory-path", help="Path for local file/directory memory provider.")
    start_parser.add_argument("--memory-url", help="Base URL for Honcho/MemPalace compatible memory provider.")
    start_parser.add_argument("--memory-limit", type=int, default=5, help="Maximum memory chunks to retrieve.")
    start_parser.add_argument("--transcript", help="Path to a conversation transcript (JSONL or plain text).")
    start_parser.add_argument("--transcript-stdin", action="store_true", help="Read the conversation transcript from stdin.")
    start_parser.add_argument("--session-id", help="Save a persistent session under ~/.rulence/sessions.")
    start_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    think_parser = subparsers.add_parser("think", help="Run a governed sequential reasoning step.")
    think_parser.add_argument("task", help="Task text to reason about.")
    think_parser.add_argument("--thought", required=True, help="Reasoning step text.")
    think_parser.add_argument("--memory", default="", help="Optional local memory/context text.")
    think_parser.add_argument("--memory-file", help="Read local memory/context from a file.")
    think_parser.add_argument("--policy-dir", help="Directory containing local Rulence policy files.")
    think_parser.add_argument("--model", help="Optional model name for tokenizer-aware budgeting.")
    think_parser.add_argument("--memory-provider", choices=["local", "honcho", "mempalace"], help="Retrieve memory from a provider before thinking.")
    think_parser.add_argument("--memory-providers", help="Comma-separated providers for combined retrieval, e.g. local,honcho,mempalace.")
    think_parser.add_argument("--memory-transport", choices=["mcp", "rest", "auto"], default="auto", help="Force MCP or REST for providers that support both.")
    think_parser.add_argument("--memory-path", help="Path for local file/directory memory provider.")
    think_parser.add_argument("--memory-url", help="Base URL for Honcho/MemPalace compatible memory provider.")
    think_parser.add_argument("--memory-limit", type=int, default=5, help="Maximum memory chunks to retrieve.")
    think_parser.add_argument("--session-file", help="Load/save a reasoning session JSON file.")
    think_parser.add_argument("--session-id", help="Load/save a persistent session under ~/.rulence/sessions.")
    think_parser.add_argument("--thought-number", type=int, help="Current thought number.")
    think_parser.add_argument("--total-thoughts", type=int, default=1, help="Estimated total thoughts.")
    think_parser.add_argument("--next-thought-needed", action="store_true", help="Mark that another thought is needed.")
    think_parser.add_argument("--revision-of", type=int, help="Mark this thought as a revision of a previous thought.")
    think_parser.add_argument("--branch-from", type=int, help="Branch from a previous thought number.")
    think_parser.add_argument("--branch-id", help="Identifier for a reasoning branch.")
    think_parser.add_argument("--needs-more-thoughts", action="store_true", help="Mark the estimate as too low.")
    think_parser.add_argument("--transcript", help="Path to a conversation transcript (JSONL or plain text).")
    think_parser.add_argument("--transcript-stdin", action="store_true", help="Read the conversation transcript from stdin.")
    think_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    sequential_parser = subparsers.add_parser("sequentialthinking", help="Run the Sequential Thinking compatible tool.")
    sequential_parser.add_argument("--input-json", required=True, help="JSON object using Sequential Thinking argument names.")
    sequential_parser.add_argument("--session-id", default=SEQUENTIAL_SESSION_ID, help="Persistent compatibility session id.")
    sequential_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    init_parser = subparsers.add_parser("init", help="Write default local policy files.")
    init_parser.add_argument("--policy-dir", help="Policy directory. Defaults to ~/.rulence/policies.")
    init_parser.add_argument("--memory-config", help="Memory config path. Defaults to ~/.rulence/memory.toml.")

    install_parser = subparsers.add_parser("install", help="Install Rulence into an agent runner.")
    install_subparsers = install_parser.add_subparsers(dest="install_target", required=True)
    claude_install = install_subparsers.add_parser("claude-code", help="Install Claude Code PreToolUse hook.")
    claude_install.add_argument("--force", action="store_true")
    claude_install.add_argument("--json", action="store_true", help="Print JSON output.")
    cursor_install = install_subparsers.add_parser("cursor", help="Install Cursor project rule.")
    cursor_install.add_argument("--dir", help="Target project directory. Defaults to current directory.")
    cursor_install.add_argument("--json", action="store_true", help="Print JSON output.")
    n8n_install = install_subparsers.add_parser("n8n", help="Print n8n MCP config.")
    n8n_install.add_argument("--json", action="store_true", help="Print JSON output.")

    trace_parser = subparsers.add_parser("trace", help="Inspect a persisted reasoning session.")
    trace_parser.add_argument("session_id", nargs="?", help="Session id to inspect.")
    trace_parser.add_argument("--session-file", help="Read trace from an explicit session JSON file.")
    trace_parser.add_argument("--html", action="store_true", help="Output as self-contained HTML.")
    trace_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    policy_parser = subparsers.add_parser("policy", help="Manage local Rulence policies.")
    policy_subparsers = policy_parser.add_subparsers(dest="policy_command", required=True)
    policy_validate = policy_subparsers.add_parser("validate", help="Validate local policy files.")
    policy_validate.add_argument("--policy-dir", help="Policy directory. Defaults to ~/.rulence/policies.")
    policy_validate.add_argument("--json", action="store_true", help="Print JSON output.")
    policy_list = policy_subparsers.add_parser("list", help="List built-in policy tiers.")
    policy_list.add_argument("--json", action="store_true", help="Print JSON output.")
    policy_test = policy_subparsers.add_parser("test", help="Run policy regression cases.")
    policy_test.add_argument("cases", help="TOML file containing [[case]] entries.")
    policy_test.add_argument("--policy-dir", help="Policy directory. Defaults to ~/.rulence/policies.")
    policy_test.add_argument("--json", action="store_true", help="Print JSON output.")
    policy_install = policy_subparsers.add_parser("install", help="Install bundled starter policies.")
    policy_install.add_argument("names", nargs="+", help="Bundled policy names, e.g. migrations secrets git.")
    policy_install.add_argument("--policy-dir", help="Target directory. Defaults to ~/.rulence/policies.")
    policy_install.add_argument("--json", action="store_true", help="Print JSON output.")
    policy_available = policy_subparsers.add_parser("list-available", help="List bundled starter policies.")
    policy_available.add_argument("--json", action="store_true", help="Print JSON output.")

    doctor_parser = subparsers.add_parser("doctor", help="Check local Rulence environment.")
    doctor_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    memory_parser = subparsers.add_parser("memory", help="Query or inspect configured memory providers.")
    memory_parser.add_argument("target", nargs="?", help="Memory query, or health/wings/rooms.")
    memory_parser.add_argument("--provider", choices=["local", "honcho", "mempalace", "all"])
    memory_parser.add_argument("--transport", choices=["mcp", "rest", "auto"], default="auto")
    memory_parser.add_argument("--path", help="Path for local file/directory memory provider.")
    memory_parser.add_argument("--url", help="Base URL for Honcho/MemPalace compatible memory provider.")
    memory_parser.add_argument("--limit", type=int, default=5)
    memory_parser.add_argument("--wing", help="MemPalace wing for rooms command.")
    memory_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    feedback_parser = subparsers.add_parser("feedback", help="Record or inspect policy feedback.")
    feedback_parser.add_argument("task", nargs="?", help="Task the feedback applies to.")
    feedback_parser.add_argument("--outcome", choices=["correct", "false_positive", "false_negative", "other"])
    feedback_parser.add_argument("--verdict", choices=["approve", "warn", "block"])
    feedback_parser.add_argument("--session-id")
    feedback_parser.add_argument("--notes", default="")
    feedback_parser.add_argument("--path", help="Feedback JSONL path. Defaults to ~/.rulence/feedback.jsonl.")
    feedback_parser.add_argument("--list", action="store_true", help="List recorded feedback.")
    feedback_parser.add_argument("--summary", action="store_true", help="Summarize recorded feedback.")
    feedback_parser.add_argument("--limit", type=int, help="Limit list output to the most recent N records.")
    feedback_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    audit_parser = subparsers.add_parser("audit", help="Inspect runtime audit traces and token estimates.")
    audit_subparsers = audit_parser.add_subparsers(dest="audit_command", required=True)

    audit_list = audit_subparsers.add_parser("list", help="List session ids that have audit records.")
    audit_list.add_argument("--root", help="Audit root. Defaults to ~/.rulence/audit.")
    audit_list.add_argument("--json", action="store_true")

    audit_show = audit_subparsers.add_parser("show", help="Show audit records for a session/corr_id.")
    audit_show.add_argument("--session", required=True, help="Rulence session id (rs_...).")
    audit_show.add_argument("--corr", help="Optional corr_id to filter to one turn.")
    audit_show.add_argument("--root", help="Audit root. Defaults to ~/.rulence/audit.")
    audit_show.add_argument("--json", action="store_true")

    audit_tokens = audit_subparsers.add_parser("tokens", help="Summarize token estimates by session.")
    audit_tokens.add_argument("--session", required=True)
    audit_tokens.add_argument("--root", help="Audit root. Defaults to ~/.rulence/audit.")
    audit_tokens.add_argument("--json", action="store_true")

    audit_report = audit_subparsers.add_parser("report", help="Full audit report for a session.")
    audit_report.add_argument("--session", required=True)
    audit_report.add_argument("--root", help="Audit root. Defaults to ~/.rulence/audit.")
    audit_report.add_argument("--json", action="store_true")

    claims_parser = subparsers.add_parser("claims", help="Inspect and verify the claims ledger.")
    claims_subparsers = claims_parser.add_subparsers(dest="claims_command", required=True)
    claims_verify = claims_subparsers.add_parser("verify", help="Validate docs/claims.yml and scan public copy.")
    claims_verify.add_argument("--root", help="Repository root. Defaults to the package root.")
    claims_verify.add_argument("--json", action="store_true", help="Print JSON output.")
    claims_list = claims_subparsers.add_parser("list", help="List claim ids and statuses.")
    claims_list.add_argument("--root", help="Repository root. Defaults to the package root.")
    claims_list.add_argument("--json", action="store_true", help="Print JSON output.")

    hook_parser = subparsers.add_parser("hook", help=argparse.SUPPRESS)
    hook_subparsers = hook_parser.add_subparsers(dest="hook_target", required=True)
    hook_subparsers.add_parser("claude-code-pretooluse", help=argparse.SUPPRESS)
    hook_subparsers.add_parser("claude-code", help=argparse.SUPPRESS)

    subparsers.add_parser("mcp", help="Start the MCP stdio server.")

    args = parser.parse_args(argv)

    if args.command == "classify":
        result = classify_task(args.task)
        _print(result.to_dict(), args.json)
        return 0

    if args.command == "decompose":
        prompt = _read_prompt_source(args.source)
        if not prompt.strip():
            raise ValueError("prompt cannot be empty")
        result = decompose_prompt(prompt, max_depth=args.max_depth)
        _print_dag(result.to_dict(), args.json)
        return 0

    if args.command == "preflight":
        memory = args.memory
        if args.memory_file:
            memory = Path(args.memory_file).expanduser().read_text(encoding="utf-8")
        memory = _merge_memory(
            args.task,
            memory,
            args.memory_provider,
            args.memory_providers,
            args.memory_path,
            args.memory_url,
            args.memory_limit,
            args.memory_transport,
        )
        transcript = _resolve_transcript(args)
        result = preflight_task(args.task, memory=memory, policy_dir=args.policy_dir, model=args.model, transcript=transcript)
        _print(result.to_dict(), args.json)
        return 0 if result.verdict != "block" else 2

    if args.command == "start":
        memory = args.memory
        if args.memory_file:
            memory = Path(args.memory_file).expanduser().read_text(encoding="utf-8")
        memory = _merge_memory(
            args.task,
            memory,
            args.memory_provider,
            args.memory_providers,
            args.memory_path,
            args.memory_url,
            args.memory_limit,
            args.memory_transport,
        )
        transcript = _resolve_transcript(args)
        session = start_session(args.task, memory=memory, policy_dir=args.policy_dir, model=args.model, session_id=args.session_id, transcript=transcript)
        if args.session_id:
            SessionStore().save(session)
        _print_reasoning(session.to_dict(), args.json)
        return 0

    if args.command == "think":
        memory = args.memory
        if args.memory_file:
            memory = Path(args.memory_file).expanduser().read_text(encoding="utf-8")
        memory = _merge_memory(
            args.task,
            memory,
            args.memory_provider,
            args.memory_providers,
            args.memory_path,
            args.memory_url,
            args.memory_limit,
            args.memory_transport,
        )
        transcript = _resolve_transcript(args)
        session = _load_or_start_session(args.task, args.session_file, args.session_id, memory, args.policy_dir, args.model, transcript=transcript)
        session = add_thought(
            session,
            args.thought,
            thought_number=args.thought_number,
            total_thoughts=args.total_thoughts,
            next_thought_needed=args.next_thought_needed,
            is_revision=args.revision_of is not None,
            revises_thought=args.revision_of,
            branch_from_thought=args.branch_from,
            branch_id=args.branch_id,
            needs_more_thoughts=args.needs_more_thoughts,
        )
        if args.session_file:
            from .sessions import redacted_session_dict

            Path(args.session_file).expanduser().write_text(
                json.dumps(redacted_session_dict(session), indent=2, sort_keys=True),
                encoding="utf-8",
            )
        if args.session_id:
            SessionStore().save(session)
        _print_reasoning(session.to_dict(), args.json)
        return 0 if session.status != "blocked" else 2

    if args.command == "sequentialthinking":
        payload = _sequentialthinking(json.loads(args.input_json), args.session_id)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "init":
        written = write_default_policies(args.policy_dir)
        memory_target = args.memory_config
        if memory_target is None and args.policy_dir:
            memory_target = str(Path(args.policy_dir).expanduser().parent / "memory.toml")
        memory_config = write_default_memory_config(memory_target)
        directory = Path(args.policy_dir).expanduser() if args.policy_dir else default_policy_dir()
        print(f"policy_dir: {directory}")
        if written:
            for path in written:
                print(f"created: {path}")
        else:
            print("no changes: default policies already exist")
        if memory_config:
            print(f"created: {memory_config}")
        else:
            print("no changes: memory config already exists")
        return 0

    if args.command == "install":
        from .installers import install_claude_code, install_cursor, install_n8n

        if args.install_target == "claude-code":
            result = install_claude_code(force=args.force)
        elif args.install_target == "cursor":
            result = install_cursor(args.dir)
        elif args.install_target == "n8n":
            result = install_n8n()
        else:
            return 2

        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"status: {result['status']}")
            if "path" in result:
                print(f"path: {result['path']}")
            if result.get("backup"):
                print(f"backup: {result['backup']}")
            if "config" in result:
                print("config to paste:")
                print(json.dumps(result["config"], indent=2, sort_keys=True))
        return 0

    if args.command == "trace":
        if args.session_file:
            session = ReasoningSession.from_dict(json.loads(Path(args.session_file).expanduser().read_text(encoding="utf-8")))
        elif args.session_id:
            store = SessionStore()
            if not store.exists(args.session_id):
                raise ValueError(f"unknown session_id: {args.session_id}")
            session = store.load(args.session_id)
        else:
            ids = SessionStore().list_ids()
            data = {"sessions": ids}
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                print("sessions:")
                for session_id in ids:
                    print(f"  - {session_id}")
            return 0
        if args.html:
            from .trace_html import render_trace_html

            print(render_trace_html(session))
            return 0
        _print_reasoning(session.to_dict(), args.json)
        return 0

    if args.command == "policy":
        if args.policy_command == "validate":
            results = validate_policy_dir(args.policy_dir)
            failed = {path: errors for path, errors in results.items() if errors}
            if args.json:
                print(json.dumps(results, indent=2, sort_keys=True))
            else:
                for path, errors in results.items():
                    if errors:
                        print(f"fail: {path}")
                        for error in errors:
                            print(f"  - {error}")
                    else:
                        print(f"pass: {path}")
            return 0 if not failed else 2
        if args.policy_command == "list":
            data = {tier: policy.to_dict() for tier, policy in DEFAULT_POLICIES.items()}
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                for tier, policy in DEFAULT_POLICIES.items():
                    print(f"tier {tier}: {policy.label} -> {', '.join(policy.required_checks) or 'no checks'}")
            return 0
        if args.policy_command == "test":
            results = run_policy_cases(args.cases, policy_dir=args.policy_dir)
            failed = [result for result in results if not result["pass"]]
            if args.json:
                print(json.dumps(results, indent=2, sort_keys=True))
            else:
                for result in results:
                    status = "pass" if result["pass"] else "fail"
                    print(f"{status}: {result['name']} tier={result['actual_tier']} verdict={result['actual_verdict']}")
                    for failure in result["failures"]:
                        print(f"  - {failure}")
            return 0 if not failed else 2
        if args.policy_command == "install":
            installed = [install_builtin_policy(name, args.policy_dir) for name in args.names]
            data = {"installed": [str(path) for path in installed]}
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                for path in installed:
                    print(f"installed: {path}")
            return 0
        if args.policy_command == "list-available":
            policies = list_builtin_policies()
            if args.json:
                print(json.dumps(policies, indent=2, sort_keys=True))
            else:
                for policy in policies:
                    print(f"{policy['name']}: tier={policy['tier']} label={policy['label']} constraints={policy['constraints_count']}")
            return 0

    if args.command == "doctor":
        checks = run_doctor(Path(__file__).resolve().parents[2])
        if args.json:
            print(json.dumps([check.to_dict() for check in checks], indent=2, sort_keys=True))
        else:
            for check in checks:
                print(f"{check.status}: {check.name} - {check.detail}")
        return 1 if any(check.status == "fail" for check in checks) else 0

    if args.command == "memory":
        if args.target == "health":
            _print_memory_payload(memory_health(args.provider or "all", path=args.path), args.json)
            return 0
        if args.target == "wings":
            try:
                wings = mempalace_wings()
            except (McpError, MemoryProviderError) as exc:
                _print_memory_payload(
                    {"provider": "mempalace", "reachable": False, "transport": "mcp", "wings": [], "error": str(exc)},
                    args.json,
                )
                return 1
            _print_memory_payload(wings, args.json)
            return 0
        if args.target == "rooms":
            if not args.wing:
                print("memory rooms requires --wing", file=sys.stderr)
                return 2
            try:
                rooms = mempalace_rooms(args.wing)
            except (McpError, MemoryProviderError) as exc:
                _print_memory_payload(
                    {"provider": "mempalace", "reachable": False, "transport": "mcp", "wing": args.wing, "rooms": [], "error": str(exc)},
                    args.json,
                )
                return 1
            _print_memory_payload(rooms, args.json)
            return 0
        if not args.target or not args.provider:
            print("memory requires QUERY and --provider, or one of: health, wings, rooms", file=sys.stderr)
            return 2
        try:
            items = retrieve_memory(
                args.provider,
                args.target,
                path=args.path,
                url=args.url,
                limit=args.limit,
                transport=args.transport,
            )
        except MEMORY_PROVIDER_ERRORS as exc:
            print(f"error: memory provider '{args.provider}' unreachable: {exc}", file=sys.stderr)
            return 1
        _print_memory_payload([item.to_dict() for item in items], args.json)
        return 0

    if args.command == "feedback":
        if args.list:
            records = read_feedback(path=args.path, limit=args.limit)
            if args.json:
                print(json.dumps(records, indent=2, sort_keys=True))
            else:
                for record in records:
                    print(f"{record.get('created_at')} {record.get('outcome')} verdict={record.get('verdict')} task={record.get('task')}")
            return 0
        if args.summary:
            summary = summarize_feedback(path=args.path)
            if args.json:
                print(json.dumps(summary, indent=2, sort_keys=True))
            else:
                print(f"count: {summary['count']}")
                print(f"by_outcome: {summary['by_outcome']}")
                print(f"by_verdict: {summary['by_verdict']}")
            return 0
        if not args.task or not args.outcome:
            print("feedback requires TASK and --outcome unless --list or --summary is used", file=sys.stderr)
            return 2
        record = record_feedback(
            task=args.task,
            outcome=args.outcome,
            verdict=args.verdict,
            session_id=args.session_id,
            notes=args.notes,
            path=args.path,
        )
        if args.json:
            print(json.dumps(record, indent=2, sort_keys=True))
        else:
            print("feedback recorded")
        return 0

    if args.command == "audit":
        from .audit import AuditTraceStore, TokenAccountant

        store = AuditTraceStore(args.root) if getattr(args, "root", None) else AuditTraceStore()
        if args.audit_command == "list":
            sessions = store.list_sessions()
            if args.json:
                print(json.dumps({"sessions": sessions}, indent=2, sort_keys=True))
            else:
                for sid in sessions:
                    print(sid)
            return 0
        if args.audit_command == "show":
            if args.corr:
                records = store.read_turn(args.session, args.corr)
            else:
                records = store.read_session(args.session)
            if args.json:
                print(json.dumps(records, indent=2, sort_keys=True))
            else:
                for record in records:
                    print(_format_audit_line(record))
            return 0
        if args.audit_command == "tokens":
            records = store.read_session(args.session)
            rollup = TokenAccountant().rollup(records)
            data = rollup.to_dict()
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                _print_token_rollup(args.session, data)
            return 0
        if args.audit_command == "report":
            records = store.read_session(args.session)
            report = _build_audit_report(args.session, records)
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                _print_audit_report(report)
            return 0

    if args.command == "claims":
        from .claims import load_claims, verify_repo

        root = Path(args.root).expanduser().resolve() if args.root else Path(__file__).resolve().parents[2]
        if args.claims_command == "verify":
            violations = verify_repo(root)
            if args.json:
                print(json.dumps([v.to_dict() for v in violations], indent=2, sort_keys=True))
            else:
                for violation in violations:
                    location = f"{violation.file}:{violation.line}" if violation.line else violation.file
                    print(f"{location}: {violation.message}")
                if not violations:
                    print("ok: claims ledger and public copy are consistent")
            return 0 if not violations else 2
        if args.claims_command == "list":
            ledger_path = root / "docs" / "claims.yml"
            if not ledger_path.exists():
                print(f"error: {ledger_path} not found", file=sys.stderr)
                return 2
            ledger = load_claims(ledger_path)
            rows = [
                {
                    "claim_id": claim.claim_id,
                    "status": claim.status,
                    "public_copy_allowed": claim.public_copy_allowed,
                    "claim_text": claim.claim_text,
                }
                for claim in ledger.claims
            ]
            if args.json:
                print(json.dumps(rows, indent=2, sort_keys=True))
            else:
                for row in rows:
                    public = "public" if row["public_copy_allowed"] else "internal"
                    print(f"{row['claim_id']:40s} {row['status']:13s} {public:8s} {row['claim_text']}")
            return 0

    if args.command == "hook":
        if args.hook_target == "claude-code-pretooluse":
            print(json.dumps(_claude_code_pretooluse(sys.stdin.read()), sort_keys=True))
            return 0
        if args.hook_target == "claude-code":
            print(json.dumps(_claude_code_hook(sys.stdin.read()), sort_keys=True))
            return 0
        return 2

    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        return mcp_main()

    return 1


def _load_or_start_session(
    task: str,
    session_file: str | None,
    session_id: str | None,
    memory: str,
    policy_dir: str | None,
    model: str | None,
    transcript: str | None = None,
) -> ReasoningSession:
    if session_file:
        path = Path(session_file).expanduser()
        if path.exists():
            return ReasoningSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if session_id:
        store = SessionStore()
        if store.exists(session_id):
            return store.load(session_id)
        return start_session(task, memory=memory, policy_dir=policy_dir, model=model, session_id=session_id, transcript=transcript)
    return start_session(task, memory=memory, policy_dir=policy_dir, model=model, transcript=transcript)


def _merge_memory(
    task: str,
    memory: str,
    provider: str | None,
    providers: str | None,
    path: str | None,
    url: str | None,
    limit: int,
    transport: str,
) -> str:
    items = []
    if providers:
        provider_names = [name.strip() for name in providers.split(",") if name.strip()]
        paths = {"local": path} if path else {}
        urls = {name: url for name in provider_names if url and name != "local"}
        try:
            items = retrieve_combined(provider_names, task, paths=paths, urls=urls, limit=limit, transport=transport)
        except MEMORY_PROVIDER_ERRORS as exc:
            print(f"warn: memory providers unreachable ({','.join(provider_names)}): {exc}", file=sys.stderr)
    elif provider:
        try:
            items = retrieve_memory(provider, task, path=path, url=url, limit=limit, transport=transport)
        except MEMORY_PROVIDER_ERRORS as exc:
            print(f"warn: memory provider '{provider}' unreachable: {exc}", file=sys.stderr)
    retrieved = memory_items_to_context(items)
    return "\n\n".join(part for part in (memory, retrieved) if part.strip())


def _resolve_transcript(args: argparse.Namespace) -> str | None:
    if getattr(args, "transcript", None) and getattr(args, "transcript_stdin", False):
        raise ValueError("--transcript and --transcript-stdin are mutually exclusive")
    if getattr(args, "transcript", None):
        from .transcript import MAX_TRANSCRIPT_BYTES
        path = Path(args.transcript).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"transcript file not found: {path}")
        size = path.stat().st_size
        if size > MAX_TRANSCRIPT_BYTES:
            raise ValueError(
                f"transcript {path} is {size} bytes, exceeds MAX_TRANSCRIPT_BYTES ({MAX_TRANSCRIPT_BYTES})"
            )
        return path.read_text(encoding="utf-8", errors="replace")
    if getattr(args, "transcript_stdin", False):
        return sys.stdin.read()
    return None


def _read_prompt_source(source: str) -> str:
    if not source.strip():
        return ""
    if source == "-":
        return sys.stdin.read()
    path = Path(source).expanduser()
    if path.exists():
        return path.read_text(encoding="utf-8")
    return source


def _sequentialthinking(input_data: dict, session_id: str) -> dict:
    thought = str(input_data.get("thought", ""))
    if not thought.strip():
        raise ValueError("thought is required")

    store = SessionStore()
    session = store.load(session_id) if store.exists(session_id) else start_session(
        thought,
        policy_dir=os.environ.get("RULENCE_POLICY_DIR"),
        session_id=session_id,
    )
    thought_number = int(input_data.get("thoughtNumber") or input_data.get("thought_number") or 1)
    total_thoughts = max(int(input_data.get("totalThoughts") or input_data.get("total_thoughts") or 1), thought_number)
    session = add_thought(
        session,
        thought,
        thought_number=thought_number,
        total_thoughts=total_thoughts,
        next_thought_needed=_coerce_bool(input_data.get("nextThoughtNeeded", input_data.get("next_thought_needed", False))),
        is_revision=_coerce_bool(input_data.get("isRevision", input_data.get("is_revision", False))),
        revises_thought=_optional_payload_int(input_data, "revisesThought", "revises_thought"),
        branch_from_thought=_optional_payload_int(input_data, "branchFromThought", "branch_from_thought"),
        branch_id=input_data.get("branchId") or input_data.get("branch_id"),
        needs_more_thoughts=_coerce_bool(input_data.get("needsMoreThoughts", input_data.get("needs_more_thoughts", False))),
    )
    store.save(session)

    step = session.steps[-1]
    branches = sorted({existing.branch_id for existing in session.steps if existing.branch_id})
    return {
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


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _optional_payload_int(input_data: dict, camel_key: str, snake_key: str) -> int | None:
    value = input_data.get(camel_key, input_data.get(snake_key))
    if value is None:
        return None
    return int(value)


def _claude_hook_transcript(payload: dict) -> str | None:
    raw_path = payload.get("transcript_path") or payload.get("transcriptPath")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    try:
        from .transcript import MAX_TRANSCRIPT_BYTES
        path = Path(raw_path).expanduser()
        if not path.exists() or not path.is_file():
            return None
        if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
            print(
                f"warn: Claude Code transcript at {path} exceeds {MAX_TRANSCRIPT_BYTES} bytes; skipping transcript-aware checks",
                file=sys.stderr,
            )
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _claude_code_pretooluse(raw_input: str) -> dict:
    """Backward-compatible entrypoint for ``rulence hook claude-code-pretooluse``.

    Existing settings.json files in the wild call the legacy subcommand
    directly. The unified dispatcher below replaces it for fresh installs.
    """
    payload = _parse_hook_payload(raw_input)
    return _claude_code_pretooluse_dispatch(payload)


def _claude_code_hook(raw_input: str) -> dict:
    """Unified Claude Code hook dispatcher.

    Reads the ``hook_event_name`` from the payload and routes to the
    matching handler. Every handled event produces an audit record via
    :class:`rulence.audit.AuditTraceStore`.
    """
    payload = _parse_hook_payload(raw_input)
    event_name = str(
        payload.get("hook_event_name") or payload.get("hookEventName") or ""
    ).strip()
    if event_name == "PreToolUse" or not event_name:
        return _claude_code_pretooluse_dispatch(payload)
    if event_name == "PostToolUse":
        return _claude_code_posttooluse(payload)
    if event_name == "UserPromptSubmit":
        return _claude_code_userpromptsubmit(payload)
    if event_name == "SessionStart":
        return _claude_code_sessionstart(payload)
    if event_name == "SessionEnd":
        return _claude_code_sessionend(payload)
    if event_name == "Stop":
        return _claude_code_stop(payload)
    # Unknown lifecycle event: still produce an audit record and allow.
    _record_audit(payload, event_name, decision=None)
    return {"suppressOutput": True}


def _parse_hook_payload(raw_input: str) -> dict:
    payload = json.loads(raw_input) if raw_input.strip() else {}
    if not isinstance(payload, dict):
        raise ValueError("Claude Code hook input must be a JSON object")
    return payload


def _record_audit(
    payload: dict,
    event_type: str,
    *,
    decision: str | None = None,
    evidence: tuple[str, ...] = (),
    tool_name: str | None = None,
    policy_name: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Write a single audit record for a Claude Code hook event.

    All text fields (evidence, metadata, tool_name, policy_name) pass
    through :func:`rulence.security.redact_payload` before they are
    persisted. The raw payload is hashed, never stored. Audit failures
    degrade to a stderr warning and never break the hook response.
    """
    try:
        import hashlib

        from .audit import AuditTraceStore, CorrelationIdManager, RulenceAuditEvent
        from .audit.correlation import hash_text
        from .security import redact_payload

        claude_session = payload.get("session_id") or payload.get("sessionId")
        manager = CorrelationIdManager()

        if event_type == "SessionStart":
            ctx = manager.session_started(claude_session)
        elif event_type == "UserPromptSubmit":
            prompt = str(payload.get("prompt") or payload.get("user_message") or "")
            prompt_hash = hash_text(prompt) if prompt else None
            ctx = manager.user_prompt(claude_session, prompt_hash)
        elif event_type == "SessionEnd":
            ctx = manager.session_ended(claude_session)
        else:
            ctx = manager.current(claude_session)

        raw_payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

        # Apply redaction at the audit boundary. Text-bearing fields go
        # through redact_payload; other fields are scalars or hashes.
        red_evidence, ev_count, ev_types = redact_payload(list(evidence))
        red_metadata, md_count, md_types = redact_payload(dict(metadata or {}))
        red_tool_name, tn_count, tn_types = redact_payload(tool_name) if tool_name else (None, 0, ())
        red_policy_name, pn_count, pn_types = redact_payload(policy_name) if policy_name else (None, 0, ())

        total_redactions = ev_count + md_count + tn_count + pn_count
        all_types = tuple([*ev_types, *md_types, *tn_types, *pn_types])

        # Top-level token_estimate on the audit event mirrors the sum of
        # input + output estimates that callers stored in metadata. None
        # if neither was recorded for this event type.
        in_est = red_metadata.get("input_estimate") if isinstance(red_metadata, dict) else None
        out_est = red_metadata.get("output_estimate") if isinstance(red_metadata, dict) else None
        token_total: int | None = None
        if isinstance(in_est, int) or isinstance(out_est, int):
            token_total = int(in_est or 0) + int(out_est or 0)

        event = RulenceAuditEvent.now(
            event_type=event_type,
            session_id=ctx.rulence_session_id,
            corr_id=ctx.corr_id,
            runner="claude_code",
            decision=decision,
            evidence=tuple(red_evidence),
            policy_name=red_policy_name,
            tool_name=red_tool_name,
            redaction_count=total_redactions,
            redaction_types=all_types,
            token_estimate=token_total,
            payload_redacted=total_redactions > 0,
            raw_payload_hash=raw_payload_hash,
            metadata=red_metadata,
        )
        AuditTraceStore().append(event)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"warn: rulence audit write failed: {exc}", file=sys.stderr)


def _claude_code_pretooluse_dispatch(payload: dict) -> dict:
    from .token_budget import estimate_tokens
    from .tool_risk import ToolRiskClassifier

    tool_name = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else None
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}

    classification = ToolRiskClassifier().classify(
        tool_name=tool_name,
        tool_input=tool_input,
        runner="claude_code",
    )

    # Estimate the synthesized task text as the input side of this event.
    task_text = _claude_tool_task(payload)
    in_count, in_method = (
        estimate_tokens(task_text) if task_text else (0, "none")
    )

    base_metadata = {
        "fast_path": not classification.should_run_full_preflight,
        "risk_level": classification.risk_level,
        "risk_reasons": list(classification.risk_reasons),
        "input_estimate": in_count,
        "output_estimate": 0,
        "estimator_name": in_method,
    }

    # When the classifier signals memory is required (high/critical
    # severity), route the read through the arbiter so the audit
    # trail records the attempt regardless of whether a provider is
    # configured.
    if classification.requires_memory_check and task_text:
        scope = (
            "project_context"
            if classification.risk_level == "critical"
            else "code_context"
        )
        base_metadata.update(_attempt_memory_read(task_text, scope=scope))

    # Fast path: no policy load, no preflight, no memory call. Audit and allow.
    if not classification.should_run_full_preflight:
        _record_audit(
            payload,
            "PreToolUse",
            decision="approve",
            tool_name=tool_name,
            metadata=base_metadata,
        )
        return {"suppressOutput": True}

    # Full path: existing preflight behavior, with severity escalation
    # for high/critical classifier verdicts so the universal matcher
    # never silently allows a clearly destructive call.
    task = _claude_tool_task(payload)
    if not task.strip():
        _record_audit(
            payload,
            "PreToolUse",
            decision="approve",
            tool_name=tool_name,
            metadata={**base_metadata, "reason": "no_task"},
        )
        return {"suppressOutput": True}

    transcript = _claude_hook_transcript(payload)
    result = preflight_task(
        task,
        policy_dir=os.environ.get("RULENCE_POLICY_DIR"),
        transcript=transcript,
    )
    policy_ref = getattr(result.policy, "ref", None)
    checks_run = [check.name for check in result.checks]
    required_followups = list(result.next_steps)
    escalated_action = _escalate_action_for_risk(classification.risk_level, result.verdict)

    full_metadata = {
        **base_metadata,
        "preflight_verdict": result.verdict,
        "checks_run": checks_run,
        "required_followups": required_followups,
    }

    if escalated_action == "deny":
        evidence = result.blocks or (
            tuple(classification.risk_reasons) or ("classifier:critical_call",)
        )
        _record_audit(
            payload,
            "PreToolUse",
            decision="deny",
            evidence=tuple(evidence),
            tool_name=tool_name,
            policy_name=policy_ref,
            metadata=full_metadata,
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _hook_reason(
                    "Rulence blocked this tool call", evidence
                ),
            }
        }
    if escalated_action == "ask":
        evidence = (
            result.warnings
            or result.next_steps
            or tuple(classification.risk_reasons)
            or ("classifier:high_risk_call",)
        )
        _record_audit(
            payload,
            "PreToolUse",
            decision="ask",
            evidence=tuple(evidence),
            tool_name=tool_name,
            policy_name=policy_ref,
            metadata=full_metadata,
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": _hook_reason("Rulence warning", evidence),
            }
        }
    _record_audit(
        payload,
        "PreToolUse",
        decision="approve",
        tool_name=tool_name,
        policy_name=policy_ref,
        metadata=full_metadata,
    )
    return {"suppressOutput": True}


def _escalate_action_for_risk(risk_level: str, preflight_verdict: str) -> str:
    """Combine classifier severity with preflight verdict.

    Deterministic rule: ``critical`` always denies, ``high`` always at
    least asks, ``medium`` follows preflight, ``low`` is fast-pathed
    upstream and never reaches here. This keeps risky calls from
    silently passing through a permissive policy file.
    """
    base = {"approve": "allow", "warn": "ask", "block": "deny"}.get(
        preflight_verdict, "ask"
    )
    if risk_level == "critical":
        return "deny"
    if risk_level == "high":
        return "deny" if base == "deny" else "ask"
    return base


def _claude_code_sessionstart(payload: dict) -> dict:
    _record_audit(payload, "SessionStart")
    return {"suppressOutput": True}


def _claude_code_userpromptsubmit(payload: dict) -> dict:
    from .classifier import classify_task
    from .token_budget import estimate_tokens

    prompt = str(payload.get("prompt") or payload.get("user_message") or "")
    in_count, in_method = estimate_tokens(prompt) if prompt else (0, "none")
    metadata = {
        "input_estimate": in_count,
        "output_estimate": 0,
        "estimator_name": in_method,
    }

    if prompt:
        tier = classify_task(prompt).tier
        metadata["task_tier"] = tier
        # Tier 3+ work usually benefits from memory context. Route the
        # read through the arbiter so the audit trail records which
        # backend was queried, even when providers are absent and the
        # call degrades.
        if tier >= 3:
            scope = "project_context" if tier >= 4 else "user_preferences"
            metadata.update(_attempt_memory_read(prompt, scope=scope))

    _record_audit(payload, "UserPromptSubmit", metadata=metadata)
    return {"suppressOutput": True}


def _attempt_memory_read(query: str, *, scope: str, corr_id: str | None = None,
                         policy_name: str | None = None) -> dict:
    """Run an audit-only memory read through the arbiter.

    Returns a flat dict suitable for merging into audit metadata.
    Failures are captured (never raised); the caller still writes an
    audit record.
    """
    try:
        from .memory import MemoryArbiter

        result = MemoryArbiter().read(
            query,
            scope=scope,
            policy=policy_name,
            corr_id=corr_id,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "memory_read_attempted": False,
            "memory_read_error": str(exc),
        }
    return {
        "memory_read_attempted": True,
        "memory_read_scope": scope,
        "memory_read_backend": result.backend,
        "memory_read_role": result.backend_role,
        "memory_read_degraded": result.degraded,
        "memory_read_item_count": len(result.items),
        "memory_read_error": result.error,
    }


def _claude_code_posttooluse(payload: dict) -> dict:
    """Observe and audit a Claude Code PostToolUse event.

    Claude Code's PostToolUse hook can append context but cannot replace
    the tool result the model already received. Rulence therefore
    *detects, redacts for audit, warns, and marks unsafe* — it does not
    filter the result before reinjection.
    """
    from .review import ToolResultReviewer

    tool_name = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else None
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    tool_output = payload.get("tool_response")
    if tool_output is None:
        tool_output = payload.get("tool_output")

    review = ToolResultReviewer().review(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
    )

    metadata: dict = {
        "review_status": review.status,
        "redaction_count": review.redaction_count,
        "redaction_types": list(review.redaction_types),
        "original_token_estimate": review.original_token_estimate,
        "filtered_token_estimate": review.filtered_token_estimate,
        "safe_for_model_context": review.safe_for_model_context,
        "suggested_action": review.suggested_action,
        # M7 accounting: PostToolUse is an output event from the tool's
        # perspective. Use the original (pre-redaction, pre-compaction)
        # estimate as what the model effectively saw.
        "input_estimate": 0,
        "output_estimate": review.original_token_estimate,
        "estimator_name": "tool_result_reviewer",
    }
    if review.summary is not None:
        metadata["summary_omitted_sections"] = list(review.summary.omitted_sections)
        metadata["summary_retained_fact_count"] = len(review.summary.retained_facts)

    _record_audit(
        payload,
        "PostToolUse",
        decision=review.status,
        evidence=review.evidence,
        tool_name=tool_name,
        metadata=metadata,
    )
    return {"suppressOutput": True}


def _claude_code_stop(payload: dict) -> dict:
    """Run final-response review at Claude Code Stop time.

    Reads audit records for the current ``corr_id``, parses any
    requires/forbids constraints from the transcript, and runs the
    deterministic :class:`FinalResponseReviewer`. Honors
    ``stop_hook_active`` to prevent infinite continuation loops.
    """
    from .audit import AuditTraceStore, CorrelationIdManager
    from .logic import parse_constraints
    from .review import FinalResponseReviewer
    from .transcript import parse_transcript_text

    stop_hook_active = bool(payload.get("stop_hook_active"))
    claude_session = payload.get("session_id") or payload.get("sessionId")

    transcript_text = _claude_hook_transcript(payload) or ""
    final_response = _final_response_from_payload(payload, transcript_text)

    manager = CorrelationIdManager()
    ctx = manager.current(claude_session)
    audit_records: list[dict] = []
    if ctx.corr_id:
        try:
            audit_records = AuditTraceStore().read_turn(ctx.rulence_session_id, ctx.corr_id)
        except Exception:
            audit_records = []
        # Drop prior final_review records so a re-stop does not
        # double-count its own metadata.
        audit_records = [
            r for r in audit_records if r.get("event_type") != "Stop"
        ]

    # Parse constraints from user-content text only. The raw transcript
    # is JSONL; ``parse_constraints`` matches ``forbids:`` / ``requires:``
    # patterns line-by-line and would not match through JSON wrapping.
    user_text = ""
    if transcript_text:
        try:
            turns = parse_transcript_text(transcript_text)
            user_text = "\n".join(t.content for t in turns if t.role == "user")
        except Exception:
            user_text = ""
    constraints = list(parse_constraints(user_text))

    review = FinalResponseReviewer().review(
        final_response=final_response,
        audit_records=audit_records,
        constraints=tuple(constraints),
        transcript=transcript_text,
    )

    from .token_budget import estimate_tokens as _estimate_tokens

    final_tokens, final_method = (
        _estimate_tokens(final_response) if final_response else (0, "none")
    )
    metadata = {
        "review_status": review.status,
        "suggested_action": review.suggested_action,
        "violated_constraints": list(review.violated_constraints),
        "missing_required_actions": list(review.missing_required_actions),
        "trace_mismatches": list(review.trace_mismatches),
        "secret_finding_count": len(review.secret_findings),
        "audit_records_inspected": len(audit_records),
        "stop_hook_active": stop_hook_active,
        # M7 accounting: the final response is the output side of Stop.
        "input_estimate": 0,
        "output_estimate": final_tokens,
        "estimator_name": final_method,
    }

    _record_audit(
        payload,
        "Stop",
        decision=review.status,
        evidence=review.evidence,
        metadata=metadata,
    )

    # Conservative continuation: only request a revision when the review
    # failed and Claude Code is not already inside a continuation. This
    # is the documented loop-prevention contract for the Stop hook.
    if review.status == "fail" and not stop_hook_active:
        return {
            "decision": "block",
            "reason": _hook_reason(
                "Rulence final-response review failed", review.evidence
            ),
        }

    return {"suppressOutput": True}


def _final_response_from_payload(payload: dict, transcript_text: str) -> str:
    """Best-effort extraction of the assistant's final message."""
    direct = (
        payload.get("final_response")
        or payload.get("final_message")
        or payload.get("response")
    )
    if isinstance(direct, str) and direct.strip():
        return direct
    if not transcript_text:
        return ""
    from .transcript import parse_transcript_text

    turns = parse_transcript_text(transcript_text)
    for turn in reversed(turns):
        if turn.role == "assistant" and turn.content.strip():
            return turn.content
    return ""


def _claude_code_sessionend(payload: dict) -> dict:
    _record_audit(payload, "SessionEnd")
    return {"suppressOutput": True}


def _claude_tool_task(payload: dict) -> str:
    tool_name = str(payload.get("tool_name", "tool")).strip() or "tool"
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return f"{tool_name}\n{tool_input}"

    fields = []
    for key in ("command", "file_path", "path", "url", "pattern", "content", "old_string", "new_string"):
        value = tool_input.get(key)
        if value:
            fields.append(f"{key}: {value}")
    if not fields:
        fields.append(json.dumps(tool_input, sort_keys=True))
    return "\n".join([tool_name, *fields])


def _hook_reason(prefix: str, details: tuple[str, ...]) -> str:
    detail = "; ".join(details[:3]) if details else "no detail"
    return f"{prefix}: {detail}"


def _format_audit_line(record: dict) -> str:
    timestamp = record.get("timestamp", "")
    event_type = record.get("event_type", "?")
    decision = record.get("decision") or "-"
    tool = record.get("tool_name") or "-"
    redactions = record.get("redaction_count", 0)
    tokens = record.get("token_estimate")
    token_str = f"{tokens}t" if isinstance(tokens, int) else "-"
    return f"{timestamp} {event_type:18s} decision={decision:8s} tool={tool:30s} redactions={redactions} tokens={token_str}"


def _print_token_rollup(session_id: str, data: dict) -> None:
    print(f"session: {session_id}")
    print(f"total_estimated:        {data['total_estimated']}")
    print(f"total_estimated_input:  {data['total_estimated_input']}")
    print(f"total_estimated_output: {data['total_estimated_output']}")
    print(f"rulence_overhead_estimate: {data['rulence_overhead_estimate']}")
    print("by_event_type:")
    for k, v in sorted(data["by_event_type"].items()):
        print(f"  {k:20s} {v}")
    if data["by_tool"]:
        print("by_tool:")
        for k, v in sorted(data["by_tool"].items()):
            print(f"  {k:30s} {v}")
    if data["by_memory_backend"]:
        print("by_memory_backend:")
        for k, v in sorted(data["by_memory_backend"].items()):
            print(f"  {k:20s} {v}")
    if data["by_corr_id"]:
        print("by_corr_id:")
        for k, v in sorted(data["by_corr_id"].items()):
            print(f"  {k:25s} {v}")


def _build_audit_report(session_id: str, records: list[dict]) -> dict:
    from .audit import TokenAccountant

    rollup = TokenAccountant().rollup(records)

    decisions: dict[str, int] = {}
    tools: dict[str, int] = {}
    redactions = 0
    redaction_types: dict[str, int] = {}
    final_review_status: str | None = None
    pretooluse_count = 0
    posttooluse_count = 0

    for record in records:
        decision = record.get("decision")
        if decision:
            decisions[decision] = decisions.get(decision, 0) + 1
        tool = record.get("tool_name")
        if tool:
            tools[tool] = tools.get(tool, 0) + 1
        redactions += int(record.get("redaction_count") or 0)
        for rt in record.get("redaction_types") or []:
            redaction_types[rt] = redaction_types.get(rt, 0) + 1
        if record.get("event_type") == "PreToolUse":
            pretooluse_count += 1
        if record.get("event_type") == "PostToolUse":
            posttooluse_count += 1
        if record.get("event_type") == "Stop":
            meta = record.get("metadata") or {}
            final_review_status = meta.get("review_status") or final_review_status

    return {
        "session_id": session_id,
        "record_count": len(records),
        "tokens": rollup.to_dict(),
        "decisions": dict(sorted(decisions.items())),
        "tool_call_counts": dict(sorted(tools.items())),
        "redaction_count": redactions,
        "redaction_types": dict(sorted(redaction_types.items())),
        "final_review_status": final_review_status,
        "pretooluse_count": pretooluse_count,
        "posttooluse_count": posttooluse_count,
    }


def _print_audit_report(report: dict) -> None:
    print(f"session: {report['session_id']}")
    print(f"record_count: {report['record_count']}")
    print(f"pretooluse_count: {report['pretooluse_count']}")
    print(f"posttooluse_count: {report['posttooluse_count']}")
    print(f"final_review_status: {report['final_review_status']}")
    print(f"redaction_count: {report['redaction_count']}")
    if report["redaction_types"]:
        print("redaction_types:")
        for k, v in report["redaction_types"].items():
            print(f"  {k:30s} {v}")
    if report["decisions"]:
        print("decisions:")
        for k, v in report["decisions"].items():
            print(f"  {k:10s} {v}")
    if report["tool_call_counts"]:
        print("tools:")
        for k, v in report["tool_call_counts"].items():
            print(f"  {k:30s} {v}")
    print()
    _print_token_rollup(report["session_id"], report["tokens"])


def _print(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return

    if "classification" not in data:
        print(f"{data['label']}")
        for reason in data["reasons"]:
            print(f"- {reason}")
        return

    classification = data["classification"]
    policy = data["policy"]
    print(f"verdict: {data['verdict']}")
    print(f"tier: {classification['label']}")
    print(f"policy: {policy['ref']}")
    print("required_checks:")
    for check in policy["required_checks"]:
        print(f"  - {check}")
    if data["warnings"]:
        print("warnings:")
        for warning in data["warnings"]:
            print(f"  - {warning}")
    if data["blocks"]:
        print("blocks:")
        for block in data["blocks"]:
            print(f"  - {block}")
    if data["token_budget"]:
        budget = data["token_budget"]
        print("token_budget:")
        print(f"  input_estimate: {budget['input_estimate']}")
        print(f"  governed_context_estimate: {budget['governed_context_estimate']}")
        print(f"  policy_overhead_estimate: {budget['policy_overhead_estimate']}")
    if data.get("decomposition"):
        decomposition = data["decomposition"]
        print("decomposition:")
        print(f"  root_units: {len(decomposition['root_units'])}")
        print(f"  flat_units: {len(decomposition['flat'])}")
        print(f"  max_depth_reached: {decomposition['max_depth_reached']}")
        for unit in decomposition["root_units"][:10]:
            print(f"  - [{unit['id']}] T{unit['tier']} {unit['instruction']}")
    print("next_steps:")
    for step in data["next_steps"]:
        print(f"  - {step}")


def _print_dag(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return

    print(f"method: {data['method']}")
    print(f"root_units: {len(data['root_units'])}")
    print(f"flat_units: {len(data['flat'])}")
    print(f"word_count_original: {data['word_count_original']}")
    print(f"word_count_decomposed: {data['word_count_decomposed']}")
    print(f"max_depth_reached: {data['max_depth_reached']}")
    print("units:")
    for unit in data["root_units"]:
        _print_unit(unit, indent=2)


def _print_unit(unit: dict, indent: int) -> None:
    prefix = " " * indent
    deps = f" depends_on={unit['depends_on']}" if unit["depends_on"] else ""
    applies = f" applies_to={unit['applies_to']}" if unit["applies_to"] else ""
    print(f"{prefix}- [{unit['id']}] T{unit['tier']} {unit['instruction']}{deps}{applies}")
    for constraint in unit["constraints"]:
        print(f"{prefix}  constraint: {constraint['kind']}({constraint['condition']}, {constraint['target']})")
    for child in unit["children"]:
        _print_unit(child, indent + 2)


def _print_memory_payload(data, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    if isinstance(data, dict):
        for key, value in data.items():
            print(f"{key}: {value}")
        return
    for item in data:
        if isinstance(item, dict) and "text" in item:
            print(f"[{item['source']}] score={item['score']}")
            print(item["text"])
            print()
        else:
            print(item)


def _print_reasoning(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return

    summary = summarize_session(ReasoningSession.from_dict(data))
    print(f"status: {summary['status']}")
    print(f"session_id: {summary['session_id']}")
    print(f"tier: {summary['tier']}")
    print(f"preflight_verdict: {summary['verdict']}")
    print(f"step_count: {summary['step_count']}")
    print(f"next_thought_needed: {summary['next_thought_needed']}")
    if summary["branches"]:
        print("branches:")
        for branch in summary["branches"]:
            print(f"  - {branch}")
    if summary["warnings"]:
        print("warnings:")
        for warning in summary["warnings"]:
            print(f"  - {warning}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
