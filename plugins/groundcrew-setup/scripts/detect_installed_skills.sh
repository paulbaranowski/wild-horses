#!/usr/bin/env bash
# detect_installed_skills.sh — detect whether the `superpowers` and `babysit-pr`
# Claude Code skills are installed, emitting a small JSON object on stdout.
#
# Detection strategy (two-tier):
#   PRIMARY: parse ~/.claude/plugins/installed_plugins.json
#     - superpowers  → key "superpowers@claude-plugins-official" present in "plugins"
#     - babysit-pr   → key "core@clipboard" present in "plugins"
#       (the `core` plugin from the `clipboard` source ships the babysit-pr skill)
#   FALLBACK (used when JSON file is missing or malformed):
#     - superpowers  → ~/.claude/plugins/cache/*/superpowers/*/skills/using-superpowers/SKILL.md
#     - babysit-pr   → ~/.claude/plugins/cache/*/core/*/skills/babysit-pr/SKILL.md
#
# Output: {"superpowers": <bool>, "babysitPr": <bool>}  (exit 0 always)

set -euo pipefail

python3 - "$HOME" <<'PYEOF'
from __future__ import annotations

import glob
import json
import os
import sys

home = sys.argv[1]
plugins_dir = os.path.join(home, ".claude", "plugins")
json_path = os.path.join(plugins_dir, "installed_plugins.json")


def check_via_json(path: str) -> tuple[bool, bool] | None:
    """
    Parse installed_plugins.json and return (superpowers, babysit_pr).
    Returns None if the file is missing or malformed.
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


def check_via_glob(base: str) -> tuple[bool, bool]:
    """
    Fall back to glob scanning the plugin cache directory.
    """
    cache_root = os.path.join(base, ".claude", "plugins", "cache")

    sp_pattern = os.path.join(
        cache_root, "*", "superpowers", "*", "skills", "using-superpowers", "SKILL.md"
    )
    babysit_pattern = os.path.join(
        cache_root, "*", "core", "*", "skills", "babysit-pr", "SKILL.md"
    )

    superpowers = len(glob.glob(sp_pattern)) > 0
    babysit_pr = len(glob.glob(babysit_pattern)) > 0
    return (superpowers, babysit_pr)


result = check_via_json(json_path)
if result is None:
    result = check_via_glob(home)

superpowers, babysit_pr = result
print(json.dumps({"superpowers": superpowers, "babysitPr": babysit_pr}))
PYEOF
