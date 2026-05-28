#!/usr/bin/env bash
# Discover an existing groundcrew config file.
#
# Searches (in order, first match wins):
#   1. ${PWD}/groundcrew.config.{ts,js,json,yaml,yml}
#   2. ${XDG_CONFIG_HOME:-$HOME/.config}/groundcrew/config.{ts,js,json,yaml,yml}
#
# Prints the absolute path of the first match to stdout, or empty on no match.
# Exit code is always 0; the caller treats empty stdout as "none found".

set -euo pipefail

# Try to find a path canonicalization tool
if command -v realpath >/dev/null 2>&1; then
    canonicalize() { realpath "$1"; }
elif command -v readlink >/dev/null 2>&1; then
    canonicalize() { readlink -f "$1"; }
else
    # Fallback: print the path as-found (no canonicalization available)
    canonicalize() { echo "$1"; }
fi

# Search locations with extensions in priority order
extensions=("ts" "js" "json" "yaml" "yml")

# 1. Check ${PWD}/groundcrew.config.{ts,js,json,yaml,yml}
for ext in "${extensions[@]}"; do
    file="${PWD}/groundcrew.config.${ext}"
    if [[ -f "$file" ]]; then
        canonicalize "$file"
        exit 0
    fi
done

# 2. Check ${XDG_CONFIG_HOME:-$HOME/.config}/groundcrew/config.{ts,js,json,yaml,yml}
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
for ext in "${extensions[@]}"; do
    file="${config_home}/groundcrew/config.${ext}"
    if [[ -f "$file" ]]; then
        canonicalize "$file"
        exit 0
    fi
done

# No match found; exit 0 with empty stdout
exit 0
