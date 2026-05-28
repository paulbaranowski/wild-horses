#!/usr/bin/env bash
# discover_repos.sh — discover known repositories from two sources:
#   1. GitHub via the `gh` CLI (best-effort; failures are silently skipped).
#   2. Local directory scan of common clone roots (~/code, ~/projects,
#      ~/src, ~/dev, ~/work) plus an optional --workspace-dir <path>.
#
# Merges the two source lists and emits a JSON array on stdout, sorted
# alphabetically by owner/repo.  Each entry has the shape:
#   {"owner": "<owner>", "repo": "<repo>", "sources": ["gh"|"local", ...]}
#
# Exit 0 on success.  "gh failed", "scan dir missing", "no .git found" are
# NOT errors — they simply contribute nothing.
#
# Usage:
#   discover_repos.sh [--workspace-dir <path>]

set -uo pipefail

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
workspace_dir=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      if [[ $# -lt 2 ]]; then
        echo "Error: --workspace-dir requires a path argument" >&2
        exit 1
      fi
      workspace_dir="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $(basename "$0") [--workspace-dir <path>]"
      echo ""
      echo "Discovers GitHub repos (via gh CLI) and local git clones, merges"
      echo "them, and prints a JSON array on stdout."
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Source 1: GitHub via gh CLI (best-effort)
# ---------------------------------------------------------------------------
gh_json="$(gh repo list --json nameWithOwner --limit 100 2>/dev/null || echo '[]')"

# ---------------------------------------------------------------------------
# Source 2: Local clone scan
# ---------------------------------------------------------------------------
declare -a scan_dirs=(
  "$HOME/code"
  "$HOME/projects"
  "$HOME/src"
  "$HOME/dev"
  "$HOME/work"
)

# Normalize workspace_dir to an absolute, canonical path for reliable dedup.
if [[ -n "$workspace_dir" && -d "$workspace_dir" ]]; then
  workspace_dir="$(cd "$workspace_dir" 2>/dev/null && pwd -P || echo "$workspace_dir")"
fi

# Add --workspace-dir if provided and not already covered
if [[ -n "$workspace_dir" ]]; then
  already=false
  for d in "${scan_dirs[@]}"; do
    if [[ "$d" == "$workspace_dir" ]]; then
      already=true
      break
    fi
  done
  if [[ "$already" == "false" ]]; then
    scan_dirs+=("$workspace_dir")
  fi
fi

# Collect .git/config files from all scan dirs (repos nested up to 3 levels deep).
# find counts the scan root as depth 0. A repo's .git/config sits 2 levels below
# the repo dir, so a repo nested N levels deep has its config at find-depth N+2.
# To cover repos nested up to 3 levels deep (find-depth 5), we use -maxdepth 5.
# -prune skips excluded dirs entirely (never descends into them), which is more
# efficient than post-filtering with -not -path.
git_config_list=""
for d in "${scan_dirs[@]}"; do
  [[ -d "$d" ]] || continue
  while IFS= read -r -d '' cfg; do
    git_config_list+="${cfg}"$'\n'
  done < <(find "$d" -maxdepth 5 \
    \( -name node_modules -o -name .venv -o -name .tox -o -name vendor -o -name target -o -name dist -o -name build \) -prune \
    -o -name "config" -path "*/.git/config" -print0 \
    2>/dev/null)
done

# ---------------------------------------------------------------------------
# Merge via Python (handles JSON parsing, URL extraction, dedupe, sort)
# ---------------------------------------------------------------------------
python3 - "$gh_json" "$git_config_list" <<'PYEOF'
from __future__ import annotations

import json
import re
import sys

gh_json_str = sys.argv[1]
git_config_str = sys.argv[2]

# --- Parse GitHub source ---
try:
    gh_entries = json.loads(gh_json_str) if gh_json_str.strip() else []
    if not isinstance(gh_entries, list):
        gh_entries = []
except (json.JSONDecodeError, ValueError):
    gh_entries = []

# --- Parse local source ---
# Split the newline-delimited list of .git/config file paths
git_config_paths = [p for p in git_config_str.splitlines() if p.strip()]

def extract_owner_repo_from_git_config(config_path: str):
    """Read a .git/config file and extract the origin remote's owner/repo.

    Handles three URL forms:
      git@github.com:owner/repo.git
      https://github.com/owner/repo.git
      https://github.com/owner/repo

    Returns 'owner/repo' or None if parsing fails or not a GitHub remote.
    """
    try:
        with open(config_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return None

    # Find the [remote "origin"] section and capture the url= line.
    # A section ends when a new [section] header appears or EOF.
    in_origin = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_origin = stripped.startswith('[remote "origin"]')
            continue
        if in_origin and stripped.startswith("url"):
            # url = <value>
            m = re.match(r"url\s*=\s*(.+)", stripped)
            if not m:
                continue
            url = m.group(1).strip()
            # SSH: git@github.com:owner/repo.git
            ssh = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
            if ssh:
                return f"{ssh.group(1)}/{ssh.group(2)}"
            # HTTPS: https://github.com/owner/repo[.git]
            https = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
            if https:
                return f"{https.group(1)}/{https.group(2)}"
            # Non-GitHub remote: skip silently
            return None
    return None

# --- Build merged dict keyed by owner/repo ---
# Value is a set of sources.
merged: dict[str, set] = {}

for entry in gh_entries:
    name_with_owner = entry.get("nameWithOwner", "")
    if not name_with_owner or "/" not in name_with_owner:
        continue
    merged.setdefault(name_with_owner, set()).add("gh")

for cfg_path in git_config_paths:
    owner_repo = extract_owner_repo_from_git_config(cfg_path)
    if owner_repo:
        merged.setdefault(owner_repo, set()).add("local")

# --- Emit sorted JSON array ---
result = []
for key in sorted(merged.keys()):
    owner, repo = key.split("/", 1)
    sources = []
    if "gh" in merged[key]:
        sources.append("gh")
    if "local" in merged[key]:
        sources.append("local")
    result.append({"owner": owner, "repo": repo, "sources": sources})

print(json.dumps(result, indent=2))
PYEOF
