#!/usr/bin/env bash
# Shell-adapter resolveOne hook. Receives ${id} as $1, emits one JSON
# issue or exits 3 if not found.
#
# Wired into crew.config.ts as:
#   { kind: "shell", commands: { resolveOne: "/path/to/resolveOne.sh ${id}" } }
#
# CLI path resolution: honors $PLAN_KEEPER_CLI (absolute path to
# plan_keeper_cli.py) when set. Otherwise falls back to the relative path
# inside this plugin tree. Set the env var when copying this script
# outside the plugin tree.
set -euo pipefail
CLI="${PLAN_KEEPER_CLI:-$(dirname "$0")/../scripts/plan_keeper_cli.py}"
exec python3 "$CLI" crew get "$1"
