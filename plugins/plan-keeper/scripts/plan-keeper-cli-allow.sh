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

# Match: `python3` immediately followed by the plan-keeper CLI as its first
# positional argument, possibly wrapped in single or double quotes. The path
# must contain `/plan-keeper/` somewhere AND end in `/scripts/plan_keeper_cli.py`.
#
# Anchoring on `^python3<space>` + first-token-is-the-script (not anywhere in
# the command) prevents over-approval of unusual invocations like
# `python3 -c "evil; ..." /some/plan-keeper/scripts/plan_keeper_cli.py` which
# happen to *contain* both required substrings — the `-c` payload would run
# before the script path is consumed.
#
# Works for both layouts:
#   - dev:       /...checkout.../plugins/plan-keeper/scripts/plan_keeper_cli.py
#   - installed: /...cache/wild-horses/plan-keeper/<version>/scripts/plan_keeper_cli.py
# A version directory sits between `plan-keeper` and `scripts` in the installed
# path; the inner `[^"'[:space:]]*` segment accepts that interior gracefully.
#
# Handles Claude Code's defensive path-quoting (paths may be wrapped in `"` or
# `'`) via the optional `[\"\']?` tokens flanking the script path. Path
# interiors exclude quote and whitespace characters, so quoted/unquoted forms
# can't blur into each other.
if [[ "$cmd" =~ ^python3[[:space:]]+[\"\']?[^\"\'[:space:]]*/plan-keeper/[^\"\'[:space:]]*/scripts/plan_keeper_cli\.py[\"\']?([[:space:]]|$) ]]; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"plan-keeper CLI is plugin-approved"}}'
fi
