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

input=$(cat)
cmd=$(echo "$input" | jq -r '.tool_input.command // empty')

# Match: `python3` immediately followed by the update-git-repos CLI as its
# first positional argument, possibly wrapped in single or double quotes. The
# path must end in `/scripts/update_repos_cli.py` AND contain
# `/update-git-repos/` somewhere — anchoring on the plugin dir prevents
# over-approval of a stray `update_repos_cli.py` elsewhere in the workspace.
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
#
# Defense in depth: reject any shell control operators (`;`, `&&`, `||`, `|`,
# redirects, command substitution, backticks) up front, before allow-matching.
# `\n`/`\r` are listed first because the allow regex's `.` does not match
# newlines — without an explicit reject, a payload like
# `python3 .../update_repos_cli.py\nuname -a` would bypass the metachar checks
# (newline isn't `;` or `&&`) and the regex's `.*$` clause can't see past
# the newline either, leaving the chained command silently approved.
case "$cmd" in
    *$'\n'* | *$'\r'* | *";"* | *"&&"* | *"||"* | *"|"* | *">"* | *"<"* | *'$('* | *'`'*)
        exit 0
        ;;
esac

# Anchor on both ends: `.*$` (instead of `[[:space:]]|$`) forces the regex to
# consume the entire command string, so trailing exotic content (a stray
# newline that somehow slipped past the case prefilter, etc.) can't ride
# along after a matching prefix.
if [[ "$cmd" =~ ^python3[[:space:]]+[\"\']?([^\"\'[:space:]]+/scripts/update_repos_cli\.py)[\"\']?([[:space:]].*)?$ ]] \
   && [[ "${BASH_REMATCH[1]}" == *"/update-git-repos/"* ]]; then
    hook_event=$(echo "$input" | jq -r '.hook_event_name // empty')
    if [[ "$hook_event" == "preToolUse" ]]; then
        printf '%s\n' '{"permission":"allow","agent_message":"update-git-repos CLI is plugin-approved"}'
    else
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"update-git-repos CLI is plugin-approved"}}'
    fi
fi
