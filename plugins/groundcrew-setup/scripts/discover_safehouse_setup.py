#!/usr/bin/env python3
"""Probe the eugene1g/agent-safehouse install state and emit JSON status.

Output fields:
    binaryAvailable      bool   — `safehouse` resolves on PATH.
    binaryPath           str|nl — absolute path from `which safehouse`, or null.
    brewFormulaInstalled bool   — `brew list agent-safehouse --formula` exits 0.
    envExported          bool   — SAFEHOUSE_APPEND_PROFILE exported in any rc file
                                  (commented lines excluded).
    sidecarPresent       bool   — `~/.config/agent-safehouse/env.sh` exists.
    sidecarHasFunctions  bool   — sidecar defines `safe()` and `safe-claude()`.

Contract: always exits 0. Never writes to stderr in normal operation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_SAFE_FN_RE = re.compile(r"^\s*safe\s*\(\s*\)", re.MULTILINE)
_SAFE_CLAUDE_FN_RE = re.compile(r"^\s*safe-claude\s*\(\s*\)", re.MULTILINE)
_EXPORT_APPEND_PROFILE_RE = re.compile(r"^\s*export\s+SAFEHOUSE_APPEND_PROFILE(?=[=\s]|$)")


def probe_binary() -> tuple[bool, str | None]:
    path = shutil.which("safehouse")
    return (path is not None, path)


def probe_brew_formula(brew_path: str | None) -> bool:
    if brew_path is None:
        return False
    try:
        result = subprocess.run(
            [brew_path, "list", "agent-safehouse", "--formula"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def probe_env_exported(home: Path) -> bool:
    """Return True if any rc file exports SAFEHOUSE_APPEND_PROFILE (commented lines excluded).

    Matches `export SAFEHOUSE_APPEND_PROFILE=...` at the start of the
    stripped line, NOT any line that merely mentions the var name.
    """
    rc_files = [home / ".zshrc", home / ".bash_profile", home / ".bashrc", home / ".profile"]
    for rc_path in rc_files:
        try:
            with rc_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if _EXPORT_APPEND_PROFILE_RE.match(stripped):
                        return True
        except OSError:
            continue
    return False


def probe_sidecar(sidecar: Path) -> tuple[bool, bool]:
    """Return (sidecar_exists, defines_both_safe_functions)."""
    if not sidecar.is_file():
        return False, False
    try:
        content = sidecar.read_text(encoding="utf-8")
    except OSError:
        return True, False
    has_safe = bool(_SAFE_FN_RE.search(content))
    has_safe_claude = bool(_SAFE_CLAUDE_FN_RE.search(content))
    return True, (has_safe and has_safe_claude)


def main() -> int:
    home = Path(os.environ.get("HOME") or os.path.expanduser("~"))
    sidecar = home / ".config" / "agent-safehouse" / "env.sh"
    binary_available, binary_path = probe_binary()
    brew_installed = probe_brew_formula(shutil.which("brew"))
    env_exported = probe_env_exported(home)
    sidecar_present, sidecar_has_functions = probe_sidecar(sidecar)

    print(json.dumps({
        "binaryAvailable": binary_available,
        "binaryPath": binary_path,
        "brewFormulaInstalled": brew_installed,
        "envExported": env_exported,
        "sidecarPresent": sidecar_present,
        "sidecarHasFunctions": sidecar_has_functions,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
