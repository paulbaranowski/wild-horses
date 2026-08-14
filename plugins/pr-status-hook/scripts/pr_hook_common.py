#!/usr/bin/env python3
"""What both pr-status-hook scripts agree on.

`pr_announce.py` answers "has the PR link reached the user yet". `pr_status.py`
answers "what is this branch's state right now". They run on different events
and print different text. Everything below is what they must not disagree on.
That covers reading the harness payload and running a command. It also covers
which branches can own a PR. Next comes the `gh` query for that branch's pull
request, and how to spell the link. Last comes the channel a banner goes out on.

The `gh` query is the reason this module exists. It lived twice before, once in
`jq` and once in Python, and the two drifted on the state filter. The query now
reports the state, and each caller decides what a non-open state means.

Neither script needs `jq`. A hook that depends on it fails silent when it is
missing. Silent is the one failure mode a status hook cannot afford.
"""

from __future__ import annotations

import hashlib
import json
import os
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
# An hour rather than a minute, because the staleness it allows costs little
# here. One branch has one pull request. So an entry can only go stale by that
# PR changing state, and the banner shows the link either way. The label can
# lag, which is the price. A new commit changes the key and asks again.
PR_CACHE_TTL_SECONDS: Final = 3600.0

# Three harnesses run these hooks, and each one names things its own way.
#
#   Claude sends `hook_event_name` in PascalCase, and reads stdout JSON.
#   Cursor sends `hook_event_name` in camelCase, and reads stderr.
#   Grok sends `hookEventName` in snake_case, and reads stderr.
#
# Grok ignores a passive hook's stdout outright. So its banner has to go to
# stderr, the way Cursor's does. The key alone cannot tell Claude and Cursor
# apart. Only the case of the value does. So both spellings are kept here.
CURSOR_STOP_EVENT: Final = "stop"
CURSOR_POST_TOOL_EVENT: Final = "postToolUse"
GROK_STOP_EVENT: Final = "stop"
GROK_POST_TOOL_EVENT: Final = "post_tool_use"

# Which harness sent the payload. `emit` picks a channel from this.
RUNTIME_CLAUDE: Final = "claude"
RUNTIME_CURSOR: Final = "cursor"
RUNTIME_GROK: Final = "grok"

# No event key at all, so no harness is identified. `emit` prints nothing for
# this. Every harness sends one of the two keys, so the only payloads that land
# here are malformed. Guessing would print a Claude banner into a Grok session.
RUNTIME_UNKNOWN: Final = "unknown"

# Runs a command and returns its trimmed stdout, or None if it failed.
CommandRunner = Callable[[Sequence[str]], Optional[str]]


@dataclass(frozen=True)
class HookInput:
    """The fields these hooks read out of the harness's stdin payload."""

    command: str
    session_id: str
    event_name: str
    cwd: str
    runtime: str


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

    # Grok camelCases every key. Read both spellings of the three fields that
    # differ, and let the event key decide which harness sent this.
    snake_event = str(payload.get("hook_event_name") or "")
    camel_event = str(payload.get("hookEventName") or "")
    if camel_event:
        runtime = RUNTIME_GROK
    elif snake_event in (CURSOR_STOP_EVENT, CURSOR_POST_TOOL_EVENT):
        runtime = RUNTIME_CURSOR
    elif snake_event:
        runtime = RUNTIME_CLAUDE
    else:
        runtime = RUNTIME_UNKNOWN

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = payload.get("toolInput")
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command") or "")

    return HookInput(
        command=command,
        session_id=str(
            payload.get("session_id") or payload.get("sessionId") or "nosession"
        ),
        event_name=camel_event or snake_event,
        cwd=str(payload.get("cwd") or ""),
        runtime=runtime,
    )


def read_stdin() -> str:
    """Read the harness payload, tolerating a terminal with nothing piped in."""
    return sys.stdin.read() if not sys.stdin.isatty() else ""


def new_deadline() -> float:
    """Start the shared budget for one hook run.

    Per-call timeouts do not bound a hook. `pr_announce.py` makes two `git`
    calls and one `gh` call. Its per-call budget therefore sums to 20 seconds,
    which is the wrapper timeout exactly. A hook killed by its wrapper prints
    nothing, which is the one outcome these scripts exist to avoid. So every
    runner in a run shares one deadline. The total cannot exceed it, however
    many calls either script grows.
    """
    return time.monotonic() + TOTAL_BUDGET_SECONDS


def make_runner(cwd: str, timeout: float, deadline: float) -> CommandRunner:
    """Build a command runner rooted at the directory the hook fired in.

    `timeout` caps one call. `deadline` caps the whole run, and the shorter of
    the two wins. Every caller passes a deadline, so no run can outlive it.
    """

    def run(argv: Sequence[str]) -> Optional[str]:
        limit = min(timeout, deadline - time.monotonic())
        if limit <= 0:
            # The budget is spent. Report nothing rather than start a call the
            # wrapper would kill mid-flight.
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
    merges mid-session should not make its own link disappear. That is what a
    `state == "OPEN"` filter does, at the moment you most want to click.

    Callers decide what a non-open state means for them. Both banners label it,
    through `pr_link` below.
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


def pr_link(pull: PullRequest) -> str:
    """Spell one pull request's link the way both banners spell it.

    `PR: <url>` is the form wild-pr's link rule defines. A non-open PR carries
    its state. The link outlives the merge, and an unlabelled link reads as an
    open one. Two banners for one link once used two spellings, and a reader
    scanning for a link should only learn one.
    """
    if pull.is_open:
        return f"PR: {pull.url}"
    return f"PR: {pull.url} ({pull.state.lower()})"


def state_root() -> Optional[Path]:
    """A private directory for what these hooks keep between runs.

    Not `TMPDIR`. On Linux that is often `/tmp`, which is world-writable. Both paths below
    are derived from a branch name and a commit sha. A local user who guesses one can plant a file the banner then prints. They
    can also leave a symlink that redirects a write. Neither is exotic on a shared machine.

    The directory is created private, and re-chmodded if it already exists. An
    entry planted before first run cannot inherit loose permissions.

    Returns None when the directory cannot be made usable. Callers then run
    without a cache or a marker, which costs a `gh` call or a duplicate banner.
    """
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    root = Path(base) / "wild-horses" / "pr-status-hook"
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
    except OSError:
        # Both hooks call this before they can print. Raising here would put
        # a traceback after every turn end, and cost the banner entirely. So
        # an unusable directory returns None, and callers run without state.
        return None
    return root


def pr_cache_path(root: Path, repo_id: str, branch: str, head_sha: str) -> Path:
    """Where one branch's last known PR answer is kept.

    The key carries the commit, so a new commit asks `gh` again rather than
    trusting an answer from before it. It carries a repository identifier too,
    because a branch name and sha do not name a repository. Two clones can
    share both, and a fork and its upstream resolve to different pull requests.
    """
    digest = hashlib.sha256(f"{repo_id}\0{branch}\0{head_sha}".encode()).hexdigest()[:16]
    return root / PR_CACHE_DIR_NAME / digest


def read_pr_cache(path: Path, now: float) -> Optional[PullRequest]:
    """Return a cached pull request that is still fresh, else None.

    Only `pr_status.py` reads this. `pr_announce.py` must not, because it runs
    at the instant `gh pr create` changes the answer. A cached "no PR" there
    would suppress the one banner this plugin exists to guarantee.
    """
    try:
        # O_NOFOLLOW so a planted symlink cannot redirect this read.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if (now - os.fstat(fd).st_mtime) >= PR_CACHE_TTL_SECONDS:
            return None
        line = os.read(fd, 4096).decode("utf-8", "replace").strip()
    except OSError:
        return None
    finally:
        os.close(fd)
    if "\t" not in line:
        return None
    state, url = line.split("\t", 1)
    return PullRequest(url=url, state=state) if url.startswith("http") else None


def write_pr_cache(path: Path, pull: PullRequest) -> None:
    """Record a pull request both hooks can reuse.

    `pr_announce.py` writes here after every fresh lookup. A PR it just
    announced is then already recorded for the next turn end.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # O_NOFOLLOW so a planted symlink cannot redirect this write.
        # Without it the target could be any file this user can write.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, f"{pull.state}\t{pull.url}".encode())
        finally:
            os.close(fd)
    except OSError:
        # A cache we cannot write costs a `gh` call, never a banner.
        return


def emit(banner: str, runtime: str) -> None:
    """Send one banner out on whichever channel this harness reads.

    Cursor and Grok both show a hook's stderr, and Grok discards a passive
    hook's stdout entirely. Claude reads a JSON object on stdout. There
    `systemMessage` is the user-facing text, and `suppressOutput` keeps the JSON
    itself out of the transcript.
    """
    if runtime == RUNTIME_UNKNOWN:
        return
    if runtime in (RUNTIME_CURSOR, RUNTIME_GROK):
        print(banner, file=sys.stderr)
        return
    # `ensure_ascii=False` keeps `·`, `⚠`, and `✎` as themselves. The default
    # escapes them to `\u00b7` and friends. That parses back the same. It does
    # not match the bytes `jq -n` wrote before this script replaced it.
    print(json.dumps({"systemMessage": banner, "suppressOutput": True}, ensure_ascii=False))
