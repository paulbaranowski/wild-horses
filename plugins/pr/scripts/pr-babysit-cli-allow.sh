#!/bin/bash
# PreToolUse hook: pre-approve `python3 .../pr_babysit_cli.py ...` invocations so
# the auto-mode classifier doesn't gate them on every pass of a pr-babysit loop.
# The CLI's surface is GitHub read/write via `gh` (review data, failed-check
# logs, threaded replies, PR comments) plus an explicit-file `git commit && push`
# — no arbitrary code execution, no shell interpolation of untrusted input.
#
# Disable this hook to restore per-call permission interception for the CLI
# (and accept many more prompts per pr-babysit run).
#
# Outputs a PreToolUse permission decision on match. Silent no-op otherwise
# (falls through to the normal allow-list + classifier flow).

set -euo pipefail

command -v jq >/dev/null 2>&1 || exit 0

input=$(cat)
cmd=$(echo "$input" | jq -r '.tool_input.command // empty')

# Match: command starts with `python3 ` (with whitespace), AND contains
# `/scripts/pr_babysit_cli.py` as a literal substring. That suffix is distinctive
# enough that a stray script elsewhere won't match, and it is common to both
# layouts:
#   - dev:       /...checkout.../plugins/pr/scripts/pr_babysit_cli.py
#   - installed: /...cache/wild-horses/pr/<version>/scripts/pr_babysit_cli.py
# (The `pr` segment is NOT adjacent to `scripts` in the installed path — a
# version directory sits between them — so we anchor on `/scripts/` instead.)
# Two-clause check (instead of one end-anchored regex) handles Claude Code's
# defensive path-quoting.
if [[ "$cmd" =~ ^python3[[:space:]] ]] && [[ "$cmd" == *"/scripts/pr_babysit_cli.py"* ]]; then
    hook_event=$(echo "$input" | jq -r '.hook_event_name // empty')
    if [[ "$hook_event" == "preToolUse" ]]; then
        printf '%s\n' '{"permission":"allow","agent_message":"pr-babysit CLI is plugin-approved"}'
    else
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"pr-babysit CLI is plugin-approved"}}'
    fi
fi
