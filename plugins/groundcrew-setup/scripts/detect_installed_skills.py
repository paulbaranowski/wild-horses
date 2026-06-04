#!/usr/bin/env python3
"""Detect whether the `superpowers` and `babysit-pr` Claude Code skills are installed.

Detection strategy (two-tier):
  PRIMARY: parse ~/.claude/plugins/installed_plugins.json
    - superpowers  → key "superpowers@claude-plugins-official" present in "plugins"
    - babysit-pr   → key "core@clipboard" present in "plugins"
      (the `core` plugin from the `clipboard` source ships the babysit-pr skill)
  FALLBACK (used when JSON file is missing or malformed):
    - superpowers  → ~/.claude/plugins/cache/*/superpowers/*/skills/using-superpowers/SKILL.md
    - babysit-pr   → ~/.claude/plugins/cache/*/core/*/skills/babysit-pr/SKILL.md

Output: {"superpowers": <bool>, "babysitPr": <bool>}  (exit 0 always)
"""

from __future__ import annotations

import glob
import json
import os
import sys


def check_via_json(path: str) -> tuple[bool, bool] | None:
    """Parse installed_plugins.json and return (superpowers, babysit_pr).

    Returns None if the file is missing, malformed, or has an unexpected shape.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    plugin_keys = data.get("plugins", {})
    if not isinstance(plugin_keys, dict):
        return None

    superpowers = "superpowers@claude-plugins-official" in plugin_keys
    babysit_pr = "core@clipboard" in plugin_keys
    return (superpowers, babysit_pr)


def check_via_glob(home: str) -> tuple[bool, bool]:
    """Fall back to glob scanning the plugin cache directory."""
    cache_root = os.path.join(home, ".claude", "plugins", "cache")
    sp_pattern = os.path.join(
        cache_root, "*", "superpowers", "*", "skills", "using-superpowers", "SKILL.md"
    )
    babysit_pattern = os.path.join(
        cache_root, "*", "core", "*", "skills", "babysit-pr", "SKILL.md"
    )
    return (len(glob.glob(sp_pattern)) > 0, len(glob.glob(babysit_pattern)) > 0)


def main() -> int:
    home = os.environ.get("HOME") or os.path.expanduser("~")
    json_path = os.path.join(home, ".claude", "plugins", "installed_plugins.json")

    result = check_via_json(json_path)
    if result is None:
        result = check_via_glob(home)

    superpowers, babysit_pr = result
    print(json.dumps({"superpowers": superpowers, "babysitPr": babysit_pr}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
