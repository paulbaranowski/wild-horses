#!/usr/bin/env bash
# Shell-adapter markInProgress hook. Reads {"path": "..."} JSON from
# stdin and flips that plan's Status frontmatter to in-progress.
#
# Wired into crew.config.ts as:
#   { kind: "shell", commands: { markInProgress: "/path/to/markInProgress.sh" } }
set -euo pipefail
exec python3 "$(dirname "$0")/../scripts/plan_keeper_cli.py" groundcrew-mark-in-progress
