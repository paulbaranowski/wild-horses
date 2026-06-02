#!/usr/bin/env python3
"""Discover an existing groundcrew config file.

Searches (in order, first match wins):
  1. ${PWD}/groundcrew.config.{ts,js,json,yaml,yml}
  2. ${XDG_CONFIG_HOME:-$HOME/.config}/groundcrew/config.{ts,js,json,yaml,yml}

Prints the canonical absolute path of the first match to stdout, or empty
on no match. Exit code is always 0; the caller treats empty stdout as
"none found".
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

EXTENSIONS = ("ts", "js", "json", "yaml", "yml")


def find_existing_config(cwd: Path, config_home: Path) -> Path | None:
    for ext in EXTENSIONS:
        candidate = cwd / f"groundcrew.config.{ext}"
        if candidate.is_file():
            return candidate.resolve()
    for ext in EXTENSIONS:
        candidate = config_home / "groundcrew" / f"config.{ext}"
        if candidate.is_file():
            return candidate.resolve()
    return None


def main() -> int:
    cwd = Path.cwd()
    config_home = Path(
        os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    )
    match = find_existing_config(cwd, config_home)
    if match is not None:
        print(match)
    return 0


if __name__ == "__main__":
    sys.exit(main())
