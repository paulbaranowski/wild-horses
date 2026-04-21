#!/bin/bash
# PostToolUse hook: run Prettier + markdownlint-cli2 on any .md file edited by
# Claude. Silently no-op when deps are missing so the hook is harmless until
# /harness:hooks installs them.

command -v jq >/dev/null 2>&1 && command -v prettier >/dev/null 2>&1 && command -v markdownlint-cli2 >/dev/null 2>&1 || exit 0

JSON=$(cat)
FILE_PATH=$(echo "$JSON" | jq -r '.tool_input.file_path // empty')

if [[ -z "$FILE_PATH" || ! "$FILE_PATH" =~ \.md$ ]]; then
  exit 0
fi

echo "🔧 Running Markdown combo fix on: $FILE_PATH"
prettier --write "$FILE_PATH" 2>/dev/null || true
markdownlint-cli2 --fix "$FILE_PATH" 2>/dev/null || true
echo "✅ Markdown combo fix completed for $FILE_PATH"
