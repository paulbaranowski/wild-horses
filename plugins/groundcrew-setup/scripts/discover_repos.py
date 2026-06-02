#!/usr/bin/env python3
"""Discover known repositories from two sources and merge them.

  1. GitHub via the `gh` CLI (best-effort; failures are silently skipped).
  2. Local directory scan of common clone roots (~/code, ~/projects,
     ~/src, ~/dev, ~/work) plus an optional --workspace-dir <path>.

Merges the two source lists and emits a JSON array on stdout, sorted
alphabetically by owner/repo.  Each entry has the shape:
    {"owner": "<owner>", "repo": "<repo>", "sources": ["gh"|"local", ...]}

Exit 0 on success. "gh failed", "scan dir missing", "no .git found" are
NOT errors — they simply contribute nothing.

Usage:
    discover_repos.py [--workspace-dir <path>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_SCAN_ROOTS = ("code", "projects", "src", "dev", "work")

# When walking a scan root, we want to find repos nested up to 3 levels deep:
# scan/repo, scan/a/repo, scan/a/b/repo, scan/a/b/c/repo. A repo is detected
# when we see ".git" as a directory entry at that depth.
MAX_REPO_DEPTH = 3

PRUNE_DIR_NAMES = frozenset({
    "node_modules",
    ".venv",
    ".tox",
    "vendor",
    "target",
    "dist",
    "build",
})


def gh_repos() -> list[dict]:
    """Return entries from `gh repo list`, or an empty list on any failure."""
    try:
        result = subprocess.run(
            ["gh", "repo", "list", "--json", "nameWithOwner", "--limit", "100"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def find_git_configs(scan_dir: Path) -> list[Path]:
    """Walk scan_dir up to MAX_REPO_DEPTH levels and collect each repo's .git/config."""
    results: list[Path] = []
    scan_parts = len(scan_dir.parts)
    try:
        walker = os.walk(str(scan_dir))
    except OSError:
        return results
    for root, dirs, _ in walker:
        root_path = Path(root)
        relative_depth = len(root_path.parts) - scan_parts
        # Prune unwanted dir names from further descent.
        dirs[:] = [d for d in dirs if d not in PRUNE_DIR_NAMES]
        if relative_depth > MAX_REPO_DEPTH:
            dirs[:] = []
            continue
        if ".git" in dirs:
            cfg = root_path / ".git" / "config"
            if cfg.is_file():
                results.append(cfg)
            # Don't descend into the .git directory itself.
            dirs.remove(".git")
    return results


_SSH_RE = re.compile(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$")
_HTTPS_RE = re.compile(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$")


def extract_owner_repo(config_path: Path) -> str | None:
    """Read a .git/config file and return the origin remote's owner/repo, or None."""
    try:
        content = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    in_origin = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_origin = stripped.startswith('[remote "origin"]')
            continue
        if in_origin and stripped.startswith("url"):
            m = re.match(r"url\s*=\s*(.+)", stripped)
            if not m:
                continue
            url = m.group(1).strip()
            ssh = _SSH_RE.match(url)
            if ssh:
                return f"{ssh.group(1)}/{ssh.group(2)}"
            https = _HTTPS_RE.match(url)
            if https:
                return f"{https.group(1)}/{https.group(2)}"
            return None
    return None


def canonicalize(path: Path) -> Path:
    """Best-effort absolute, canonical path (resolve symlinks). Falls back to as-given."""
    try:
        return path.resolve(strict=False)
    except OSError:
        return path


def collect_scan_dirs(home: Path, workspace_dir: str | None) -> list[Path]:
    """Build the ordered list of directories to scan."""
    dirs: list[Path] = [home / name for name in DEFAULT_SCAN_ROOTS]
    if workspace_dir:
        candidate = Path(workspace_dir)
        if candidate.is_dir():
            candidate = canonicalize(candidate)
        # Append if not already in list (compare canonical forms).
        canonical_existing = {canonicalize(d) for d in dirs}
        if candidate not in canonical_existing:
            dirs.append(candidate)
    return dirs


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Discover repos via gh + local scan; emit a sorted JSON array.",
    )
    parser.add_argument(
        "--workspace-dir",
        default=None,
        help="Additional directory to include in the local scan.",
    )
    args = parser.parse_args(argv)

    home = Path(os.environ.get("HOME") or os.path.expanduser("~"))
    merged: dict[str, set[str]] = {}

    for entry in gh_repos():
        name_with_owner = entry.get("nameWithOwner", "")
        if isinstance(name_with_owner, str) and "/" in name_with_owner:
            merged.setdefault(name_with_owner, set()).add("gh")

    for scan_dir in collect_scan_dirs(home, args.workspace_dir):
        if not scan_dir.is_dir():
            continue
        for cfg in find_git_configs(scan_dir):
            owner_repo = extract_owner_repo(cfg)
            if owner_repo:
                merged.setdefault(owner_repo, set()).add("local")

    result: list[dict] = []
    for key in sorted(merged.keys()):
        owner, repo = key.split("/", 1)
        sources: list[str] = []
        if "gh" in merged[key]:
            sources.append("gh")
        if "local" in merged[key]:
            sources.append("local")
        result.append({"owner": owner, "repo": repo, "sources": sources})

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
