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
# This is a prompt-reduction convenience for the agent's own CLI calls,
# not a sandbox. It does not attempt full shell parsing.

first_line="${cmd%%$'\n'*}"

case "$first_line" in
    *$'\r'* | *";"* | *"&"* | *"|"* | *'$('* | *'`'*)
        exit 0
        ;;
esac

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
    # Claude Code sets CLAUDE_PLUGIN_ROOT and Cursor sets CURSOR_PLUGIN_ROOT.
    # Read both. Checking only the Claude variable would send every Cursor
    # invocation down the weaker suffix fallback below.
    plugin_root="${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-}}"
    if [[ -n "$plugin_root" ]]; then
        # Production: exact same-file match against THIS plugin's own CLI,
        # by inode (`-ef`). A planted copy at any other path is a different
        # file, so it is rejected. That holds even when its path string
        # contains `/plugins/harness/skills/task-list-runner/`.
        if [[ "$script" -ef "${plugin_root}/skills/task-list-runner/task_list_cli.py" ]]; then
            approve
        fi
    else
        # Neither host variable is set: a direct or test invocation, where
        # the file may not exist on disk. These patterns are SUFFIX matches,
        # not prefix anchors. A planted copy would match if its path ends in
        # one of these shapes. That is why this branch is the fallback and
        # never the production path.
        #   - dev:       /...checkout.../plugins/harness/skills/task-list-runner/task_list_cli.py
        #   - cursor:    /.../.cursor/plugins/local/harness/skills/task-list-runner/task_list_cli.py
        #   - installed: /...cache/wild-horses/harness/<version>/skills/task-list-runner/task_list_cli.py
        if [[ "$script" == *"/plugins/harness/skills/task-list-runner/task_list_cli.py" \
           || "$script" == *"/.cursor/plugins/local/harness/skills/task-list-runner/task_list_cli.py" \
           || "$script" == *"/cache/wild-horses/harness/"*"/skills/task-list-runner/task_list_cli.py" ]]; then
            approve
        fi
    fi
fi
