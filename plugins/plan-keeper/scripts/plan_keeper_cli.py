#!/usr/bin/env python3
"""Entry point for the plan-keeper CLI. Implementation lives in plan_keeper/.

The filename is load-bearing: the PreToolUse allow-hook
(plan-keeper-cli-allow.sh) anchors on `python3 .../scripts/plan_keeper_cli.py`
as the first command token, and every skill SKILL.md invokes the CLI by this
path. Keep it a thin shim — the package holds the logic.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plan_keeper.cli import main

if __name__ == "__main__":
    sys.exit(main())
