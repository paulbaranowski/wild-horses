#!/bin/bash
# PostToolUse hook: run pyright on any .py file edited by Claude. Non-blocking
# (always exits 0) — surfaces type issues at edit time without derailing the
# agent. Complements /pyright:run-and-fix, which is for bulk work.
# Silently no-ops when deps are missing.

command -v jq >/dev/null 2>&1 && command -v pyright >/dev/null 2>&1 || exit 0

JSON=$(cat)
FILE_PATH=$(echo "$JSON" | jq -r '.tool_input.file_path // empty')

if [[ -z "$FILE_PATH" || ! "$FILE_PATH" =~ \.py$ ]]; then
  exit 0
fi

echo "🔍 pyright check on: $FILE_PATH" >&2
pyright "$FILE_PATH" >&2 || true
exit 0
