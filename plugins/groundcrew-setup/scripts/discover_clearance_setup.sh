#!/usr/bin/env bash
# discover_clearance_setup.sh — probe the @clipboard-health/clearance egress-
# allowlist setup and report its state as a JSON object on stdout.
#
# Output fields:
#   personalFileExists      — does ~/.config/clearance/personal-allow-hosts exist?
#   personalFileHasClaudeHosts — does that file (if present) contain an uncommented
#                               line with "downloads.claude.ai"?
#   envExported             — does any shell rc file (zshrc, bash_profile, bashrc,
#                               profile) export CLEARANCE_ALLOW_HOSTS_FILES?
#   daemonPid               — integer pid from ~/.cache/clearance/clearance.pid,
#                               or null if the file is absent or unparseable.
#   daemonAgeSeconds        — age of the pid FILE in whole seconds (now - mtime),
#                               or null if the pid file doesn't exist.
#
# Contract: always exits 0. Never writes to stderr in normal operation.

set -euo pipefail

python3 - "$HOME" <<'PYEOF'
from __future__ import annotations

import json
import os
import sys
import time

home = sys.argv[1]

# ---------------------------------------------------------------------------
# 1. personalFileExists
# ---------------------------------------------------------------------------
personal_file = os.path.join(home, ".config", "clearance", "personal-allow-hosts")
personal_file_exists: bool = os.path.isfile(personal_file)

# ---------------------------------------------------------------------------
# 2. personalFileHasClaudeHosts
# ---------------------------------------------------------------------------
personal_file_has_claude_hosts: bool = False
if personal_file_exists:
    try:
        with open(personal_file, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                # Skip comments and blank lines
                if not stripped or stripped.startswith("#"):
                    continue
                if "downloads.claude.ai" in stripped:
                    personal_file_has_claude_hosts = True
                    break
    except OSError:
        pass

# ---------------------------------------------------------------------------
# 3. envExported
# ---------------------------------------------------------------------------
rc_files = [
    os.path.join(home, ".zshrc"),
    os.path.join(home, ".bash_profile"),
    os.path.join(home, ".bashrc"),
    os.path.join(home, ".profile"),
]

env_exported: bool = False
for rc_path in rc_files:
    try:
        with open(rc_path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                # Skip comments and blank lines
                if not stripped or stripped.startswith("#"):
                    continue
                if "CLEARANCE_ALLOW_HOSTS_FILES" in stripped:
                    env_exported = True
                    break
    except OSError:
        # Missing rc file is fine — skip it
        pass
    if env_exported:
        break

# ---------------------------------------------------------------------------
# 4. daemonPid  +  5. daemonAgeSeconds
# ---------------------------------------------------------------------------
pid_file = os.path.join(home, ".cache", "clearance", "clearance.pid")
daemon_pid: int | None = None
daemon_age_seconds: int | None = None

if os.path.isfile(pid_file):
    # Age from mtime regardless of pid validity
    try:
        mtime = os.path.getmtime(pid_file)
        daemon_age_seconds = round(time.time() - mtime)
    except OSError:
        pass

    try:
        with open(pid_file, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        daemon_pid = int(raw)
    except (OSError, ValueError):
        # Malformed pid → null; daemonAgeSeconds still set if mtime succeeded
        daemon_pid = None

# ---------------------------------------------------------------------------
# Emit JSON
# ---------------------------------------------------------------------------
print(json.dumps({
    "personalFileExists": personal_file_exists,
    "personalFileHasClaudeHosts": personal_file_has_claude_hosts,
    "envExported": env_exported,
    "daemonPid": daemon_pid,
    "daemonAgeSeconds": daemon_age_seconds,
}))
PYEOF
