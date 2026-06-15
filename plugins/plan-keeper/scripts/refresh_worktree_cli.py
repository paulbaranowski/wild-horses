#!/usr/bin/env python3
"""plan-keeper worktree-refresh CLI.

Fast-forwards the *current* worktree onto its base branch (`main`/`master`)
before plan-do hands a plan off to an execution engine — but only when the
worktree is *untouched* (clean tree AND no commits ahead of base), so the
update is always a conflict-free fast-forward that can't lose work.

This is a single-repo tool: it refreshes whatever repo `--path` points at
(default: cwd). It deliberately does NOT touch the update-git-repos config or
pull any other repo — syncing every configured repo is update-git-repos's job.

The git plumbing (timeout-bounded `git()` with whole-process-group teardown,
the disk-free floor, and the race-safe `fetch one ref into its tracking ref`
+ `merge --ff-only <tracking-ref>` pattern) is copied from update-git-repos's
`update_repos_cli.py`, where the inline comments explain why each guard exists.
plan-keeper and update-git-repos are separate plugins with separate install
paths, so the mechanics are copied here rather than imported across plugins.

The one subcommand, `refresh`, prints a JSON result on stdout. Non-zero exit
means the command itself failed to run (bad args); a per-repo outcome (dirty,
ahead, fetch-failed, ...) is reported inside the JSON with exit 0.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

# Every git call is bounded by this so one slow/unreachable remote can't hang
# the refresh. Override with PLAN_KEEPER_REFRESH_TIMEOUT (seconds).
GIT_TIMEOUT_SECONDS = 300.0

# Synthetic rc for a git call we killed on timeout (matches timeout(1)).
GIT_TIMEOUT_RC = 124

# Grace between SIGTERM and SIGKILL when tearing down a timed-out git group.
GIT_TERM_GRACE_SECONDS = 3.0

# Refuse to fetch when free space is under this floor: a near-full disk is
# where a giant fetch can't finish, gets killed, and strands a tmp_pack_*.
# Override with PLAN_KEEPER_REFRESH_MIN_FREE_GB (gigabytes).
MIN_FREE_GB_DEFAULT = 5.0


def git_timeout() -> float:
    raw = os.environ.get("PLAN_KEEPER_REFRESH_TIMEOUT")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return GIT_TIMEOUT_SECONDS


def git_env() -> dict[str, str]:
    """Env that makes git fail fast instead of blocking on a credential prompt.

    A remote needing credentials, or an unknown SSH host key, would otherwise
    hang on stdin that never arrives. We only *setdefault* GIT_SSH_COMMAND so a
    user's existing one (identity files, ports) is never clobbered.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    return env


def _terminate_group(proc: subprocess.Popen) -> None:
    """SIGTERM then SIGKILL the whole process group of a timed-out git call.

    `subprocess` only signals the direct child, but `git fetch` forks
    `git index-pack`; a plain kill orphans that grandchild, which keeps writing
    multi-GB tmp_pack_* files to an already-full disk. git ran with
    start_new_session=True, so it leads its own group — SIGTERM lets git's
    handlers delete in-progress packs, then SIGKILL anything still alive.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        return
    for sig, grace in ((signal.SIGTERM, GIT_TERM_GRACE_SECONDS), (signal.SIGKILL, GIT_TERM_GRACE_SECONDS)):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            return
        try:
            proc.communicate(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


# Track every live git subprocess so a fatal signal can tear down its group
# instead of orphaning a detached `git index-pack`. Guarded by a lock for
# symmetry with the spawn/teardown critical sections below.
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: set[subprocess.Popen] = set()
_TEARING_DOWN = False
_FATAL_SIGNALS = frozenset({signal.SIGINT, signal.SIGTERM})


def _killpg_quietly(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, OSError):
        pass


def _kill_inflight_groups() -> None:
    """SIGTERM then SIGKILL every in-flight git group (shared grace period).

    Does NOT reap — the owning call (or interpreter shutdown) does that; we only
    need the groups dead so they stop writing tmp_pack_* files.
    """
    with _INFLIGHT_LOCK:
        procs = list(_INFLIGHT)
    pgids = []
    for proc in procs:
        try:
            pgids.append(os.getpgid(proc.pid))
        except (ProcessLookupError, OSError):
            pass
    if not pgids:
        return
    for pgid in pgids:
        _killpg_quietly(pgid, signal.SIGTERM)
    time.sleep(GIT_TERM_GRACE_SECONDS)
    for pgid in pgids:
        _killpg_quietly(pgid, signal.SIGKILL)


def _on_fatal_signal(signum: int, frame) -> None:
    del frame
    global _TEARING_DOWN
    _TEARING_DOWN = True
    _kill_inflight_groups()
    os._exit(128 + signum)


def install_signal_handlers() -> None:
    """Trap SIGINT/SIGTERM (+ atexit backstop) so any abnormal exit kills
    in-flight git groups. Must run on the main thread."""
    signal.signal(signal.SIGINT, _on_fatal_signal)
    signal.signal(signal.SIGTERM, _on_fatal_signal)
    atexit.register(_kill_inflight_groups)


def git(cwd: Path, *args: str) -> tuple[int, str, str]:
    """Run a git command; return (rc, stdout, stderr), both trimmed.

    Bounded by git_timeout() with prompts disabled (git_env). git runs in its
    own session so a timeout can signal the whole process group (see
    _terminate_group). Spawn+register is made atomic against fatal signals by
    blocking SIGINT/SIGTERM around it and re-checking _TEARING_DOWN under the
    lock, closing the TOCTOU window where a session spawned during teardown's
    grace sleep would be invisible and orphaned.
    """
    signal.pthread_sigmask(signal.SIG_BLOCK, _FATAL_SIGNALS)
    try:
        try:
            proc = subprocess.Popen(
                ["git", "-C", str(cwd), *args],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=git_env(),
                start_new_session=True,
            )
        except FileNotFoundError:
            return 127, "", "git not found"
        with _INFLIGHT_LOCK:
            if _TEARING_DOWN:
                _terminate_group(proc)
                return GIT_TIMEOUT_RC, "", "aborted during shutdown"
            _INFLIGHT.add(proc)
    finally:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, _FATAL_SIGNALS)
    try:
        out, err = proc.communicate(timeout=git_timeout())
    except subprocess.TimeoutExpired:
        _terminate_group(proc)
        return GIT_TIMEOUT_RC, "", "timed out"
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.discard(proc)
    return proc.returncode, out.strip(), err.strip()


def min_free_bytes() -> int:
    raw = os.environ.get("PLAN_KEEPER_REFRESH_MIN_FREE_GB")
    gb = MIN_FREE_GB_DEFAULT
    if raw:
        try:
            gb = float(raw)
        except ValueError:
            pass
    return int(gb * 1024 ** 3)


def free_bytes(path: Path) -> int | None:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def is_git_repo(path: Path) -> bool:
    if not path.exists():
        return False
    rc, _, _ = git(path, "rev-parse", "--git-dir")
    return rc == 0


def is_misconfigured_bare(path: Path) -> bool:
    """True for a real working tree wrongly flagged core.bare=true.

    Some worktree tooling (emdash/graft) intermittently sets core.bare=true on a
    normal checkout, after which git refuses every work-tree op. We detect the
    contradiction: --is-bare-repository reads the flag (true) while --git-dir
    still resolves to a '.git' subdir for a real checkout. A genuine bare repo
    returns '.', so it is correctly excluded.
    """
    rc, bare, _ = git(path, "rev-parse", "--is-bare-repository")
    if rc != 0 or bare.strip() != "true":
        return False
    rc, gitdir, _ = git(path, "rev-parse", "--git-dir")
    if rc != 0:
        return False
    return Path(gitdir.strip()).name == ".git"


def detect_base_branch(repo_path: Path) -> str:
    """Best-effort base branch: origin/HEAD -> 'main' if it exists -> 'master'."""
    rc, out, _ = git(repo_path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if rc == 0 and out.startswith("origin/"):
        return out.split("/", 1)[1]
    rc, _, _ = git(repo_path, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main")
    if rc == 0:
        return "main"
    return "master"


def resolve_path(s: str) -> Path:
    return Path(s).expanduser().resolve()


def refresh_worktree(repo_path: str, base: str | None) -> dict:
    """Fast-forward the current branch onto origin/<base> when untouched.

    Status (in `status`) is one of:
      not-a-repo, bare-misconfig, detached-head, dirty, low-disk, fetch-failed,
      timed-out, ahead (has local commits — left as-is), up-to-date, refreshed,
      ff-failed.
    Only `refreshed` mutates the repo (a pure fast-forward); every other status
    leaves the worktree exactly as found.
    """
    p = Path(repo_path).expanduser()
    out: dict = {"path": str(p)}

    if not is_git_repo(p):
        out["status"] = "not-a-repo"
        return out

    # Must precede any work-tree query: a stray core.bare=true makes git fatal on
    # `status`/`merge` with an opaque error, so name it explicitly instead.
    if is_misconfigured_bare(p):
        out["status"] = "bare-misconfig"
        out["error"] = (
            "core.bare=true is set on a real working tree; every work-tree "
            "operation fails until it is unset (git config core.bare false)."
        )
        return out

    rc, current, _ = git(p, "branch", "--show-current")
    if rc != 0 or not current:
        # Detached HEAD has no branch to fast-forward; leave it untouched.
        out["status"] = "detached-head"
        return out
    out["current_branch"] = current

    base = base or detect_base_branch(p)
    out["base"] = base

    # Dirty = staged/unstaged tracked changes. Untracked files don't block a
    # fast-forward, so (matching update-git-repos) they're ignored.
    rc, dirty, _ = git(p, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        out["status"] = "dirty"
        return out

    # Refuse to fetch on a near-full disk so a killed fetch can't strand a
    # partial pack. Bail before touching anything.
    free = free_bytes(p)
    floor = min_free_bytes()
    if free is not None and free < floor:
        out["status"] = "low-disk"
        out["free_gb"] = round(free / 1024 ** 3, 2)
        out["min_free_gb"] = round(floor / 1024 ** 3, 2)
        out["error"] = (
            f"only {out['free_gb']} GB free (need >= {out['min_free_gb']} GB); "
            "skipped to avoid leaving a partial pack."
        )
        return out

    # Fetch exactly the base ref into its tracking ref. The leading '+'
    # force-updates refs/remotes/origin/<base> under a per-ref lock, so a
    # concurrent fetch of the same ref (a sibling worktree) is serialized, never
    # corrupted — and the ff below reads this named ref, never FETCH_HEAD.
    rc, fout, ferr = git(p, "fetch", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}")
    if rc != 0:
        if rc == GIT_TIMEOUT_RC:
            out["status"] = "timed-out"
            out["error"] = f"git fetch exceeded the {git_timeout():.0f}s timeout"
        else:
            out["status"] = "fetch-failed"
            out["error"] = (ferr or fout).strip()
        return out

    target = f"refs/remotes/origin/{base}"
    # Commits on the current branch not on base. >0 means the branch is NOT
    # untouched — it has local work — so we never fast-forward (that would
    # require a rebase, which could conflict). Report `ahead` and leave it.
    rc, ahead, _ = git(p, "rev-list", "--count", f"{target}..HEAD")
    if rc != 0:
        out["status"] = "ff-failed"
        out["error"] = "could not count commits ahead of base"
        return out
    if ahead != "0":
        out["status"] = "ahead"
        out["ahead"] = int(ahead)
        return out

    # No local commits → measure how far base moved. 0 means nothing to do.
    rc, behind, _ = git(p, "rev-list", "--count", f"HEAD..{target}")
    if rc == 0 and behind == "0":
        out["status"] = "up-to-date"
        return out

    # ahead == 0 and base advanced → `merge --ff-only` is a pure fast-forward.
    # --ff-only is a belt-and-suspenders guard: it refuses anything that isn't a
    # fast-forward, so even a race that added a commit between the count and here
    # fails safe (ff-failed) rather than creating a merge commit.
    rc_before, before_sha, _ = git(p, "rev-parse", "HEAD")
    rc, mout, merr = git(p, "merge", "--ff-only", target)
    if rc != 0:
        out["status"] = "ff-failed"
        out["error"] = (merr or mout).strip()
        return out

    out["status"] = "refreshed"
    if behind.isdigit():
        out["behind"] = int(behind)
    rc_after, after_sha, _ = git(p, "rev-parse", "HEAD")
    if rc_before == 0 and before_sha and rc_after == 0:
        rc_stat, stat, _ = git(p, "diff", "--shortstat", before_sha, after_sha)
        if rc_stat == 0 and stat:
            out["stat"] = stat
    return out


def cmd_refresh(args: argparse.Namespace) -> None:
    path = str(resolve_path(args.path)) if args.path else os.getcwd()
    print(json.dumps(refresh_worktree(path, args.base), indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refresh_worktree_cli.py",
        description="Fast-forward the current worktree onto its base branch when untouched.",
    )
    sp = parser.add_subparsers(dest="cmd", required=True)
    p_ref = sp.add_parser("refresh", help="Fast-forward the worktree onto origin/<base> if untouched.")
    p_ref.add_argument("--path", default=None, help="Repo path (default: current directory).")
    p_ref.add_argument("--base", default=None, help="Base branch (default: auto-detect main/master).")
    p_ref.set_defaults(func=cmd_refresh)
    return parser


def main(argv: list[str] | None = None) -> None:
    install_signal_handlers()
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
