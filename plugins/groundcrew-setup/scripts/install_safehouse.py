#!/usr/bin/env python3
"""Detect and (optionally) install eugene1g/agent-safehouse via Homebrew.

Probes via `brew list agent-safehouse --formula`; installs via
`brew install eugene1g/safehouse/agent-safehouse`. Idempotent.

The tap (eugene1g/safehouse) is auto-added by `brew install` on first use
— no separate `brew tap` step is needed.

JSON output on stdout (single line):
    {"action": "<str>", "version": "<str>|null", "details": "<str>"}

Actions:
    already-installed  agent-safehouse formula is present (no-op).
    installed          installed during this invocation.
    missing            not installed (only emitted with --check).
    failed             brew unavailable or install failed.

Exit codes:
    0    on success (already-installed | installed | missing-with-check)
    1    when brew is not on PATH
    >0   propagated from brew install on install failure
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from typing import TypedDict

FORMULA_REF = "eugene1g/safehouse/agent-safehouse"
FORMULA_NAME = "agent-safehouse"


class StatusReport(TypedDict):
    action: str
    version: str | None
    details: str


def _emit(report: StatusReport) -> None:
    print(json.dumps(report))


def probe_installed(brew_path: str) -> tuple[bool, str | None]:
    """Return (installed, version) by inspecting `brew list --versions`."""
    try:
        result = subprocess.run(
            [brew_path, "list", "--versions", FORMULA_NAME],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, None
    if result.returncode != 0:
        return False, None
    # Output: "agent-safehouse 0.9.0\n"
    parts = result.stdout.strip().split()
    if not parts:
        return False, None
    # First token is the formula name; remaining tokens are version(s).
    if len(parts) == 1:
        return True, None
    version = parts[1]
    return True, version if _looks_like_version(version) else None


_VERSION_RE = re.compile(r"^\d+(\.\d+)*(\S*)$")


def _looks_like_version(token: str) -> bool:
    return bool(_VERSION_RE.match(token))


def install_formula(brew_path: str) -> tuple[int, str]:
    """Run `brew install <tap>/<formula>`. Return (exit_code, error_details_if_any)."""
    try:
        result = subprocess.run(
            [brew_path, "install", FORMULA_REF],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 1, f"brew install timed out after 600s for {FORMULA_REF}"
    if result.returncode != 0:
        return result.returncode, (
            result.stderr.strip() or result.stdout.strip() or "brew install failed"
        )
    return 0, ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=f"Detect and install {FORMULA_REF} via Homebrew.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Probe only; do not install.",
    )
    args = parser.parse_args(argv)

    brew_path = shutil.which("brew")
    if brew_path is None:
        _emit(StatusReport(
            action="failed",
            version=None,
            details="brew not found on PATH — install Homebrew from https://brew.sh",
        ))
        return 1

    installed, version = probe_installed(brew_path)
    if installed:
        _emit(StatusReport(action="already-installed", version=version, details=""))
        return 0

    if args.check:
        _emit(StatusReport(action="missing", version=None, details=""))
        return 0

    exit_code, details = install_formula(brew_path)
    if exit_code != 0:
        _emit(StatusReport(action="failed", version=None, details=details))
        return exit_code

    _, new_version = probe_installed(brew_path)
    _emit(StatusReport(action="installed", version=new_version, details=""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
