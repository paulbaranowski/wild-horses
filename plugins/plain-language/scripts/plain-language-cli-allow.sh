#!/bin/bash
# PreToolUse hook: pre-approve `python3 .../plain_language_cli.py ...`
# invocations so the plain-language apply loop doesn't gate on the auto-mode
# classifier once per pass. The CLI's surface is bounded and read-only.
# It reads the target files. It also runs `git rev-parse --show-toplevel`
# and `git show --end-of-options <ref>:<path>` to fetch baselines for
# `verify`. It executes no file-supplied content.
#
# The CLI passes --end-of-options so a `--ref` value beginning with "-"
# cannot reach `git show` as an option. Without that flag, a crafted ref
# made git write a file. This hook approves the whole argument surface with
# no user prompt, so keep the flag if you touch _git_baseline.
#
# Outputs a PreToolUse allow decision on match. The dialect matches whichever
# harness is running: Claude Code, Cursor, or Grok Build. Silent no-op
# otherwise (falls through to the normal allow-list + classifier flow).

set -euo pipefail

command -v jq >/dev/null 2>&1 || exit 0

source "$(dirname "${BASH_SOURCE[0]}")/hook_runtime.sh"

input=$(cat)
hook_runtime_init "$input"
cmd="$HOOK_COMMAND"

# Match: `python3` immediately followed by the plain-language CLI as its first
# positional argument, possibly wrapped in single or double quotes. The path
# must end in `/scripts/plain_language_cli.py`. It must also sit under one
# of the legitimate layouts: dev checkout, Cursor local, or install cache.
# The final test below checks that.
#
# Approval runs whatever Python file lives at the path. A bare
# `/plain-language/` substring match would therefore auto-approve any
# attacker-planted copy. One under /tmp would qualify, as would one in a
# malicious clone. Anchoring on the full plugin-specific path structure is
# the repo convention (CLAUDE.md "Hook design").
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
# Defense in depth: reject any shell control operator before allow-matching.
# That covers `;`, `&` (including a bare backgrounding `&`), `|`, redirects,
# command substitution, and backticks.
#
# A single `&` matters. `python3 <approved-cli> scan & curl evil` backgrounds
# the approved command and chains a second one. The allow regex's trailing
# `.*$` would otherwise let it ride through. So `*"&"*`, which also subsumes
# `&&`, must be rejected.
#
# `\n` and `\r` are listed first because the allow regex's `.` does not match
# newlines. Consider a payload like
# `python3 .../plain_language_cli.py\nuname -a`. Without an explicit reject
# it bypasses the metachar checks, because a newline is neither `;` nor `&`.
# The regex's `.*$` clause cannot see past the newline either, so the
# chained command would be approved silently.
case "$cmd" in
    *$'\n'* | *$'\r'* | *";"* | *"&"* | *"|"* | *">"* | *"<"* | *'$('* | *'`'*)
        exit 0
        ;;
esac

approve() {
    hook_runtime_emit_allow "plain-language CLI is plugin-approved"
}

# Extract the script path from one of three forms. A legitimate plugin path
# containing spaces is only possible when quoted, and these still capture it.
# A double-quoted or single-quoted path may hold spaces. A bare path may
# not, because a space would start the next argument.
#
# Each pattern anchors on both ends. The trailing `([[:space:]].*)?$` forces
# the regex to consume the entire command string. Trailing exotic content
# then cannot ride along after a matching prefix. A stray newline that
# slipped past the case prefilter is one example.
script=""
if [[ "$cmd" =~ ^python3[[:space:]]+\"([^\"]+/scripts/plain_language_cli\.py)\"([[:space:]].*)?$ ]]; then
    script="${BASH_REMATCH[1]}"
elif [[ "$cmd" =~ ^python3[[:space:]]+\'([^\']+/scripts/plain_language_cli\.py)\'([[:space:]].*)?$ ]]; then
    script="${BASH_REMATCH[1]}"
elif [[ "$cmd" =~ ^python3[[:space:]]+([^\"\'[:space:]]+/scripts/plain_language_cli\.py)([[:space:]].*)?$ ]]; then
    script="${BASH_REMATCH[1]}"
fi

if [[ -n "$script" ]]; then
    # Claude Code sets CLAUDE_PLUGIN_ROOT and Cursor sets CURSOR_PLUGIN_ROOT.
    # Read both. Checking only the Claude variable would send every Cursor
    # invocation down the weaker suffix fallback below. That fallback is a
    # shipped configuration (hooks/cursor-hooks.json), not a test-only path.
    plugin_root="${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-}}"
    if [[ -n "$plugin_root" ]]; then
        # Production: exact same-file match against THIS plugin's own CLI,
        # by inode (`-ef`). A planted copy at any other path is a different
        # file, so it is rejected. That holds even when its path string
        # contains `/plugins/plain-language/scripts/`. This is the real hook
        # path under both hosts.
        if [[ "$script" -ef "${plugin_root}/scripts/plain_language_cli.py" ]]; then
            approve
        fi
    else
        # Neither host variable is set: a direct or test invocation, where
        # the file may not exist on disk. These patterns are SUFFIX matches,
        # not prefix anchors. A planted copy would match if its path ends in
        # one of these shapes. That is why this branch is the fallback and
        # never the production path.
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
