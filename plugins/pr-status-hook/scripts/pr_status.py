#!/usr/bin/env python3
"""Stop hook: report the branch's PR, push, and dirty-tree state at turn end.

Three facts, read from real `git` and `gh` output rather than from the agent's
account of what it did. The hook stays silent unless one of them is worth
saying. A clean pushed branch with no PR is the only case it skips.

Its sibling `pr_announce.py` prints the PR link the moment `gh` knows it. This
one runs later and carries what that banner cannot. That is whether the commits
reached the remote, and whether anything is still uncommitted.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataclasses import dataclass
from typing import Optional

from pr_hook_common import (
    CURSOR_STOP_EVENT,
    GH_TIMEOUT_SECONDS,
    GIT_TIMEOUT_SECONDS,
    CommandRunner,
    announceable_branch,
    emit,
    make_runner,
    open_pr_url,
    parse_hook_input,
    read_stdin,
)


@dataclass(frozen=True)
class BranchStatus:
    """What the banner reports, before it is worded.

    `ahead` is None when the branch has no upstream at all. That is a
    different report from being level with one.
    """

    pr_url: Optional[str]
    ahead: Optional[str]
    dirty: int


def count_dirty(run: CommandRunner) -> int:
    """Count files with uncommitted changes."""
    porcelain = run(["git", "status", "--porcelain"])
    if porcelain is None:
        return 0
    return sum(1 for line in porcelain.splitlines() if line.strip())


def count_ahead(run: CommandRunner) -> Optional[str]:
    """Count commits not yet on the upstream, or None when there is no upstream.

    `git rev-list` fails rather than answering zero when `@{u}` resolves to
    nothing, and that failure is the signal for "never pushed".
    """
    return run(["git", "rev-list", "--count", "@{u}..HEAD"])


def read_status(git: CommandRunner, gh: CommandRunner) -> BranchStatus:
    """Gather the three facts the banner reports."""
    return BranchStatus(
        pr_url=open_pr_url(gh),
        ahead=count_ahead(git),
        dirty=count_dirty(git),
    )


def is_quiet(status: BranchStatus) -> bool:
    """Report whether every fact is unremarkable, so the hook says nothing.

    Only a branch with no PR, level with its upstream, and clean stays silent.
    A missing upstream is remarkable, so `ahead` of None is never quiet.
    """
    return status.pr_url is None and status.ahead == "0" and status.dirty == 0


def build_banner(branch: str, status: BranchStatus) -> str:
    """Word the three facts as one line."""
    if status.pr_url:
        parts = [f"PR {status.pr_url}"]
    else:
        parts = [f"No PR for branch '{branch}'"]

    if status.ahead is None:
        parts.append("⚠ branch has no upstream (never pushed)")
    elif status.ahead != "0":
        parts.append(f"⚠ {status.ahead} commit(s) NOT pushed")
    else:
        parts.append("✓ all commits pushed")

    if status.dirty != 0:
        parts.append(f"✎ {status.dirty} file(s) uncommitted")

    return " · ".join(parts)


def main() -> int:
    hook = parse_hook_input(read_stdin())
    cwd = hook.cwd if hook else ""
    event_name = hook.event_name if hook else ""

    git = make_runner(cwd, GIT_TIMEOUT_SECONDS)
    branch = announceable_branch(git)
    if branch is None:
        return 0

    status = read_status(git, make_runner(cwd, GH_TIMEOUT_SECONDS))
    if is_quiet(status):
        return 0

    emit(build_banner(branch, status), event_name, CURSOR_STOP_EVENT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
