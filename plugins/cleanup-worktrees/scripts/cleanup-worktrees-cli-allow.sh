#!/bin/bash
# PreToolUse hook: pre-approve `python3 .../cleanup_worktrees_cli.py ...`
# invocations so the cleanup-worktrees skill flow doesn't gate on the auto-mode
# classifier multiple times per turn. The CLI's surface is bounded: it
# reads/writes a config file under ~/.config/wild-horses/cleanup-worktrees/ and
# runs git/gh/du commands (worktree list, status, rev-list, worktree remove,
# branch -D, gh pr list, du) against configured worktree paths. No subprocess
# execution of file-supplied content.
#
# Outputs PreToolUse permissionDecision JSON on match. Silent no-op otherwise
# (falls through to normal allow-list + classifier flow).

set -euo pipefail

command -v jq >/dev/null 2>&1 || exit 0

cmd=$(jq -r '.tool_input.command // empty')

# Match: `python3` immediately followed by the cleanup-worktrees CLI as its
# first positional argument, possibly wrapped in single or double quotes. The
# path must end in `/scripts/cleanup_worktrees_cli.py` AND sit under one of the
# two legitimate layouts (dev checkout or install cache — see the final test
# below). Approval runs whatever Python file lives at the path, so a bare
# `/cleanup-worktrees/` substring match would hand auto-approval to any
# attacker-planted `…/cleanup-worktrees/scripts/cleanup_worktrees_cli.py` (e.g.
# under /tmp or a malicious clone). Anchoring on the full plugin-specific path
# structure is the repo convention (CLAUDE.md "Hook design").
#
# Anchoring on `^python3<space>` + first-token-is-the-script (not anywhere in
# the command) prevents over-approval of unusual invocations like
# `python3 -c "evil; ..." /some/cleanup-worktrees/scripts/cleanup_worktrees_cli.py`
# which happen to *contain* both required substrings — the `-c` payload would
# run before the script path is consumed.
#
# Works for both layouts:
#   - dev:       /...checkout.../plugins/cleanup-worktrees/scripts/cleanup_worktrees_cli.py
#   - installed: /...cache/wild-horses/cleanup-worktrees/<version>/scripts/cleanup_worktrees_cli.py
#
# Handles Claude Code's defensive path-quoting (paths may be wrapped in `"` or
# `'`) via the optional `[\"\']?` tokens flanking the script path.
#
# Defense in depth: reject any shell control operators (`;`, `&&`, `||`, `|`,
# redirects, command substitution, backticks) up front, before allow-matching.
# `\n`/`\r` are listed first because the allow regex's `.` does not match
# newlines — without an explicit reject, a payload like
# `python3 .../cleanup_worktrees_cli.py\nuname -a` would bypass the metachar
# checks (newline isn't `;` or `&&`) and the regex's `.*$` clause can't see past
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
approve() {
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"cleanup-worktrees CLI is plugin-approved"}}'
}

if [[ "$cmd" =~ ^python3[[:space:]]+[\"\']?([^\"\'[:space:]]+/scripts/cleanup_worktrees_cli\.py)[\"\']?([[:space:]].*)?$ ]]; then
    script="${BASH_REMATCH[1]}"
    # Assumes Claude Code EXPORTS CLAUDE_PLUGIN_ROOT into the hook's environment
    # (not merely expanding it in the command string). If that ever stops being
    # true, this branch is skipped and the weaker suffix fallback below applies.
    if [[ -n "${CLAUDE_PLUGIN_ROOT:-}" ]]; then
        # Production: exact same-file match against THIS plugin's own CLI, by
        # inode (`-ef`), so a planted copy at any other path — even one whose
        # path string contains `/plugins/cleanup-worktrees/scripts/` — is a
        # different file and is rejected. Airtight; this is the real hook path
        # (hooks.json always sets CLAUDE_PLUGIN_ROOT).
        if [[ "$script" -ef "${CLAUDE_PLUGIN_ROOT}/scripts/cleanup_worktrees_cli.py" ]]; then
            approve
        fi
    else
        # No plugin-root env (direct / test invocation, where the file may not
        # even exist on disk): fall back to the two known layout shapes.
        #   - dev:       /...checkout.../plugins/cleanup-worktrees/scripts/cleanup_worktrees_cli.py
        #   - installed: /...cache/wild-horses/cleanup-worktrees/<version>/scripts/cleanup_worktrees_cli.py
        if [[ "$script" == *"/plugins/cleanup-worktrees/scripts/cleanup_worktrees_cli.py" \
           || "$script" == *"/cache/wild-horses/cleanup-worktrees/"*"/scripts/cleanup_worktrees_cli.py" ]]; then
            approve
        fi
    fi
fi
