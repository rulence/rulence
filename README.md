# Rulence

Value in Consistency.

**Portable governance for AI agents.**

One local policy layer for Claude Code, Cursor, n8n, and every MCP-compatible
agent.

> Sequential Thinking is the protocol your agent uses to think.
> Rulence is the protocol your team uses to govern every agent.

The MVP gives an agent one first call before it acts:

```bash
rulence preflight "migrate the database this week"
```

It classifies task difficulty, loads the matching policy, estimates governed
context budget, runs logic/risk checks, and returns an inspectable verdict. Then
`rulence think` can keep a governed reasoning trace for the task.

## Demo

Same task, three runners, one verdict:

```text
delete production credentials
```

| Agent | Verdict | Artifact |
| --- | --- | --- |
| Claude Code | block | `examples/demo/claude-code-block.md` |
| Cursor | block | `examples/demo/cursor-block.md` |
| n8n | block | `examples/demo/n8n-block.md` |

Run the local demo script after installing the package:

```bash
examples/demo/run-demo.sh
```

The recording script is in `examples/demo/video-script.md`.

## What works in this MVP

- `rulence classify`: deterministic task tier classification.
- `rulence decompose`: deterministic prompt breakdown into a typed task DAG.
- `rulence preflight`: policy-driven checks before an agent proceeds.
- `rulence think`: governed sequential reasoning with revision and branching.
- `rulence init`: writes editable TOML policies to `~/.rulence/policies`.
- `rulence policy install`: installs bundled starter policies for secrets, git,
  migrations, AWS, and payments.
- `rulence policy test`: runs local policy regression cases.
- `rulence install`: installs Rulence into Claude Code, Cursor, or n8n.
- `rulence feedback`: records false positives/negatives into local JSONL.
- `rulence memory`: queries local file memory, Honcho over REST, or MemPalace
  over native MCP stdio.
- `rulence memory health`: checks provider reachability without crashing when
  a backend is not installed.
- `rulence-mcp`: local stdio MCP server exposing `rulence_classify`,
  `rulence_decompose`, and `rulence_preflight`, plus memory health/structure
  tools, `rulence_start_thinking`, `rulence_think`, and `rulence_trace`.
- `sequentialthinking`: drop-in MCP tool name/schema compatible with the
  Sequential Thinking server, with Rulence governance metadata added.
- npm MCP launcher runs the Python MCP server as the only source of truth.
- No cloud calls, no model dependency, no database, no telemetry.

## Install locally

From this folder:

```bash
python3 -m pip install -e .
npm install
```

For zero-install testing:

```bash
PYTHONPATH=src python3 -m rulence classify "debug this failing migration"
PYTHONPATH=src python3 -m rulence decompose "Build API. Then build UI." --json
PYTHONPATH=src python3 -m rulence preflight "delete production credentials"
PYTHONPATH=src python3 -m rulence think "plan a migration" --thought "Risk: rollback is missing, so verify backup first."
PYTHONPATH=src python3 -m rulence memory "database rollback" --provider local --path examples/memory.md
PYTHONPATH=src python3 -m rulence memory health --provider local --path examples/memory.md
PYTHONPATH=src python3 -m rulence policy test examples/policy-cases.toml
```

Install in 60 seconds after publishing:

```bash
pip install rulence
rulence policy install secrets aws migrations
rulence install claude-code
```

## Commands

Classify a task:

```bash
rulence classify "plan an agent architecture change" --json
```

Decompose a larger prompt:

```bash
rulence decompose "Build the API. Then build the UI. Test everything." --json
rulence decompose ./spec.md --max-depth 3 --json
cat ./spec.md | rulence decompose - --json
```

Run preflight:

```bash
rulence preflight "migrate the database this week" \
  --memory "Release freeze is active this week. Backups run nightly." \
  --json
```

Run preflight with local memory retrieval:

```bash
rulence preflight "plan database migration" \
  --memory-provider local \
  --memory-path examples/memory.md \
  --model gpt-4o \
  --json
```

Run governed sequential reasoning:

```bash
PYTHONPATH=src python3 -m rulence start "compare two architecture options" \
  --session-id demo \
  --json

rulence think "compare two architecture options" \
  --thought "Assume option A is faster because it avoids network calls." \
  --thought-number 1 \
  --total-thoughts 3 \
  --next-thought-needed \
  --session-file /tmp/rulence-session.json

rulence think "compare two architecture options" \
  --thought "Revise thought 1: option B may be safer because it isolates failures." \
  --thought-number 2 \
  --total-thoughts 3 \
  --revision-of 1 \
  --session-file /tmp/rulence-session.json
```

Create local policy files:

```bash
rulence init
```

Install starter policies:

```bash
rulence policy list-available
rulence policy install secrets git migrations aws payments
```

If multiple policy files exist for the same tier, Rulence merges them. Checks,
warnings, blocks, and constraints are unioned so installed starter policies
compose instead of shadowing each other.

Policy files are TOML:

```toml
tier = 4
label = "complex"
required_checks = ["memory_check", "consistency_check", "context_budget", "tool_preflight", "counterexample_search", "constraint_solver", "decomposition_check"]
warn_if = ["memory_missing", "budget_high", "counterexample_found", "contradictions_found"]
block_if = ["destructive_with_weak_context"]
decompose_threshold = 1500
decompose_max_depth = 3
```

Run policy regression cases:

```bash
rulence policy test examples/policy-cases.toml
```

Render a self-contained trace:

```bash
rulence trace demo --html > trace.html
```

Record a false positive/negative for later tuning:

```bash
rulence feedback "plan database migration" \
  --verdict warn \
  --outcome false_positive \
  --notes "memory had enough rollback context"

rulence feedback --summary
rulence feedback --list --limit 10
```

Memory health and discovery:

```bash
rulence memory health --provider all --json
rulence memory health --provider mempalace --json
rulence memory wings --json
rulence memory rooms --wing osow --json
```

Memory config lives at `~/.rulence/memory.toml`:

```toml
[mempalace]
command = "mempalace-mcp"
args = []
env = { MEMPALACE_DB = "~/.mempalace/db" }
timeout_seconds = 5.0
default_limit = 5

[honcho]
url = "http://127.0.0.1:8787"
api_key_env = "HONCHO_API_KEY"
timeout_seconds = 5.0
retries = 2

[priority]
order = ["mempalace", "honcho", "local"]
```

MemPalace is MCP-native. Honcho is REST-native. Rulence uses the native
transport for each backend and merges results behind the same memory interface.

Start MCP server:

```bash
npm start
```

Local npm binary:

```bash
npx --no-install rulence-mcp
```

The npm MCP package bundles the Python source and delegates all decisions to it.
Users still need Python 3.11+ available as `python3`, `PYTHON`, or
`RULENCE_PYTHON`. The npm binary starts one long-lived Python MCP process; it
does not cold-start Python for every MCP tool call.

## Tier model

- Tier 0 / direct: no tool use or policy overhead.
- Tier 1 / trivial: answer from current context or memory.
- Tier 2 / mild: one lookup or one memory/consistency check.
- Tier 3 / moderate: multi-step work with budget and tool preflight.
- Tier 4 / complex: migrations, research, debugging, architecture, synthesis.
- Tier 5 / high-risk: credentials, destructive actions, sensitive data,
  payments, legal/medical/tax, or production changes.

## MCP tool config example

See [examples/mcp-config.json](examples/mcp-config.json).

Local npm package config:

```json
{
  "mcpServers": {
    "rulence": {
      "command": "node",
      "args": [
        "/Users/aumordecade/Desktop/Rulence MVP/src-js/mcp-server.js"
      ]
    }
  }
}
```

Docker:

```bash
docker build -t rulence/mcp:local .
docker run --rm -i rulence/mcp:local
```

Future published npm config:

```json
{
  "mcpServers": {
    "rulence": {
      "command": "npx",
      "args": ["-y", "@rulence/mcp"]
    }
  }
}
```

One-line runner installers:

```bash
rulence install claude-code
rulence install cursor --dir .
rulence install n8n
```

`claude-code` installs an executable PreToolUse gate. `cursor` writes a project
rule, and `n8n` prints MCP server config to paste into n8n; those runners enforce
Rulence when their agent/workflow calls the configured rule or MCP tool before
acting.

## How this replaces Sequential Thinking

Sequential Thinking gives an agent a structured scratchpad. Rulence includes
that, but adds what the scratchpad lacks:

- Tier classification before reasoning starts.
- Local policy selection per tier.
- Typed prompt decomposition into governable task units.
- Approve/warn/block verdicts.
- Memory-aware preflight.
- Governed context estimates with optional `tiktoken` support when installed.
- Risk and destructive-action gates.
- Explicit constraint conflict checks using local `requires: A -> B` and
  `forbids: A -> B` rules.
- Quality checks on each reasoning step.
- Revision and branch validation.
- A trace that can travel across agents.

For compatibility, the MCP server exposes the original-style
`sequentialthinking` tool with these fields:

- `thought`
- `nextThoughtNeeded`
- `thoughtNumber`
- `totalThoughts`
- `isRevision`
- `revisesThought`
- `branchFromThought`
- `branchId`
- `needsMoreThoughts`

The response keeps the expected core shape:

- `thoughtNumber`
- `totalThoughts`
- `nextThoughtNeeded`
- `branches`
- `thoughtHistoryLength`

Rulence adds a `rulence` object containing tier, preflight verdict, warnings,
step checks, and whether action is blocked.

## Constraint Checks

Rulence supports a small local constraint syntax for rules that need strict
conflict detection:

```text
requires: deploy -> rollback
forbids: deploy -> rollback

requires(migration, backup)
forbids(migration, backup)
```

The Decomposer also recognizes conservative natural-language constraints:

```text
Auth must use JWT
Don't log credentials
Never auth with JWT
```

The `constraint_solver` check blocks only when the same condition and target are
both required and forbidden. Broad prose contradictions remain warnings because
they are heuristic.

## Development checks

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
npm test
npm run check
PYTHONPATH=src python3 -m rulence doctor
PYTHONPATH=src python3 -m rulence classify "prove this claim is true" --json
PYTHONPATH=src python3 -m rulence preflight "delete production credentials" --json
PYTHONPATH=src python3 -m rulence think "plan a migration" --thought "Risk: rollback is missing, so verify backup first." --json
```

Session persistence:

```bash
PYTHONPATH=src python3 -m rulence think "compare two plans" \
  --session-id demo \
  --thought "Assume plan A is faster because it avoids network calls." \
  --next-thought-needed

PYTHONPATH=src python3 -m rulence trace demo
```

Policy validation:

```bash
PYTHONPATH=src python3 -m rulence init
PYTHONPATH=src python3 -m rulence policy validate
PYTHONPATH=src python3 -m rulence policy list
PYTHONPATH=src python3 -m rulence policy test examples/policy-cases.toml
```

Memory adapters:

```bash
PYTHONPATH=src python3 -m rulence memory "rollback migration" \
  --provider local \
  --path examples/memory.md

PYTHONPATH=src python3 -m rulence memory "user preference" \
  --provider honcho \
  --url http://127.0.0.1:8787

PYTHONPATH=src python3 -m rulence memory "project decision" \
  --provider mempalace

PYTHONPATH=src python3 -m rulence memory health --provider mempalace --json
```

Optional local classifier fallback:

```bash
RULENCE_CLASSIFIER_MODEL=gemma3:1b \
PYTHONPATH=src python3 -m rulence classify "do it" --json
```

When `RULENCE_CLASSIFIER_MODEL` is set, Rulence asks local Ollama only for
ambiguous tasks or longer novel tasks that matched no known risk/tool terms.
Without it, classification stays deterministic and offline.

Release files are included but publishing is intentionally manual:

- `.github/workflows/publish-npm.yml`
- `.github/workflows/publish-pypi.yml`
- `.github/workflows/publish-ghcr.yml`

Publishing requires configuring `NPM_TOKEN`, `PYPI_API_TOKEN`, and GitHub
package permissions.

## Honest limitations

This is not full formal verification yet. The MVP has a small explicit
constraint checker and deterministic heuristics, but not Z3/Prolog theorem
proving. Context estimates are estimates, not token-savings benchmarks. Later
versions should add measured Sequential Thinking comparisons, richer local-model
routing, and solver-backed policy checks.
