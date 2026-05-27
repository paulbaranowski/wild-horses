#!/bin/bash
# PreToolUse hook: pre-approve `python3 .../update_repos_cli.py ...` invocations
# so the update-git-repos skill flow doesn't gate on the auto-mode classifier
# multiple times per turn. The CLI's surface is bounded: it reads/writes a
# config file under ~/.config/wild-horses/update-git-repos/ and runs git
# commands (status, branch, fetch, pull, stash) against configured repo paths.
# No subprocess execution of file-supplied content.
#
# Outputs PreToolUse permissionDecision JSON on match. Silent no-op otherwise
# (falls through to normal allow-list + classifier flow).

set -euo pipefail

command -v jq >/dev/null 2>&1 || exit 0

cmd=$(jq -r '.tool_input.command // empty')

# Match: `python3` immediately followed by the update-git-repos CLI as its first
# positional argument, possibly wrapped in single or double quotes. The path
# must end in `/scripts/update_repos_cli.py` AND contain `/update-git-repos/`
# somewhere — anchoring on the plugin dir prevents over-approval of a stray
# `update_repos_cli.py` elsewhere in the workspace.
#
# Anchoring on `^python3<space>` + first-token-is-the-script (not anywhere in
# the command) prevents over-approval of unusual invocations like
# `python3 -c "evil; ..." /some/update-git-repos/scripts/update_repos_cli.py`
# which happen to *contain* both required substrings — the `-c` payload would
# run before the script path is consumed.
#
# Works for both layouts:
#   - dev:       /...checkout.../plugins/update-git-repos/scripts/update_repos_cli.py
#   - installed: /...cache/wild-horses/update-git-repos/<version>/scripts/update_repos_cli.py
#
# Handles Claude Code's defensive path-quoting (paths may be wrapped in `"` or
# `'`) via the optional `[\"\']?` tokens flanking the script path.
if [[ "$cmd" =~ ^python3[[:space:]]+[\"\']?([^\"\'[:space:]]+/scripts/update_repos_cli\.py)[\"\']?([[:space:]]|$) ]] \
   && [[ "${BASH_REMATCH[1]}" == *"/update-git-repos/"* ]]; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"update-git-repos CLI is plugin-approved"}}'
fi
