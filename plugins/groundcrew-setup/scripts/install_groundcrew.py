#!/usr/bin/env python3
"""Detect and (optionally) install @clipboard-health/groundcrew via npm.

Probes `npm ls -g` for the package; installs via `npm install -g` if missing.
Idempotent: a second invocation against an already-installed system is a no-op.

JSON output on stdout (single line):
    {"action": "<str>", "version": "<str>|null", "details": "<str>"}

Actions:
    already-installed  groundcrew is present (no-op).
    installed          installed during this invocation.
    missing            not installed (only emitted with --check).
    failed             npm unavailable or install failed.

Exit codes:
    0    on success (already-installed | installed | missing-with-check)
    1    when npm is not on PATH
    >0   propagated from npm install on install failure
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import TypedDict

PACKAGE_NAME = "@clipboard-health/groundcrew"


class StatusReport(TypedDict):
    action: str
    version: str | None
    details: str


def _emit(report: StatusReport) -> None:
    print(json.dumps(report))


def probe_installed(npm_path: str) -> tuple[bool, str | None]:
    """Return (installed, version) by parsing `npm ls -g <pkg> --json`.

    npm ls exits non-zero when the package is missing but still writes a
    valid JSON body, so we ignore exit code and key off `dependencies`.
    """
    try:
        result = subprocess.run(
            [npm_path, "ls", "-g", PACKAGE_NAME, "--depth", "0", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, None
    if not isinstance(data, dict):
        return False, None
    deps = data.get("dependencies", {})
    if not isinstance(deps, dict):
        return False, None
    info = deps.get(PACKAGE_NAME)
    if not isinstance(info, dict):
        return False, None
    version = info.get("version")
    return True, version if isinstance(version, str) else None


def install_global(npm_path: str) -> tuple[int, str]:
    """Run `npm install -g <pkg>`. Return (exit_code, error_details_if_any)."""
    try:
        result = subprocess.run(
            [npm_path, "install", "-g", PACKAGE_NAME],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 1, f"npm install timed out after 600s for {PACKAGE_NAME}"
    if result.returncode != 0:
        return result.returncode, (
            result.stderr.strip() or result.stdout.strip() or "npm install failed"
        )
    return 0, ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=f"Detect and install {PACKAGE_NAME} via npm.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Probe only; do not install.",
    )
    args = parser.parse_args(argv)

    npm_path = shutil.which("npm")
    if npm_path is None:
        _emit(StatusReport(
            action="failed",
            version=None,
            details="npm not found on PATH — install Node.js from https://nodejs.org",
        ))
        return 1

    installed, version = probe_installed(npm_path)
    if installed:
        _emit(StatusReport(action="already-installed", version=version, details=""))
        return 0

    if args.check:
        _emit(StatusReport(action="missing", version=None, details=""))
        return 0

    exit_code, details = install_global(npm_path)
    if exit_code != 0:
        _emit(StatusReport(action="failed", version=None, details=details))
        return exit_code

    _, new_version = probe_installed(npm_path)
    _emit(StatusReport(action="installed", version=new_version, details=""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
