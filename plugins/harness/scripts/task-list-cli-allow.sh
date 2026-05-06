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
# `/skills/task-list-runner/task_list_cli.py` as a literal substring.
# That suffix is tighter than just `/task_list_cli.py` (a stray script
# elsewhere on the filesystem won't match) and is the common substring
# between both layouts:
#   - dev:       /...checkout.../plugins/harness/skills/task-list-runner/task_list_cli.py
#   - installed: /...cache/wild-horses/harness/<version>/skills/task-list-runner/task_list_cli.py
# (The `harness` segment is NOT adjacent to `skills` in the installed
# path — a version directory sits between them — so we anchor on the
# `skills/task-list-runner/` prefix instead.) Two-clause check (instead
# of one regex with end-anchor) handles Claude Code's defensive
# path-quoting.
if [[ "$cmd" =~ ^python3[[:space:]] ]] && [[ "$cmd" == *"/skills/task-list-runner/task_list_cli.py"* ]]; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"task-list-runner CLI is plugin-approved"}}'
fi
