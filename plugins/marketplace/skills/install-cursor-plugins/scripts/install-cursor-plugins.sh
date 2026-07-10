#!/usr/bin/env bash
# Copy every plugin from a Cursor marketplace catalog into ~/.cursor/plugins/local
# as real files (never symlinks).
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install-cursor-plugins.sh <marketplace-root> [dest-root]

  marketplace-root  Directory that contains .cursor-plugin/marketplace.json
  dest-root         Defaults to ~/.cursor/plugins/local

Copies each catalog entry's source tree into <dest-root>/<plugin-name>/.
Replaces an existing destination (including a leftover symlink) with a real
file tree via rsync --delete. Does not create symlinks.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage >&2
  exit 2
fi

marketplace_root=$(cd "$1" && pwd)
dest_root=${2:-"$HOME/.cursor/plugins/local"}
mkdir -p "$dest_root"
dest_root=$(cd "$dest_root" && pwd)

manifest="$marketplace_root/.cursor-plugin/marketplace.json"
if [[ ! -f "$manifest" ]]; then
  echo "error: missing Cursor marketplace manifest: $manifest" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required" >&2
  exit 1
fi

plugin_root=$(jq -r '.metadata.pluginRoot // empty' "$manifest")
copied=0
skipped=0

while IFS=$'\t' read -r name source; do
  [[ -n "$name" && -n "$source" ]] || continue

  # Normalize source relative to marketplace root (optional pluginRoot prefix).
  source=${source#./}
  if [[ -n "$plugin_root" ]]; then
    plugin_root_norm=${plugin_root#./}
    plugin_root_norm=${plugin_root_norm%/}
    case "$source" in
      "$plugin_root_norm"/*) ;;
      *) source="$plugin_root_norm/$source" ;;
    esac
  fi

  src_dir="$marketplace_root/$source"
  if [[ ! -d "$src_dir" ]]; then
    echo "skip $name: source not found: $src_dir" >&2
    skipped=$((skipped + 1))
    continue
  fi

  if [[ ! -f "$src_dir/.cursor-plugin/plugin.json" ]]; then
    echo "skip $name: no .cursor-plugin/plugin.json under $src_dir" >&2
    skipped=$((skipped + 1))
    continue
  fi

  dest_dir="$dest_root/$name"
  if [[ -L "$dest_dir" ]]; then
    echo "replacing symlink $dest_dir with a real copy"
    rm "$dest_dir"
  fi
  mkdir -p "$dest_dir"

  # Trailing slashes: copy tree contents into dest_dir; --delete keeps dest in sync.
  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.DS_Store' \
    "$src_dir/" "$dest_dir/"

  if [[ -L "$dest_dir" ]]; then
    echo "error: destination is still a symlink after copy: $dest_dir" >&2
    exit 1
  fi

  echo "copied $name -> $dest_dir"
  copied=$((copied + 1))
done < <(jq -r '.plugins[] | [.name, (.source | if type=="string" then . else .path end)] | @tsv' "$manifest")

echo "done: copied=$copied skipped=$skipped dest=$dest_root"
if [[ "$copied" -eq 0 ]]; then
  exit 1
fi
