#!/usr/bin/env python3
"""Load an existing groundcrew config and emit it as JSON.

Wraps a node ESM dynamic import of @clipboard-health/groundcrew's loadConfig().
Cosmiconfig (groundcrew's loader) walks CWD upward, so this script runs node
with `cwd` set to the config file's directory.

Usage:
    load_existing.py /path/to/groundcrew.config.ts

Exits 0 on success (stdout = JSON); non-zero on any failure (stderr = reason).
Known failure modes (all surface as non-zero + stderr message):
    - `node` not on PATH
    - `@clipboard-health/groundcrew` not npm-installed (any install location)
    - Config file throws on parse (groundcrew rejects malformed input)

Documented limitation: users who only have a source clone of groundcrew
(no built dist/) will hit "not installed" failure. The caller (the wizard)
treats this as "no seeding available" and falls back to static defaults.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

NODE_SCRIPT = (
    "import('@clipboard-health/groundcrew')"
    ".then(m => m.loadConfig())"
    ".then(c => process.stdout.write(JSON.stringify(c)))"
    ".catch(e => { console.error(e.message); process.exit(1); })"
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Load a groundcrew config via the package's loadConfig() and emit JSON on stdout.",
    )
    parser.add_argument("config_path", help="Path to a groundcrew config file (.ts/.js/.json/.yaml).")
    args = parser.parse_args(argv)

    if shutil.which("node") is None:
        print("could not load existing config: node not found on PATH", file=sys.stderr)
        return 1

    config_dir = Path(args.config_path).parent
    if not config_dir.is_dir():
        print(f"could not load existing config: directory not found: {config_dir}", file=sys.stderr)
        return 1

    try:
        result = subprocess.run(
            ["node", "--input-type=module", "-e", NODE_SCRIPT],
            cwd=str(config_dir),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("could not load existing config: node invocation timed out after 30s", file=sys.stderr)
        return 1

    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
            if not result.stderr.endswith("\n"):
                sys.stderr.write("\n")
        print("could not load existing config", file=sys.stderr)
        return result.returncode

    sys.stdout.write(result.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
