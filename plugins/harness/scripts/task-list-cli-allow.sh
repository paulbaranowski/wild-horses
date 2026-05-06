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

# Match: command starts with `python3 ` (with whitespace), AND contains
# `/task_list_cli.py` as a literal substring. The leading slash prevents
# accidental matches against a stray `task_list_cli.py` in CWD; the
# `python3 ` prefix prevents matches against e.g. `cat task_list_cli.py`.
# Two-clause check (instead of a single regex with end-anchor) handles
# quoted paths — Claude Code defensively quotes script paths, so the
# real command is `python3 "/path/task_list_cli.py" --file ...` and a
# regex requiring whitespace right after `.py` would miss it.
if [[ "$cmd" =~ ^python3[[:space:]] ]] && [[ "$cmd" == *"/task_list_cli.py"* ]]; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"task-list-runner CLI is plugin-approved"}}'
fi
