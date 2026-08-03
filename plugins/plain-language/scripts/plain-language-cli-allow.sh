#!/bin/bash
# PreToolUse hook: pre-approve `python3 .../plain_language_cli.py ...`
# invocations so the plain-language apply loop doesn't gate on the auto-mode
# classifier once per pass. The CLI's surface is bounded and read-only: it
# reads the target files, and runs `git rev-parse --show-toplevel` plus
# `git show <ref>:<path>` to fetch baselines for `verify`. It writes no file
# and executes no file-supplied content.
#
# Outputs PreToolUse permissionDecision JSON on match. Silent no-op otherwise
# (falls through to normal allow-list + classifier flow).

set -euo pipefail

command -v jq >/dev/null 2>&1 || exit 0

input=$(cat)
cmd=$(echo "$input" | jq -r '.tool_input.command // empty')

# Match: `python3` immediately followed by the plain-language CLI as its first
# positional argument, possibly wrapped in single or double quotes. The path
# must end in `/scripts/plain_language_cli.py` AND sit under one of the
# legitimate layouts (dev checkout, Cursor local, or install cache: see the
# final test below). Approval runs whatever Python file lives at the path, so
# a bare `/plain-language/` substring match would hand auto-approval to any
# attacker-planted `.../plain-language/scripts/plain_language_cli.py` (for
# example under /tmp or a malicious clone). Anchoring on the full
# plugin-specific path structure is the repo convention (CLAUDE.md "Hook
# design").
#
# Anchoring on `^python3<space>` + first-token-is-the-script (not anywhere in
# the command) prevents over-approval of unusual invocations like
# `python3 -c "evil; ..." /some/plain-language/scripts/plain_language_cli.py`
# which happen to *contain* both required substrings. The `-c` payload would
# run before the script path is consumed.
#
# Handles Claude Code's defensive path-quoting (paths may be wrapped in `"` or
# `'`) via the quoted alternatives below.
#
# Defense in depth: reject any shell control operators (`;`, `&` including a
# bare backgrounding `&`, `|`, redirects, command substitution, backticks) up
# front, before allow-matching. A single `&` matters: `python3 <approved-cli>
# scan & curl evil` backgrounds the approved command and chains a second one,
# and the allow regex's trailing `.*$` would otherwise let it ride through. So
# `*"&"*` (which also subsumes `&&`) must be rejected, not just `&&`.
# `\n`/`\r` are listed first because the allow regex's `.` does not match
# newlines. Without an explicit reject, a payload like
# `python3 .../plain_language_cli.py\nuname -a` would bypass the metachar
# checks (newline is neither `;` nor `&`), and the regex's `.*$` clause cannot
# see past the newline either, leaving the chained command silently approved.
case "$cmd" in
    *$'\n'* | *$'\r'* | *";"* | *"&"* | *"|"* | *">"* | *"<"* | *'$('* | *'`'*)
        exit 0
        ;;
esac

approve() {
    hook_event=$(echo "$input" | jq -r '.hook_event_name // empty')
    if [[ "$hook_event" == "preToolUse" ]]; then
        printf '%s\n' '{"permission":"allow","agent_message":"plain-language CLI is plugin-approved"}'
    else
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"plain-language CLI is plugin-approved"}}'
    fi
}

# Extract the script path from one of three forms, so a legitimate plugin path
# containing spaces (only possible when quoted) is still captured:
#   "..."  double-quoted  -> spaces allowed inside
#   '...'  single-quoted  -> spaces allowed inside
#   bare                  -> no spaces (a space would start the next argument)
#
# Each pattern anchors on both ends: the trailing `([[:space:]].*)?$` forces
# the regex to consume the entire command string, so trailing exotic content
# (a stray newline that slipped past the case prefilter, for example) cannot
# ride along after a matching prefix.
script=""
if [[ "$cmd" =~ ^python3[[:space:]]+\"([^\"]+/scripts/plain_language_cli\.py)\"([[:space:]].*)?$ ]]; then
    script="${BASH_REMATCH[1]}"
elif [[ "$cmd" =~ ^python3[[:space:]]+\'([^\']+/scripts/plain_language_cli\.py)\'([[:space:]].*)?$ ]]; then
    script="${BASH_REMATCH[1]}"
elif [[ "$cmd" =~ ^python3[[:space:]]+([^\"\'[:space:]]+/scripts/plain_language_cli\.py)([[:space:]].*)?$ ]]; then
    script="${BASH_REMATCH[1]}"
fi

if [[ -n "$script" ]]; then
    # Assumes Claude Code EXPORTS CLAUDE_PLUGIN_ROOT into the hook's environment
    # (not merely expanding it in the command string). If that ever stops being
    # true, this branch is skipped and the weaker suffix fallback below applies.
    if [[ -n "${CLAUDE_PLUGIN_ROOT:-}" ]]; then
        # Production: exact same-file match against THIS plugin's own CLI, by
        # inode (`-ef`), so a planted copy at any other path (even one whose
        # path string contains `/plugins/plain-language/scripts/`) is a
        # different file and is rejected. Airtight; this is the real hook path
        # (hooks.json always sets CLAUDE_PLUGIN_ROOT).
        if [[ "$script" -ef "${CLAUDE_PLUGIN_ROOT}/scripts/plain_language_cli.py" ]]; then
            approve
        fi
    else
        # No plugin-root env (direct / test invocation, where the file may not
        # even exist on disk): fall back to known layout shapes. Each pattern
        # anchors on a plugin-specific prefix so planted copies under /tmp or
        # arbitrary clones cannot ride the substring.
        #   - dev:       /...checkout.../plugins/plain-language/scripts/plain_language_cli.py
        #   - cursor:    /.../.cursor/plugins/local/plain-language/scripts/plain_language_cli.py
        #   - installed: /...cache/wild-horses/plain-language/<version>/scripts/plain_language_cli.py
        if [[ "$script" == *"/plugins/plain-language/scripts/plain_language_cli.py" \
           || "$script" == *"/.cursor/plugins/local/plain-language/scripts/plain_language_cli.py" \
           || "$script" == *"/cache/wild-horses/plain-language/"*"/scripts/plain_language_cli.py" ]]; then
            approve
        fi
    fi
fi
