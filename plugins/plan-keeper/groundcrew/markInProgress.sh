#!/usr/bin/env bash
# Shell-adapter markInProgress hook. Reads {"path": "..."} JSON from
# stdin and flips that plan's Status frontmatter to in-progress.
#
# Wired into crew.config.ts as:
#   { kind: "shell", commands: { markInProgress: "/path/to/markInProgress.sh" } }
#
# CLI path resolution: honors $PLAN_KEEPER_CLI (absolute path to
# plan_keeper_cli.py) when set. Otherwise falls back to the relative path
# inside this plugin tree. Set the env var when copying this script
# outside the plugin tree.
set -euo pipefail
CLI="${PLAN_KEEPER_CLI:-$(dirname "$0")/../scripts/plan_keeper_cli.py}"
exec python3 "$CLI" crew start
