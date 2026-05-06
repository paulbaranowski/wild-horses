#!/bin/bash
# PreToolUse hook: pre-approve `python3 .../task_list_cli.py ...` invocations
# so the auto-mode classifier doesn't gate them on every iteration of a
# task-list-runner loop. The script being approved is bounded — it reads/writes
# one JSON file under docs/exec-plans/active/ via atomic tmp+os.replace and
# validates schema on each call. See task-list-runner/SKILL.md.
#
# Outputs PreToolUse permissionDecision JSON on match. Silent no-op otherwise
# (falls through to normal allow-list + classifier flow).

set -euo pipefail

command -v jq >/dev/null 2>&1 || exit 0

cmd=$(jq -r '.tool_input.command // empty')

# Match: starts with `python3 ` (with whitespace), then any chars, then
# `/task_list_cli.py` followed by whitespace or end-of-string. The leading
# slash on the script path prevents accidental matches against a stray file
# named `task_list_cli.py` in CWD.
if [[ "$cmd" =~ ^python3[[:space:]].*/task_list_cli\.py([[:space:]]|$) ]]; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"task-list-runner CLI is plugin-approved"}}'
fi
