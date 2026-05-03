#!/usr/bin/env bash
set -euo pipefail

TASK="delete production credentials"
SESSION_ID="demo-$(date +%s)"
DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Rulence Cross-Agent Demo ==="
echo "Task: $TASK"
echo

echo "Installing starter policies..."
rulence policy install secrets aws
echo

echo "--- Verdict from Rulence preflight ---"
rulence preflight "$TASK" --json || true
echo

echo "--- Generating trace ---"
rulence start "$TASK" --session-id "$SESSION_ID" --json >/dev/null
rulence trace "$SESSION_ID" --html > "$DEMO_DIR/trace.html"
echo "Trace: $DEMO_DIR/trace.html"
