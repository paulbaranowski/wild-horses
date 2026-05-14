#!/usr/bin/env bash
# PreToolUse allow-list for codepaths_cli.py invocations.
# Anchors on plugin-specific path structure so a stray codepaths_cli.py
# elsewhere in the workspace doesn't get auto-approved.
#
# Matches both the dev-checkout path and the installed plugin-cache path.
set -euo pipefail

# Read JSON event from stdin
EVENT=$(cat)

# Extract the command string
CMD=$(printf '%s' "$EVENT" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("tool_input", {}).get("command", ""))
except Exception:
    print("")
')

# Match: python3 .../plugins/codepath-visualizer/skills/codepath-mapper/codepaths_cli.py ...
# Or:    python3 .../claude-plugins-official/codepath-visualizer/<ver>/skills/codepath-mapper/codepaths_cli.py ...
if [[ "$CMD" =~ python3[[:space:]]+([^[:space:]]+/)?(plugins/codepath-visualizer|codepath-visualizer/[0-9]+\.[0-9]+\.[0-9]+)/skills/codepath-mapper/codepaths_cli\.py([[:space:]]|$) ]]; then
  printf '{"decision":"approve","reason":"codepaths_cli.py (allow-listed)"}\n'
  exit 0
fi

# Not our CLI — pass through (no decision)
exit 0
