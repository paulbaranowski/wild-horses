#!/usr/bin/env python3
"""What both pr-status-hook scripts agree on.

`pr_announce.py` answers "has the PR link reached the user yet". `pr_status.py`
answers "what is this branch's state right now". They run on different events
and print different text. Everything below is what they must not disagree on.
That covers reading the harness payload and running a command. It also covers
which branches can own a PR and how to ask `gh` for an open one. Last, it
covers which channel the banner goes out on.

The `OPEN` filter is the reason this module exists. It lived twice before, once
in `jq` and once in Python. A rule written down twice is a rule that drifts.

Neither script needs `jq`. A hook that depends on it fails silent when it is
missing. Silent is the one failure mode a status hook cannot afford.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final, Optional, Sequence

# Branches that never have a PR of their own. A detached HEAD answers `HEAD`.
SKIPPED_BRANCHES: Final = frozenset({"HEAD", "main", "master"})

GIT_TIMEOUT_SECONDS: Final = 5.0
GH_TIMEOUT_SECONDS: Final = 10.0

# What one hook run may spend in total, across every subprocess it starts. It
# sits well under the 20 second wrapper timeout in hooks.json. So the wrapper
# never has to kill a run that is still trying to print.
TOTAL_BUDGET_SECONDS: Final = 12.0

PR_CACHE_DIR_NAME: Final = "wild-horses-pr-cache"

# How long a cached PR answer may be reused. It buys back a `gh` call that
# measures around 500ms, on a hook that runs at every turn end.
#
# An hour rather than a minute, because the staleness it allows costs nothing
# here. One branch has one pull request, so a cache entry can only go stale by
# that PR changing state, and the banner shows the link either way. A new
# commit changes the key and asks again. The bound exists only so an entry
# cannot outlive the work by days if TMPDIR is never swept.
PR_CACHE_TTL_SECONDS: Final = 3600.0

# Cursor spells its events in camelCase and writes hook banners to stderr.
# Claude uses PascalCase and a JSON object on stdout.
CURSOR_STOP_EVENT: Final = "stop"
CURSOR_POST_TOOL_EVENT: Final = "postToolUse"

# Runs a command and returns its trimmed stdout, or None if it failed.
CommandRunner = Callable[[Sequence[str]], Optional[str]]


@dataclass(frozen=True)
class HookInput:
    """The fields these hooks read out of the harness's stdin payload."""

    command: str
    session_id: str
    event_name: str
    cwd: str


def parse_hook_input(raw: str) -> Optional[HookInput]:
    """Parse the harness payload, or return None when it is unusable.

    A malformed payload means there is nothing to report. The caller exits
    quietly on None. Raising instead would put a traceback in the result of
    every tool call the user makes.
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


def read_stdin() -> str:
    """Read the harness payload, tolerating a terminal with nothing piped in."""
    return sys.stdin.read() if not sys.stdin.isatty() else ""


def new_deadline() -> float:
    """Start the shared budget for one hook run.

    Per-call timeouts do not bound a hook. `pr_status.py` makes three `git`
    calls and one `gh` call. Its per-call budget therefore sums to 25 seconds,
    against a 20 second wrapper. A hook killed by its wrapper prints nothing,
    which is the one outcome these scripts exist to avoid. So every runner in a
    run shares one deadline. The total cannot exceed it, however many calls the
    script grows.
    """
    return time.monotonic() + TOTAL_BUDGET_SECONDS


def make_runner(cwd: str, timeout: float, deadline: Optional[float] = None) -> CommandRunner:
    """Build a command runner rooted at the directory the hook fired in.

    `timeout` caps one call. `deadline` caps the whole run, and the shorter of
    the two wins. Omitting the deadline keeps the per-call behavior, which suits
    a caller that makes exactly one call.
    """

    def run(argv: Sequence[str]) -> Optional[str]:
        limit = timeout
        if deadline is not None:
            limit = min(timeout, deadline - time.monotonic())
            if limit <= 0:
                # The budget is spent. Report nothing rather than start a call
                # the wrapper would kill mid-flight.
                return None
        try:
            done = subprocess.run(
                list(argv),
                cwd=cwd or None,
                capture_output=True,
                text=True,
                timeout=limit,
            )
        except (OSError, subprocess.SubprocessError):
            # A missing or hanging `git`/`gh` leaves nothing to report. These
            # hooks are advisory, so they stay silent instead of failing a turn.
            return None
        if done.returncode != 0:
            return None
        return done.stdout.strip() or None

    return run


def announceable_branch(run: CommandRunner) -> Optional[str]:
    """Return the checked-out branch when it can own a PR, else None.

    One `git` call does every job here. It fails outside a work tree. On a
    detached HEAD it answers `HEAD`, which `SKIPPED_BRANCHES` rejects.
    """
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return branch if branch and branch not in SKIPPED_BRANCHES else None


@dataclass(frozen=True)
class PullRequest:
    """A branch's most recent pull request, whatever state it is in."""

    url: str
    state: str

    @property
    def is_open(self) -> bool:
        return self.state == "OPEN"


def find_pull_request(run: CommandRunner) -> Optional[PullRequest]:
    """Ask `gh` for this branch's most recent PR, or None when there is none.

    The state comes back with the URL rather than filtering on it. A PR that
    merges mid-session should not make its own link disappear, which is what a
    `state == "OPEN"` filter does at the moment you most want to click it.

    Callers decide what a non-open state means for them. `pr_status.py` labels
    it. `pr_announce.py` ignores it, because one branch has one pull request.
    """
    # A raw string, so `jq` receives `\(...)` and `\t` and expands them itself.
    jq = r'"\(.state)\t\(.url)"'
    line = run(["gh", "pr", "view", "--json", "url,state", "--jq", jq])
    if line is None or "\t" not in line:
        return None
    state, url = line.split("\t", 1)
    if not url.startswith("http"):
        return None
    return PullRequest(url=url, state=state)


def pr_cache_path(tmp_root: Path, branch: str, head_sha: str) -> Path:
    """Where one branch's last known PR answer is kept.

    The key carries the commit, so a new commit asks `gh` again rather than
    trusting an answer from before it.
    """
    digest = hashlib.sha256(f"{branch}\0{head_sha}".encode()).hexdigest()[:16]
    return tmp_root / PR_CACHE_DIR_NAME / digest


def read_pr_cache(path: Path, now: float) -> Optional[PullRequest]:
    """Return a cached pull request that is still fresh, else None.

    Only `pr_status.py` reads this. `pr_announce.py` must not, because it runs
    at the instant `gh pr create` changes the answer. A cached "no PR" there
    would suppress the one banner this plugin exists to guarantee.
    """
    try:
        if (now - path.stat().st_mtime) >= PR_CACHE_TTL_SECONDS:
            return None
        line = path.read_text().strip()
    except OSError:
        return None
    if "\t" not in line:
        return None
    state, url = line.split("\t", 1)
    return PullRequest(url=url, state=state) if url.startswith("http") else None


def write_pr_cache(path: Path, pull: PullRequest) -> None:
    """Record a pull request both hooks can reuse.

    `pr_announce.py` writes here after every fresh lookup, so a PR it just
    announced is already warm for the next turn end.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{pull.state}\t{pull.url}")
    except OSError:
        # A cache we cannot write costs a `gh` call, never a banner.
        return


def emit(banner: str, event_name: str, cursor_event: str) -> None:
    """Send one banner out on whichever channel this harness reads.

    Cursor shows a hook's stderr. Claude reads a JSON object on stdout. There
    `systemMessage` is the user-facing text, and `suppressOutput` keeps the JSON
    itself out of the transcript.
    """
    if event_name == cursor_event:
        print(banner, file=sys.stderr)
        return
    # `ensure_ascii=False` keeps `·`, `⚠`, and `✎` as themselves. The default
    # escapes them to `\u00b7` and friends. That parses back the same. It does
    # not match the bytes `jq -n` wrote before this script replaced it.
    print(json.dumps({"systemMessage": banner, "suppressOutput": True}, ensure_ascii=False))
