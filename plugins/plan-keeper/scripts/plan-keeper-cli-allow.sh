#!/bin/bash
# PreToolUse hook: pre-approve `python3 .../plan_keeper_cli.py ...` invocations
# so each plan-* skill flow doesn't gate on the auto-mode classifier multiple
# times per turn. The CLI's surface is bounded: it reads/lists/writes files
# under ~/plans/ and runs `git remote get-url origin` for repo derivation.
# No subprocess execution of file-supplied content (unlike task_list_cli's
# `verify` subcommand) — surface is purely I/O + naming + mutation.
#
# Outputs PreToolUse permissionDecision JSON on match. Silent no-op otherwise
# (falls through to normal allow-list + classifier flow).

set -euo pipefail

command -v jq >/dev/null 2>&1 || exit 0

cmd=$(jq -r '.tool_input.command // empty')

# Match: command starts with `python3 ` (with whitespace), AND contains BOTH
# `/plan-keeper/` and `/scripts/plan_keeper_cli.py` as literal substrings.
# Two-clause check (instead of one regex):
#   - Pins the plugin (`/plan-keeper/`) so a stray `plan_keeper_cli.py`
#     elsewhere on the filesystem won't match
#   - Anchors the script path (`/scripts/plan_keeper_cli.py`)
# Works for both layouts:
#   - dev:       /...checkout.../plugins/plan-keeper/scripts/plan_keeper_cli.py
#   - installed: /...cache/wild-horses/plan-keeper/<version>/scripts/plan_keeper_cli.py
# (The version directory sits between `plan-keeper` and `scripts` in the
# installed path but doesn't disrupt either substring.) Handles Claude
# Code's defensive path-quoting because we use substring checks, not anchored
# regex matches.
if [[ "$cmd" =~ ^python3[[:space:]] ]] \
   && [[ "$cmd" == *"/plan-keeper/"* ]] \
   && [[ "$cmd" == *"/scripts/plan_keeper_cli.py"* ]]; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"plan-keeper CLI is plugin-approved"}}'
fi
