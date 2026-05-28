#!/usr/bin/env bash
# Load an existing groundcrew config and emit it as JSON.
#
# Wraps a node ESM dynamic import of @clipboard-health/groundcrew's loadConfig().
# Cosmiconfig (groundcrew's loader) walks CWD upward, so this script cd's
# into the config file's directory before invoking node.
#
# Usage:
#   load_existing.sh /path/to/groundcrew.config.ts
#
# Exits 0 on success (stdout = JSON); non-zero on any failure (stderr = reason).
# Known failure modes (all surface as non-zero + stderr message):
#   - `node` not on PATH
#   - `@clipboard-health/groundcrew` not npm-installed (any install location)
#   - Config file throws on parse (groundcrew rejects malformed input)
#
# Documented limitation: users who only have a source clone of groundcrew
# (no built dist/) will hit "not installed" failure. The caller (the wizard)
# treats this as "no seeding available" and falls back to static defaults.

set -uo pipefail

# ── Argument validation ────────────────────────────────────────────────────────
if [[ $# -ne 1 || -z "${1:-}" ]]; then
    echo "ERROR: missing required argument: path to groundcrew config file" >&2
    echo "Usage: $(basename "$0") /path/to/groundcrew.config.ts" >&2
    exit 2
fi

config_path="$1"

# ── node availability check ───────────────────────────────────────────────────
if ! command -v node >/dev/null 2>&1; then
    echo "could not load existing config: node not found on PATH" >&2
    exit 1
fi

# ── cd into config file's directory ──────────────────────────────────────────
# Cosmiconfig walks CWD upward to locate the config; we must be in the right dir.
config_dir="$(dirname "$config_path")"
if ! cd "$config_dir" 2>/dev/null; then
    echo "could not load existing config: directory not found: ${config_dir}" >&2
    exit 1
fi

# ── invoke node with ESM dynamic import ──────────────────────────────────────
# groundcrew is "type": "module"; require() would fail. ESM dynamic import is required.
# loadConfig() takes no arguments (verified by groundcrew source).
node_stderr_tmp=$(mktemp)
trap 'rm -f "$node_stderr_tmp"' EXIT

node_output=$(
    node --input-type=module -e \
        "import('@clipboard-health/groundcrew').then(m => m.loadConfig()).then(c => process.stdout.write(JSON.stringify(c))).catch(e => { console.error(e.message); process.exit(1); })" \
        2>"$node_stderr_tmp"
)
node_exit=$?

if [[ $node_exit -ne 0 ]]; then
    cat "$node_stderr_tmp" >&2
    echo "could not load existing config" >&2
    exit "$node_exit"
fi

# On success, emit JSON to stdout
printf '%s' "$node_output"
