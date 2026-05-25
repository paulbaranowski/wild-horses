#!/usr/bin/env bash
# Shell-adapter resolveOne hook. Receives ${id} as $1, emits one JSON
# issue or exits 3 if not found.
#
# Wired into crew.config.ts as:
#   { kind: "shell", commands: { resolveOne: "/path/to/resolveOne.sh ${id}" } }
set -euo pipefail
exec python3 "$(dirname "$0")/../scripts/plan_keeper_cli.py" groundcrew-resolve-one "$1"
