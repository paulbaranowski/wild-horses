#!/usr/bin/env bash
# Shell-adapter fetch hook for groundcrew. Emits a JSON array of active
# plans across all ~/plans/<repo>/ directories.
#
# Wired into crew.config.ts as:
#   { kind: "shell", commands: { fetch: "/path/to/fetch.sh" } }
set -euo pipefail
exec python3 "$(dirname "$0")/../scripts/plan_keeper_cli.py" groundcrew-fetch
