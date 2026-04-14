#!/bin/bash
# PostToolUse hook: validate plugin structure after editing skill/command files.
# Runs `claude plugin validate .` only when a file inside plugins/ is modified.
# Exit 2 = block the edit and surface the error to Claude.

set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.file // empty')

# Only validate when the edited file is inside a plugin directory
if [[ -z "$FILE_PATH" ]] || ! [[ "$FILE_PATH" =~ (^|/)plugins/ ]]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR" || exit 1

OUTPUT=$(claude plugin validate . 2>&1) || {
  echo "Plugin validation failed after editing: $FILE_PATH" >&2
  echo "$OUTPUT" >&2
  exit 2
}
