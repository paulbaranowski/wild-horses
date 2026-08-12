#!/usr/bin/env python3
"""PostToolUse hook: print the open PR's URL the moment it becomes knowable.

The sibling `pr_status.py` Stop hook already reports this URL. It only runs
when a turn ends. A user interrupt never reaches that point. Neither does a
turn still blocked inside `gh pr checks --watch`. So a `/wild-pr` run can
create a pull request, then spend twenty minutes in its babysit loop. The user
never gets a link.

This hook ties the same guarantee to the `gh` call instead of to turn end. It
also rate-limits itself, so a babysit loop cannot spam the user.

The URL always comes from `gh`, never from the model's account of what it did.
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import re
import time
from pathlib import Path
from typing import Final, Optional

from pr_hook_common import (
    CURSOR_POST_TOOL_EVENT,
    GH_TIMEOUT_SECONDS,
    GIT_TIMEOUT_SECONDS,
    HookInput,
    announceable_branch,
    emit,
    make_runner,
    open_pr_url,
    parse_hook_input,
    read_stdin,
)

# Commands worth checking after. All three fire once the command returns,
# because that is what PostToolUse means. None can announce ahead of the tool
# it matches.
#
# `create` is the moment a URL first exists. It is the one that beats the CI
# wait. `view` covers the path where preflight finds a PR that already existed.
# It also opens each babysit pass. `checks` fires when a long `--watch`
# returns. So a pass that ran silently for minutes ends with the link on screen.
TRIGGER_COMMANDS: Final = ("gh pr create", "gh pr view", "gh pr checks")

# A babysit pass opens with `gh pr view` and can then burn 600 seconds inside
# `gh pr checks --watch`. At five minutes a three-pass run yields roughly three
# banners, while two `gh pr view` calls seconds apart collapse into one.
ANNOUNCE_INTERVAL_SECONDS: Final = 300.0

MARKER_DIR_NAME: Final = "wild-horses-pr-announce"

_UNSAFE_PATH_CHARS: Final = re.compile(r"[^A-Za-z0-9_.-]")


def is_trigger_command(command: str) -> bool:
    """Report whether this Bash command is one worth checking for a PR after.

    Matches anywhere in the command, not just at the start, because these calls
    are often embedded: `BASE=$(gh pr view --json baseRefName --jq .baseRefName)`.
    """
    return any(trigger in command for trigger in TRIGGER_COMMANDS)


def marker_path(tmp_root: Path, session_id: str, branch: str) -> Path:
    """Build the rate-limit marker's path for one session and branch.

    Both parts are sanitized because branch names contain slashes. A raw slash
    would turn the marker into a nested directory that does not exist.

    Sanitizing alone collides. `feat/a` and `feat_a` are both valid branches, and
    both become `feat_a`. Sharing one marker would let either branch mute the
    other's banner for five minutes. Worktrees make two branches in one session
    ordinary. So the name also carries a digest of the raw values. That keeps the
    branch readable for whoever debugs a stuck marker.
    """
    digest = hashlib.sha256(f"{session_id}\0{branch}".encode()).hexdigest()[:8]
    name = f"{_sanitize(session_id)}--{_sanitize(branch)}-{digest}"
    return tmp_root / MARKER_DIR_NAME / name


def _sanitize(value: str) -> str:
    return _UNSAFE_PATH_CHARS.sub("_", value)


def should_announce(marker: Path, now: float, interval: float) -> bool:
    """Report whether enough time has passed since the last banner.

    An unreadable marker counts as due. Losing the rate limit costs the user one
    duplicate line. Losing the banner costs them the link this hook exists for.
    """
    try:
        last = marker.stat().st_mtime
    except OSError:
        # Covers both a missing marker and an unreadable one.
        return True
    return (now - last) >= interval


def touch_marker(marker: Path, now: float) -> None:
    """Stamp the marker so the next few calls stay quiet.

    A marker this process cannot write costs the user one duplicate banner. An
    escaping OSError costs them the banner itself. It also puts a traceback in
    the result of every `gh pr` call. So this write stays advisory, like the
    reads around it.
    """
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        os.utime(marker, (now, now))
    except OSError:
        return


def announce(hook: HookInput, tmp_root: Path, now: float) -> Optional[str]:
    """Decide whether to announce, and return the URL when the answer is yes.

    Returns None at every gate that does not apply, so the caller stays quiet.

    Announcing also stamps the rate-limit marker, so calling this twice in a row
    yields the URL once. That write is the reason this is not a pure predicate.
    """
    if not is_trigger_command(hook.command):
        return None

    branch = announceable_branch(make_runner(hook.cwd, GIT_TIMEOUT_SECONDS))
    if branch is None:
        return None

    marker = marker_path(tmp_root, hook.session_id, branch)
    if not should_announce(marker, now, ANNOUNCE_INTERVAL_SECONDS):
        return None

    url = open_pr_url(make_runner(hook.cwd, GH_TIMEOUT_SECONDS))
    if url is None:
        # No PR yet. Leave the marker alone so the next call checks again.
        return None

    touch_marker(marker, now)
    return url


def main() -> int:
    hook = parse_hook_input(read_stdin())
    if hook is None:
        return 0

    tmp_root = Path(os.environ.get("TMPDIR") or "/tmp")
    url = announce(hook, tmp_root, time.time())
    if url is None:
        return 0

    emit(f"PR: {url}", hook.event_name, CURSOR_POST_TOOL_EVENT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
