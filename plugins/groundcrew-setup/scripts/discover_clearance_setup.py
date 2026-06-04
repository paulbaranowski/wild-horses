#!/usr/bin/env python3
"""Probe the @clipboard-health/clearance egress-allowlist setup.

Reports the state as a JSON object on stdout.

Output fields:
  personalFileExists         — does ~/.config/clearance/personal-allow-hosts exist?
  personalFileHasClaudeHosts — does that file (if present) contain an uncommented
                               line with "downloads.claude.ai"?
  envExported                — does any shell rc file (zshrc, bash_profile, bashrc,
                               profile) export CLEARANCE_ALLOW_HOSTS_FILES?
  daemonPid                  — integer pid from ~/.cache/clearance/clearance.pid,
                               or null if the file is absent or unparseable.
  daemonAgeSeconds           — age of the pid FILE in whole seconds (now - mtime),
                               or null if the pid file doesn't exist.

Contract: always exits 0. Never writes to stderr in normal operation.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

_EXPORT_ALLOW_HOSTS_RE = re.compile(r"^\s*export\s+CLEARANCE_ALLOW_HOSTS_FILES(?=[=\s]|$)")


def check_personal_file(personal_file: Path) -> tuple[bool, bool]:
    """Return (file_exists, has_claude_hosts) for the personal allowlist file."""
    exists = personal_file.is_file()
    has_claude_hosts = False
    if exists:
        try:
            with personal_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if "downloads.claude.ai" in stripped:
                        has_claude_hosts = True
                        break
        except OSError:
            pass
    return exists, has_claude_hosts


def check_env_exported(home: Path) -> bool:
    """Return True if any rc file exports CLEARANCE_ALLOW_HOSTS_FILES (commented lines excluded).

    Matches `export CLEARANCE_ALLOW_HOSTS_FILES=...` at the start of the
    line, NOT any line that merely mentions the var name (e.g. an
    `echo "set CLEARANCE_ALLOW_HOSTS_FILES"` reminder, an unset alias,
    or the var appearing inside another export's value as ${VAR:+...}).
    """
    rc_files = [home / ".zshrc", home / ".bash_profile", home / ".bashrc", home / ".profile"]
    for rc_path in rc_files:
        try:
            with rc_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if _EXPORT_ALLOW_HOSTS_RE.match(stripped):
                        return True
        except OSError:
            continue
    return False


def _pid_is_alive(pid: int) -> bool:
    """Return True if a process with this pid currently exists.

    Uses signal 0, which performs the standard kernel permission/existence
    check without delivering any signal. ProcessLookupError → dead/missing;
    PermissionError → the process exists but we can't signal it (still
    "alive" for our purpose).
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def check_daemon(pid_file: Path) -> tuple[int | None, int | None]:
    """Return (pid, age_seconds) parsed from the daemon pid file.

    `daemon_pid` is None when the pid file is missing, unreadable,
    contains non-integer garbage, OR the pid no longer points at a
    live process. The age field tracks the pid FILE's mtime regardless
    — useful even with a stale pid for "this daemon hasn't been
    refreshed in N seconds." Callers should treat `daemon_pid == null`
    as "no live daemon to kill" (avoids `kill <stale-pid>` no-ops or,
    worse, killing a recycled pid).
    """
    if not pid_file.is_file():
        return None, None
    daemon_pid: int | None = None
    daemon_age_seconds: int | None = None
    try:
        mtime = pid_file.stat().st_mtime
        daemon_age_seconds = round(time.time() - mtime)
    except OSError:
        pass
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
        candidate = int(raw)
    except (OSError, ValueError):
        return None, daemon_age_seconds
    if _pid_is_alive(candidate):
        daemon_pid = candidate
    return daemon_pid, daemon_age_seconds


def main() -> int:
    home = Path(os.environ.get("HOME") or os.path.expanduser("~"))
    personal_file = home / ".config" / "clearance" / "personal-allow-hosts"
    pid_file = home / ".cache" / "clearance" / "clearance.pid"

    personal_file_exists, personal_file_has_claude_hosts = check_personal_file(personal_file)
    env_exported = check_env_exported(home)
    daemon_pid, daemon_age_seconds = check_daemon(pid_file)

    print(json.dumps({
        "personalFileExists": personal_file_exists,
        "personalFileHasClaudeHosts": personal_file_has_claude_hosts,
        "envExported": env_exported,
        "daemonPid": daemon_pid,
        "daemonAgeSeconds": daemon_age_seconds,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
