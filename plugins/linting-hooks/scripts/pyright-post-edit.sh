#!/bin/bash
# PostToolUse hook: run pyright on any .py file edited by Claude. Non-blocking
# (always exits 0) — surfaces type issues at edit time without derailing the
# agent. Complements /pyright:run-and-fix, which is for bulk work.
# Silently no-ops when deps are missing.
#
# uv-aware: when the edited file lives under a uv project (a uv.lock above it),
# runs `uv run --no-sync pyright` from that project root so imports resolve
# against the project's .venv. Without this, global pyright reports a flood of
# false-positive "could not be resolved" diagnostics for project dependencies.

command -v jq >/dev/null 2>&1 && command -v pyright >/dev/null 2>&1 || exit 0

JSON=$(cat)
FILE_PATH=$(echo "$JSON" | jq -r '.tool_input.file_path // empty')

if [[ -z "$FILE_PATH" || ! "$FILE_PATH" =~ \.py$ ]]; then
  exit 0
fi

find_uv_root() {
  local dir
  dir=$(dirname "$1")
  while [[ "$dir" != "/" && -n "$dir" ]]; do
    if [[ -f "$dir/uv.lock" ]]; then
      printf '%s' "$dir"
      return 0
    fi
    dir=$(dirname "$dir")
  done
  return 1
}

echo "🔍 pyright check on: $FILE_PATH" >&2

if command -v uv >/dev/null 2>&1 && UV_ROOT=$(find_uv_root "$FILE_PATH"); then
  ( cd "$UV_ROOT" && uv run --no-sync pyright "$FILE_PATH" ) >&2 || true
else
  pyright "$FILE_PATH" >&2 || true
fi

exit 0
