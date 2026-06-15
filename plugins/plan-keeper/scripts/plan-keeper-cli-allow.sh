#!/bin/bash
# PreToolUse hook: pre-approve the plan-keeper CLIs
# (`python3 .../scripts/plan_keeper_cli.py ...` and
# `python3 .../scripts/refresh_worktree_cli.py ...`) so each plan-* skill flow
# doesn't gate on the auto-mode classifier multiple times per turn. Both
# surfaces are bounded:
#   - plan_keeper_cli.py reads/lists/writes files under ~/plans/ and runs
#     `git remote get-url origin` for repo derivation.
#   - refresh_worktree_cli.py only fetches one base ref and `git merge --ff-only`s
#     the current worktree onto it (a pure fast-forward, never a merge/rebase),
#     gated on the tree being clean and not ahead of base.
# Neither executes file-supplied content (unlike task_list_cli's `verify`
# subcommand) — surfaces are purely I/O + naming + mutation + a fixed-argv
# fast-forward.
#
# Outputs PreToolUse permissionDecision JSON on match. Silent no-op otherwise
# (falls through to normal allow-list + classifier flow).

set -euo pipefail

command -v jq >/dev/null 2>&1 || exit 0

cmd=$(jq -r '.tool_input.command // empty')

# Match: `python3` immediately followed by a plan-keeper CLI as its first
# positional argument, possibly wrapped in single or double quotes. The path
# must end in `/scripts/plan_keeper_cli.py` or `/scripts/refresh_worktree_cli.py`
# AND contain `/plan-keeper/` somewhere.
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
#
# Implementation: a single regex captures the path token in group 1, then a
# plain substring check on that captured value verifies `/plan-keeper/` is
# present. Splitting the check (instead of a single regex like
# `.../plan-keeper/[^...]*/scripts/...`) avoids relying on bash ERE's
# backtracking into a middle `*` segment, which doesn't fire reliably on
# macOS libc when the surrounding literals are adjacent (e.g., the dev path
# `/plan-keeper/scripts/...` with no intermediate dir).
#
# Handles Claude Code's defensive path-quoting (paths may be wrapped in `"` or
# `'`) via the optional `[\"\']?` tokens flanking the script path. Path
# interiors exclude quote and whitespace characters, so quoted/unquoted forms
# can't blur into each other.
if [[ "$cmd" =~ ^python3[[:space:]]+[\"\']?([^\"\'[:space:]]+/scripts/(plan_keeper_cli|refresh_worktree_cli)\.py)[\"\']?([[:space:]]|$) ]] \
   && [[ "${BASH_REMATCH[1]}" == *"/plan-keeper/"* ]]; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"plan-keeper CLI is plugin-approved"}}'
fi
