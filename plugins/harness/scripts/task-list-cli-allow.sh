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

# Match: command starts with `python3 ` (with whitespace), AND contains
# `/skills/task-list-runner/task_list_cli.py` as a literal substring.
# That suffix is tighter than just `/task_list_cli.py` (a stray script
# elsewhere on the filesystem won't match) and is the common substring
# between both layouts:
#   - dev:       /...checkout.../plugins/harness/skills/task-list-runner/task_list_cli.py
#   - installed: /...cache/wild-horses/harness/<version>/skills/task-list-runner/task_list_cli.py
# (The `harness` segment is NOT adjacent to `skills` in the installed
# path — a version directory sits between them — so we anchor on the
# `skills/task-list-runner/` prefix instead.) Two-clause check (instead
# of one regex with end-anchor) handles Claude Code's defensive
# path-quoting.
if [[ "$cmd" =~ ^python3[[:space:]] ]] && [[ "$cmd" == *"/skills/task-list-runner/task_list_cli.py"* ]]; then
    hook_runtime_emit_allow "task-list-runner CLI is plugin-approved"
fi
