#!/bin/bash
# PreToolUse hook: pre-approve `python3 .../task_list_cli.py ...` invocations
# so the auto-mode classifier doesn't gate them on every iteration of a
# task-list-runner loop. The CLI's surface includes:
#   - JSON read/write on one file under docs/exec-plans/active/ via atomic
#     tmp+os.replace, schema-validated on every call
#   - subprocess execution of that file's verifySteps (the `verify` subcommand)
#
# Trust for verifySteps content is delegated to the upstream task-list-builder
# that produced the file — there is no in-loop user-vetting moment. Disable
# this hook to restore per-call permission interception for `verify`
# (and accept many more prompts per task-list-runner run).
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

# Approve only a first-argument invocation of the form
# `python3 <path>/skills/task-list-runner/task_list_cli.py ...`.
#
# `python3` is the executable. `python3 -c` / `python3 -m` would run
# arbitrary code with the CLI path riding along in a comment or string,
# so those forms are rejected: the script must be the first positional
# argument, not a later token.
#
# Approval runs whatever Python file lives at the path. A bare
# `/skills/task-list-runner/` substring match would therefore auto-approve
# any attacker-planted copy. One under /tmp would qualify. Anchoring on
# the full plugin-specific path structure is the repo convention
# (CLAUDE.md "Hook design").
#
# Works for both layouts:
#   - dev:       /...checkout.../plugins/harness/skills/task-list-runner/task_list_cli.py
#   - installed: /...cache/wild-horses/harness/<version>/skills/task-list-runner/task_list_cli.py
# (The `harness` segment is NOT adjacent to `skills` in the installed
# path — a version directory sits between them.)
#
# Only the first physical line is metacharacter-checked. Mutation bodies
# arrive as a heredoc on later lines (`--log-file - <<'EOF'`) and
# legitimately contain markdown (backticks, pipes, semicolons) that must
# not disqualify the invocation. `<` and `>` stay allowed on the first
# line because a one-line `<<'EOF'` is a real call, not a chain.
#
# A second physical command after a newline is not a heredoc. Reject
# multiline input unless the first line opens a quoted heredoc and the
# matching terminator is the last non-empty line. An unquoted `<<EOF`
# expands substitutions in the body, so it is not accepted.
#
# This is a prompt-reduction convenience for the agent's own CLI calls,
# not a sandbox. It does not attempt full shell parsing.

first_line="${cmd%%$'\n'*}"

case "$first_line" in
    *$'\r'* | *";"* | *"&"* | *"|"* | *'$('* | *'`'*)
        exit 0
        ;;
esac

if [[ "$cmd" == *$'\n'* ]]; then
    sq_pat='<<'\''([A-Za-z_][A-Za-z0-9_]*)'\''[[:space:]]*$'
    dq_pat='<<"([A-Za-z_][A-Za-z0-9_]*)"[[:space:]]*$'
    delim=""
    if [[ "$first_line" =~ $sq_pat ]]; then
        delim="${BASH_REMATCH[1]}"
    elif [[ "$first_line" =~ $dq_pat ]]; then
        delim="${BASH_REMATCH[1]}"
    else
        exit 0
    fi
    rest="${cmd#*$'\n'}"
    found=0
    leftover=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ $found -eq 1 ]]; then
            if [[ -n "$line" ]]; then
                leftover=1
                break
            fi
            continue
        fi
        if [[ "$line" == "$delim" ]]; then
            found=1
        fi
    done <<< "$rest"
    if [[ $found -eq 0 || $leftover -eq 1 ]]; then
        exit 0
    fi
fi

approve() {
    hook_runtime_emit_allow "task-list-runner CLI is plugin-approved"
}

# Extract the script path from one of three forms. A legitimate plugin
# path containing spaces is only possible when quoted, and these still
# capture it. A double-quoted or single-quoted path may hold spaces. A
# bare path may not, because a space would start the next argument.
#
# Each pattern anchors on both ends of the first line. The trailing
# `([[:space:]].*)?$` forces the regex to consume that line. Trailing
# exotic content then cannot ride along after a matching prefix.
script=""
if [[ "$first_line" =~ ^python3[[:space:]]+\"([^\"]+/skills/task-list-runner/task_list_cli\.py)\"([[:space:]].*)?$ ]]; then
    script="${BASH_REMATCH[1]}"
elif [[ "$first_line" =~ ^python3[[:space:]]+\'([^\']+/skills/task-list-runner/task_list_cli\.py)\'([[:space:]].*)?$ ]]; then
    script="${BASH_REMATCH[1]}"
elif [[ "$first_line" =~ ^python3[[:space:]]+([^\"\'[:space:]]+/skills/task-list-runner/task_list_cli\.py)([[:space:]].*)?$ ]]; then
    script="${BASH_REMATCH[1]}"
fi

if [[ -n "$script" ]]; then
    # Trust this hook's own plugin tree. The allow-script lives in
    # scripts/; the CLI lives in skills/task-list-runner/. That holds
    # in the checkout, the Claude cache, and the Cursor local layout.
    # Claude Code sets CLAUDE_PLUGIN_ROOT and Cursor sets
    # CURSOR_PLUGIN_ROOT. Grok and direct runs may set neither, so
    # the script directory is the fallback root.
    #
    # Approval is always an inode match (`-ef`). A planted copy at
    # any other path is a different file, even when its path string
    # ends in a known layout suffix.
    derived_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    plugin_root="${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-$derived_root}}"
    if [[ "$script" -ef "${plugin_root}/skills/task-list-runner/task_list_cli.py" \
       || "$script" -ef "${derived_root}/skills/task-list-runner/task_list_cli.py" ]]; then
        approve
    fi
fi
