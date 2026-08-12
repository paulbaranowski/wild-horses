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
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pr_hook_common import (
    CURSOR_STOP_EVENT,
    GH_TIMEOUT_SECONDS,
    GIT_TIMEOUT_SECONDS,
    SKIPPED_BRANCHES,
    CommandRunner,
    PullRequest,
    emit,
    make_runner,
    new_deadline,
    find_pull_request,
    parse_hook_input,
    pr_cache_path,
    read_pr_cache,
    read_stdin,
    state_root,
    write_pr_cache,
)


@dataclass(frozen=True)
class BranchStatus:
    """What the banner reports, before it is worded.

    `ahead` is None when the branch has no upstream at all. That is a
    different report from being level with one.
    """

    pull: Optional[PullRequest]
    ahead: Optional[str]
    dirty: int


@dataclass(frozen=True)
class WorkTree:
    """Everything one `git status --porcelain=v2 --branch` call reports.

    `branch` is None on a detached HEAD, which that format spells `(detached)`.
    `upstream` is None when the branch was never pushed. Then `ahead` is None
    too, because a branch with no upstream cannot be counted against one.
    """

    branch: Optional[str]
    upstream: Optional[str]
    ahead: Optional[str]
    dirty: int
    head_sha: str


def parse_work_tree(porcelain: Optional[str]) -> WorkTree:
    """Read branch, upstream, ahead count, and dirty count from one output.

    This replaced three separate `git` calls. Each header line is optional, and
    an absent `# branch.upstream` is the signal for "never pushed".
    """
    branch = upstream = ahead = None
    head_sha = ""
    dirty = 0
    for line in (porcelain or "").splitlines():
        if not line.strip():
            continue
        if not line.startswith("# "):
            # Every non-header line is one file with something uncommitted.
            dirty += 1
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        key, value = parts[1], parts[2]
        if key == "branch.oid":
            head_sha = value
        elif key == "branch.head":
            branch = None if value == "(detached)" else value
        elif key == "branch.upstream":
            upstream = value
        elif key == "branch.ab":
            # `+N -M`, where N is commits we have that the upstream does not.
            ahead = value.lstrip("+")
    return WorkTree(
        branch=branch, upstream=upstream, ahead=ahead, dirty=dirty, head_sha=head_sha
    )


def read_work_tree(run: CommandRunner) -> WorkTree:
    """Ask `git` once for every local fact the banner needs."""
    return parse_work_tree(run(["git", "status", "--porcelain=v2", "--branch"]))


def is_quiet(status: BranchStatus) -> bool:
    """Report whether every fact is unremarkable, so the hook says nothing.

    Only a branch with no PR, level with its upstream, and clean stays silent.
    A missing upstream is remarkable, so `ahead` of None is never quiet.
    """
    return status.pull is None and status.ahead == "0" and status.dirty == 0


def build_banner(branch: str, status: BranchStatus) -> str:
    """Word the three facts as one line.

    The link uses the `PR: <url>` form that wild-pr's link rule defines, which
    is also what `pr_announce.py` prints. Two banners for one link once used two
    spellings, and a reader scanning for a link should only learn one.
    """
    if status.pull is None:
        parts = [f"No PR for branch '{branch}'"]
    elif status.pull.is_open:
        parts = [f"PR: {status.pull.url}"]
    else:
        # The link still shows after a merge, which is when it is most wanted.
        # The state is spelled out because every other fact on this line
        # carries its own. An unlabelled link reads as an open PR.
        parts = [f"PR: {status.pull.url} ({status.pull.state.lower()})"]

    if status.ahead is None:
        parts.append("⚠ branch has no upstream (never pushed)")
    elif status.ahead != "0":
        parts.append(f"⚠ {status.ahead} commit(s) NOT pushed")
    else:
        parts.append("✓ all commits pushed")

    if status.dirty != 0:
        parts.append(f"✎ {status.dirty} file(s) uncommitted")

    return " · ".join(parts)


def find_pr(
    tree: WorkTree, cwd: str, deadline: float, root: Path, now: float
) -> Optional[PullRequest]:
    """Find this branch's open PR, avoiding `gh` when a fresh answer is cached.

    `gh pr view` measures around 500ms, and this hook runs at every turn end.

    An earlier version also skipped `gh` when the branch had no upstream. The
    reasoning was that an unpushed branch cannot have a PR. That is wrong.
    `gh pr view` resolves by branch name against the remote, not by local
    tracking config. So `git branch --unset-upstream` and a recreated local
    branch both leave an open PR reachable with no upstream. The recorded
    `pr-no-upstream` case is exactly that state, and it caught the mistake.
    """
    # `cwd` is the repository identifier. Two clones sit at different paths,
    # and a fork and its upstream resolve to different pull requests.
    cache = pr_cache_path(root, cwd, tree.branch or "", tree.head_sha)
    cached = read_pr_cache(cache, now)
    if cached is not None:
        return cached

    pull = find_pull_request(make_runner(cwd, GH_TIMEOUT_SECONDS, deadline))
    if pull is not None:
        write_pr_cache(cache, pull)
    return pull


def main() -> int:
    hook = parse_hook_input(read_stdin())
    if hook is None:
        # The shared parser returns None for a payload it cannot use, and that
        # means stay quiet. Carrying on with empty defaults would read the
        # process working directory and could still print a banner.
        return 0

    # One budget across all four subprocess calls this run makes.
    deadline = new_deadline()
    tree = read_work_tree(make_runner(hook.cwd, GIT_TIMEOUT_SECONDS, deadline))
    if tree.branch is None or tree.branch in SKIPPED_BRANCHES:
        return 0

    status = BranchStatus(
        pull=find_pr(tree, hook.cwd, deadline, state_root(), time.time()),
        ahead=tree.ahead,
        dirty=tree.dirty,
    )
    if is_quiet(status):
        return 0

    emit(build_banner(tree.branch, status), hook.event_name, CURSOR_STOP_EVENT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
