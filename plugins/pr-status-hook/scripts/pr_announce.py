#!/usr/bin/env python3
"""PostToolUse hook: print the open PR's URL the moment it becomes knowable.

The sibling `pr-status.sh` Stop hook already reports this URL. It only runs
when a turn ends. A user interrupt never reaches that point. Neither does a
turn still blocked inside `gh pr checks --watch`. So a `/wild-pr` run can
create a pull request, then spend twenty minutes in its babysit loop. The user
never gets a link.

This hook ties the same guarantee to the `gh` call instead of to turn end. It
also rate-limits itself, so a babysit loop cannot spam the user.

The URL always comes from `gh`, never from the model's account of what it did.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final, Optional, Sequence

# Commands worth checking after. `create` is the moment a URL first exists.
# `view` covers the path where preflight finds a PR that already existed, so no
# create ever runs. `checks` opens each long CI wait, which is the silence the
# user most needs a link in front of.
TRIGGER_COMMANDS: Final = ("gh pr create", "gh pr view", "gh pr checks")

# Branches that never have a PR of their own.
SKIPPED_BRANCHES: Final = frozenset({"HEAD", "main", "master", ""})

# A babysit pass opens with `gh pr view` and can then burn 600 seconds inside
# `gh pr checks --watch`. At five minutes a three-pass run yields roughly three
# banners, while two `gh pr view` calls seconds apart collapse into one.
ANNOUNCE_INTERVAL_SECONDS: Final = 300.0

GIT_TIMEOUT_SECONDS: Final = 5.0
GH_TIMEOUT_SECONDS: Final = 10.0

MARKER_DIR_NAME: Final = "wild-horses-pr-announce"

# Cursor spells the event in camelCase and writes hook banners to stderr.
CURSOR_EVENT_NAME: Final = "postToolUse"

_UNSAFE_PATH_CHARS: Final = re.compile(r"[^A-Za-z0-9_.-]")

# Runs a command and returns its trimmed stdout, or None if it failed.
CommandRunner = Callable[[Sequence[str]], Optional[str]]


@dataclass(frozen=True)
class HookInput:
    """The fields this hook reads out of the harness's stdin payload."""

    command: str
    session_id: str
    event_name: str
    cwd: str


def parse_hook_input(raw: str) -> Optional[HookInput]:
    """Parse the harness payload, or return None when it is unusable.

    A malformed payload means there is nothing to announce. The hook returns
    None so the caller can exit quietly. Raising instead would put a traceback
    in the result of every Bash tool call the user makes.
    """
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    tool_input = payload.get("tool_input")
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command") or "")

    return HookInput(
        command=command,
        session_id=str(payload.get("session_id") or "nosession"),
        event_name=str(payload.get("hook_event_name") or ""),
        cwd=str(payload.get("cwd") or ""),
    )


def is_trigger_command(command: str) -> bool:
    """Report whether this Bash command is one worth checking for a PR after.

    Matches anywhere in the command, not just at the start, because these calls
    are often embedded: `BASE=$(gh pr view --json baseRefName --jq .baseRefName)`.
    """
    return any(trigger in command for trigger in TRIGGER_COMMANDS)


def current_branch(run: CommandRunner) -> Optional[str]:
    """Return the checked-out branch name, or None outside a git work tree.

    One call does both jobs. `git rev-parse` already fails outside a repo. On a
    detached HEAD it answers `HEAD`, which `SKIPPED_BRANCHES` rejects.
    """
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"])


def is_announceable_branch(branch: Optional[str]) -> bool:
    """Report whether a branch is one that can own a pull request."""
    return branch is not None and branch not in SKIPPED_BRANCHES


def announceable_branch(run: CommandRunner) -> Optional[str]:
    """Return the checked-out branch when it can own a PR, else None."""
    branch = current_branch(run)
    return branch if is_announceable_branch(branch) else None


def marker_path(tmp_root: Path, session_id: str, branch: str) -> Path:
    """Build the rate-limit marker's path for one session and branch.

    Both parts are sanitized because branch names contain slashes. A raw slash
    would turn the marker into a nested directory that does not exist.
    """
    name = f"{_sanitize(session_id)}--{_sanitize(branch)}"
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


def open_pr_url(run: CommandRunner) -> Optional[str]:
    """Ask `gh` for the open PR's URL, or return None when there is none."""
    url = run(["gh", "pr", "view", "--json", "url", "--jq", ".url"])
    if url is None or not url.startswith("http"):
        return None
    return url


def touch_marker(marker: Path, now: float) -> None:
    """Stamp the marker so the next few calls stay quiet."""
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    os.utime(marker, (now, now))


def build_claude_payload(url: str) -> str:
    """Build the banner the harness prints to the user.

    `systemMessage` is the guarantee. The harness prints it whatever the model
    does next. The link reaches the user even when the run is interrupted.

    Nothing here talks to the model. `PostToolUse` documents no supported way to
    add context to the model's turn. The one channel it does document is exit
    code 2, which means failure. So the duty to restate the link stays where it
    can be read. That is **The PR link rule** in wild-pr's SKILL.md.
    """
    return json.dumps({"systemMessage": f"PR: {url}", "suppressOutput": True})


def make_runner(cwd: str, timeout: float) -> CommandRunner:
    """Build a command runner rooted at the directory the tool call ran in."""

    def run(argv: Sequence[str]) -> Optional[str]:
        try:
            done = subprocess.run(
                list(argv),
                cwd=cwd or None,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            # A missing or hanging `git`/`gh` leaves nothing to announce. This
            # hook is advisory, so it stays silent instead of failing the turn.
            return None
        if done.returncode != 0:
            return None
        return done.stdout.strip() or None

    return run


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
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    hook = parse_hook_input(raw)
    if hook is None:
        return 0

    tmp_root = Path(os.environ.get("TMPDIR") or "/tmp")
    url = announce(hook, tmp_root, time.time())
    if url is None:
        return 0

    if hook.event_name == CURSOR_EVENT_NAME:
        print(f"PR: {url}", file=sys.stderr)
        return 0

    print(build_claude_payload(url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
