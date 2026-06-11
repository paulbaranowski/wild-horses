#!/usr/bin/env python3
"""update-git-repos CLI.

Maintains ~/.config/wild-horses/update-git-repos/repos.json and fetches
`origin/<branch>` then fast-forwards with `git merge --ff-only` against the
tracking ref for each configured repo. All subcommands print
JSON on stdout; non-zero exit means the command itself failed to run
(not a per-repo error — those are reported inside the JSON).
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "wild-horses" / "update-git-repos"
CONFIG_PATH = CONFIG_DIR / "repos.json"

NOISE_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".tox", ".cache", "target", "dist", "build", ".next"}

VALID_DIRTY_ACTIONS = ("ask", "skip", "stash")

# pull-all fans repos out across threads (the work is network/subprocess-bound,
# so the GIL isn't the bottleneck). Capped so a large config doesn't spawn
# dozens of simultaneous `git` processes.
MAX_PULL_WORKERS = 8

# Every git call is bounded by this, so one slow or unreachable remote can't
# hang the (parallel) batch forever. Generous enough for a large first fetch;
# override with UPDATE_GIT_REPOS_TIMEOUT (seconds) for unusually large repos.
GIT_TIMEOUT_SECONDS = 300.0

# Synthetic return code for a git call we killed on timeout. Matches the
# convention of timeout(1) / coreutils so it reads the same as babysit-pr's
# `timeout 600 ...` handling.
GIT_TIMEOUT_RC = 124

# After a timeout we SIGTERM the whole git process group (so `git fetch` /
# `git index-pack` get a chance to delete their own tmp_pack_* files), wait this
# long for it to exit, then SIGKILL anything still alive. See _terminate_group.
GIT_TERM_GRACE_SECONDS = 3.0

# pull-all/pull-one refuse to fetch a repo when its filesystem has less than
# this much free space — a near-full disk is exactly where a giant fetch can't
# finish, gets killed, and leaves a tmp_pack_* behind, so re-runs just pile on
# more garbage. Override with UPDATE_GIT_REPOS_MIN_FREE_GB (gigabytes).
MIN_FREE_GB_DEFAULT = 5.0


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"repos": []}
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as e:
        sys.stderr.write(f"ERROR: corrupt config at {CONFIG_PATH}: {e}\n")
        sys.exit(3)
    if not isinstance(data, dict) or "repos" not in data or not isinstance(data["repos"], list):
        sys.stderr.write(f"ERROR: config missing 'repos' list at {CONFIG_PATH}\n")
        sys.exit(3)
    da = data.get("default_dirty_action")
    if da is not None and da not in VALID_DIRTY_ACTIONS:
        sys.stderr.write(
            f"ERROR: invalid default_dirty_action {da!r} in {CONFIG_PATH}; "
            f"expected one of {', '.join(VALID_DIRTY_ACTIONS)}\n"
        )
        sys.exit(3)
    for i, repo in enumerate(data["repos"]):
        if (
            not isinstance(repo, dict)
            or not isinstance(repo.get("path"), str) or not repo["path"]
            or not isinstance(repo.get("branch"), str) or not repo["branch"]
        ):
            sys.stderr.write(
                f"ERROR: invalid repo entry at index {i} in {CONFIG_PATH}; "
                "expected {'path': non-empty str, 'branch': non-empty str}\n"
            )
            sys.exit(3)
        rda = repo.get("dirty_action")
        if rda is not None and rda not in VALID_DIRTY_ACTIONS:
            sys.stderr.write(
                f"ERROR: invalid dirty_action {rda!r} at index {i} in {CONFIG_PATH}; "
                f"expected one of {', '.join(VALID_DIRTY_ACTIONS)}\n"
            )
            sys.exit(3)
    return data


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_config(cfg: dict) -> None:
    write_atomic(CONFIG_PATH, json.dumps(cfg, indent=2) + "\n")


def git_timeout() -> float:
    """Per-call git timeout, overridable via UPDATE_GIT_REPOS_TIMEOUT (seconds)."""
    raw = os.environ.get("UPDATE_GIT_REPOS_TIMEOUT")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return GIT_TIMEOUT_SECONDS


def git_env() -> dict[str, str]:
    """Env that makes git fail fast instead of blocking on a prompt.

    A repo whose remote needs credentials, or an SSH host with an unknown key,
    would otherwise hang `subprocess.run` waiting on stdin that never arrives —
    and in the parallel pull-all a single such repo wedges the whole pool. We
    only *setdefault* GIT_SSH_COMMAND so we never clobber a user's existing one
    (identity files, ports, etc.); GIT_TERMINAL_PROMPT is the main lever for
    https remotes and is always forced off.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    return env


def _terminate_group(proc: subprocess.Popen) -> None:
    """Kill the entire process group of a timed-out git call, then reap it.

    This is the fix for the disk-fill runaway. `subprocess` only ever signals
    the *direct* child, but `git pull` forks `git fetch`, which forks
    `git index-pack`. A plain proc.kill() leaves those grandchildren orphaned
    and still writing multi-GB tmp_pack_* files to a disk that's already too
    full for them to finish — so the pull reports `timed-out` while the real
    work grinds on for hours and re-runs pile on more orphans.

    git ran with start_new_session=True, so it's the leader of its own process
    group. We SIGTERM the whole group first — git's own signal handlers then
    delete their in-progress tmp packs — wait a short grace period, then SIGKILL
    anything still alive. communicate() after each signal reaps the child and
    closes the pipes (the grandchildren inherit them, so this also unblocks once
    they die).
    """
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        return  # already gone

    for sig, grace in ((signal.SIGTERM, GIT_TERM_GRACE_SECONDS), (signal.SIGKILL, GIT_TERM_GRACE_SECONDS)):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            return  # whole group exited between signals
        try:
            proc.communicate(timeout=grace)
            return  # group is gone and reaped
        except subprocess.TimeoutExpired:
            continue  # escalate to the next signal


# Every live `git` subprocess, so a fatal signal (or an unexpected exit) can
# tear down their process groups instead of orphaning detached git sessions.
# Without this, the _terminate_group cleanup only ever runs on the *timeout*
# path inside a living parent: because git runs with start_new_session=True it
# is shielded from the terminal's Ctrl-C, so a SIGINT/SIGTERM to this CLI (or an
# OOM that we *can* still catch as a signal) would kill the parent and leave
# `git index-pack` writing multi-GB tmp_pack_* files to the disk forever. Guarded
# by a lock because pull-all runs git from a thread pool.
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: set[subprocess.Popen] = set()

# Set true the instant fatal teardown begins. Once set, git() refuses to spawn a
# new detached session: without this gate the teardown snapshot (taken before the
# grace sleep) could miss a `git pull` a worker starts *during* that sleep, and
# os._exit would then strand it — the exact orphan teardown exists to prevent.
_TEARING_DOWN = False

# Blocked around git()'s spawn+register critical section so a fatal signal can't
# land between creating the detached git session and recording it in _INFLIGHT
# (which would hide it from teardown). Blocking also stops a same-thread handler
# (pull-one runs git on the main thread) from re-entering _INFLIGHT_LOCK and
# self-deadlocking, since threading.Lock is not reentrant.
_FATAL_SIGNALS = frozenset({signal.SIGINT, signal.SIGTERM})


def _killpg_quietly(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, OSError):
        pass  # group already gone


def _kill_inflight_groups() -> None:
    """SIGTERM then SIGKILL every in-flight git process group.

    Batched (one shared grace period, not per-proc) so tearing down a full
    pull-all fan-out on Ctrl-C stays fast. Unlike _terminate_group this does NOT
    communicate()/reap — the owning worker thread (or interpreter shutdown) does
    that; we only need the groups *dead* so they stop writing tmp_pack_* files.
    Reaping here would also race the worker's own communicate() on the same proc.
    """
    with _INFLIGHT_LOCK:
        procs = list(_INFLIGHT)
    pgids = []
    for proc in procs:
        try:
            pgids.append(os.getpgid(proc.pid))
        except (ProcessLookupError, OSError):
            pass  # already exited
    if not pgids:
        return
    for pgid in pgids:
        _killpg_quietly(pgid, signal.SIGTERM)
    time.sleep(GIT_TERM_GRACE_SECONDS)
    for pgid in pgids:
        _killpg_quietly(pgid, signal.SIGKILL)


def _on_fatal_signal(signum: int, frame) -> None:
    """Tear down in-flight git groups, then exit so we don't strand orphans."""
    del frame
    # Close the gate FIRST, before snapshotting _INFLIGHT below. Any worker that
    # reaches git()'s under-lock check after this point sees teardown in progress
    # and aborts its spawn instead of starting a session we'd miss.
    global _TEARING_DOWN
    _TEARING_DOWN = True
    _kill_inflight_groups()
    # os._exit skips atexit/finally — the groups are already handled, and we must
    # not run more Python from a signal handler than necessary. 128+signum is the
    # shell convention for "killed by signal N".
    os._exit(128 + signum)


def install_signal_handlers() -> None:
    """Trap SIGINT/SIGTERM (and register an atexit backstop) so any abnormal
    exit kills in-flight git groups. Must run on the main thread."""
    signal.signal(signal.SIGINT, _on_fatal_signal)
    signal.signal(signal.SIGTERM, _on_fatal_signal)
    atexit.register(_kill_inflight_groups)


def git(cwd: Path, *args: str) -> tuple[int, str, str]:
    """Run a git command. Returns (rc, stdout, stderr) with both trimmed.

    Bounded by git_timeout() and run with prompts disabled (git_env), so one
    slow/unreachable/auth-needing remote can't hang the batch. A timeout
    surfaces as rc=GIT_TIMEOUT_RC with a marker on stderr; callers map that to
    a `timed-out` status.

    git runs in its own session (start_new_session=True) so that on timeout we
    can signal the *whole* process group — see _terminate_group for why killing
    only the direct child isn't enough. The proc is tracked in _INFLIGHT for the
    duration so a fatal signal can tear its group down too (see
    _kill_inflight_groups).

    Spawn+register is made atomic against fatal signals by blocking SIGINT/SIGTERM
    around it (pthread_sigmask) and re-checking _TEARING_DOWN under the lock. This
    closes a TOCTOU race: _kill_inflight_groups snapshots _INFLIGHT then sleeps
    through the grace period, so a session spawned during that window would be
    invisible to teardown and orphaned by os._exit. If teardown has begun by the
    time we hold the lock, we kill the just-spawned group ourselves and abort.
    """
    # Block fatal signals so the handler can't fire between Popen and the gate
    # check below (which would let an untracked session escape), and so a
    # same-thread handler can't re-enter the non-reentrant _INFLIGHT_LOCK.
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
                # Teardown started while we were spawning; the snapshot already
                # ran without us. Kill this group ourselves so it can't orphan.
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
    """Free-space floor below which we refuse to fetch, overridable via
    UPDATE_GIT_REPOS_MIN_FREE_GB (gigabytes)."""
    raw = os.environ.get("UPDATE_GIT_REPOS_MIN_FREE_GB")
    gb = MIN_FREE_GB_DEFAULT
    if raw:
        try:
            gb = float(raw)
        except ValueError:
            pass
    return int(gb * 1024 ** 3)


def free_bytes(path: Path) -> int | None:
    """Free bytes on the filesystem holding `path` (or its nearest existing
    parent). Returns None if it can't be determined."""
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
    normal checkout, after which git refuses every work-tree operation
    ('fatal: this operation must be run in a work tree'). We detect the
    contradiction between two queries: `--is-bare-repository` reads the core.bare
    flag (true), while `--git-dir` reports the on-disk layout — a real checkout
    still resolves to a '.git' subdir. A genuine bare repo returns '.', so it is
    correctly excluded and left untouched.
    """
    rc, bare, _ = git(path, "rev-parse", "--is-bare-repository")
    if rc != 0 or bare.strip() != "true":
        return False
    rc, gitdir, _ = git(path, "rev-parse", "--git-dir")
    if rc != 0:
        return False
    # Path(...).name gets the final component cross-platform: git may emit
    # backslash separators for --git-dir on Windows, which a "/"-only split
    # would miss. A genuine bare repo returns ".", whose .name is "" (not
    # ".git"), so it stays correctly excluded.
    return Path(gitdir.strip()).name == ".git"


def detect_default_branch(repo_path: Path) -> str:
    """Best-effort default branch: origin/HEAD -> current branch -> 'main'."""
    rc, out, _ = git(repo_path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if rc == 0 and out.startswith("origin/"):
        return out.split("/", 1)[1]
    rc, out, _ = git(repo_path, "branch", "--show-current")
    if rc == 0 and out:
        return out
    return "main"


def resolve_path(s: str) -> Path:
    return Path(s).expanduser().resolve()


def repo_status(repo_path: str, branch: str) -> dict:
    """Inspect a repo without mutating it.

    Returns a dict with at minimum {path, branch, status}. Status is one of:
      missing, not-a-repo, bare-misconfig, wrong-branch, dirty, ready
    """
    p = Path(repo_path).expanduser()
    out: dict = {"path": str(p), "branch": branch}

    if not p.exists():
        out["status"] = "missing"
        return out
    if not is_git_repo(p):
        out["status"] = "not-a-repo"
        return out

    # A real working tree with a stray core.bare=true must be caught here, before
    # `git status` (the dirty check below) fatals with 'must be run in a work
    # tree' and the failure surfaces as an opaque downstream pull-failed.
    if is_misconfigured_bare(p):
        out["status"] = "bare-misconfig"
        out["error"] = (
            "core.bare=true is set on a real working tree; every work-tree "
            "operation will fail until it is unset (git config core.bare false)."
        )
        return out

    rc, current, _ = git(p, "branch", "--show-current")
    out["current_branch"] = current if (rc == 0 and current) else "(detached)"

    # Dirty = staged or unstaged tracked-file changes. Untracked files don't
    # block `git pull --ff-only` so we ignore them here.
    rc, dirty, _ = git(p, "status", "--porcelain", "--untracked-files=no")
    is_dirty = bool(dirty)
    out["dirty"] = is_dirty

    if out["current_branch"] != branch:
        out["status"] = "wrong-branch"
        return out
    if is_dirty:
        out["status"] = "dirty"
        return out

    out["status"] = "ready"
    return out


def pull_repo(repo_path: str, branch: str, *, stash: bool = False, verbose: bool = True) -> dict:
    """Pull one repo: fetch one ref, then `git merge --ff-only` against the
    stable tracking ref refs/remotes/origin/<branch> (never FETCH_HEAD).

    Splitting `git pull` into an explicit fetch + named-ref merge makes the
    fast-forward immune to the transient "Cannot fast-forward to multiple
    branches" race that FETCH_HEAD corruption (a concurrent fetch in a sibling
    worktree) triggers — see the inline comments on the fetch/merge core.

    A `pulled` result always carries a one-line `stat` (git's `--shortstat`) so
    callers can report what landed. `verbose=True` (the default, used by
    pull-one) additionally includes the full `git merge --stat` stdout — the
    per-file listing — as `output`. `verbose=False` (used by pull-all) omits
    `output`: one diffstat line per repo is fine, but a full per-file listing
    for every repo overflows tool-output buffers on real-world configs.
    """
    p = Path(repo_path).expanduser()
    out: dict = {"path": str(p), "branch": branch}

    # Refuse to fetch when the disk is near-full: that's exactly where a large
    # fetch can't complete, gets killed, and leaves a tmp_pack_* behind, so a
    # re-run would just add more orphaned garbage. Bail before touching anything
    # (no stash, no fetch) so the repo is left exactly as we found it.
    free = free_bytes(p)
    floor = min_free_bytes()
    if free is not None and free < floor:
        out["status"] = "low-disk"
        out["free_gb"] = round(free / 1024 ** 3, 2)
        out["min_free_gb"] = round(floor / 1024 ** 3, 2)
        out["error"] = (
            f"only {out['free_gb']} GB free (need >= {out['min_free_gb']} GB); "
            "skipped to avoid leaving a partial pack. Free disk space, then re-run."
        )
        return out

    stashed = False
    if stash:
        # -u stashes untracked too, in case the user wants a clean state.
        rc, sout, serr = git(p, "stash", "push", "-u", "-m", "update-git-repos auto-stash")
        if rc != 0:
            out["status"] = "stash-failed"
            out["error"] = serr or sout
            return out
        stashed = "No local changes to save" not in sout

    # HEAD before the fetch, so we can both detect "nothing moved"
    # (before_sha == after_sha) and diff the old tip against the new one to
    # report exactly what landed. Captured even when rev-parse fails (unborn
    # HEAD) — before_sha is then empty and the stat is simply omitted below.
    rc_before, before_sha, _ = git(p, "rev-parse", "HEAD")

    # Two explicit steps instead of one `git pull`, to dodge the transient
    # "Cannot fast-forward to multiple branches" race. `git pull` fast-forwards
    # against FETCH_HEAD — a single file in the *shared* common git dir, visible
    # to every linked worktree, with no per-write lock; a concurrent fetch (an
    # emdash worktree, a sibling fetch) can leave `main` listed twice as
    # for-merge and pull's ff step then refuses, even on a cleanly
    # fast-forwardable repo. So:

    # 1. Fetch exactly one ref into its tracking ref. The leading '+'
    #    force-updates refs/remotes/origin/<branch> (a tracking ref is meant to
    #    mirror the remote); git takes a per-ref lock, so a concurrent fetch of
    #    the same ref is serialized, never corrupted. This is the network step —
    #    a timeout or failure here is the remote's (or the path's) fault.
    rc, fout, ferr = git(p, "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    if rc != 0:
        if rc == GIT_TIMEOUT_RC:
            out["status"] = "timed-out"
            out["error"] = f"git fetch exceeded the {git_timeout():.0f}s timeout"
        else:
            out["status"] = "pull-failed"
            # Some fetch failures print the `fatal:` line to stdout, not stderr,
            # so fall back to stdout to keep `error` non-empty (the contract).
            out["error"] = (ferr or fout).strip()
        if stashed:
            # Try to restore their work so we don't strand it.
            git(p, "stash", "pop")
        return out

    # 2. Fast-forward against the stable tracking ref, NEVER FETCH_HEAD. The
    #    merge reads refs/remotes/origin/<branch>, a named ref protected by
    #    git's per-ref lock, so a racing fetch elsewhere can't corrupt the merge
    #    target. A non-zero rc here is now a genuine non-fast-forward (diverged
    #    history), correctly reported as pull-failed. `--stat` (verbose only)
    #    gives the per-file `output`; pull-all omits it to bound its output.
    target = f"refs/remotes/origin/{branch}"
    merge_args = ("merge", "--ff-only", "--stat", target) if verbose else ("merge", "--ff-only", target)
    rc, mout, merr = git(p, *merge_args)
    if rc != 0:
        out["status"] = "pull-failed"
        out["error"] = (merr or mout).strip()
        if stashed:
            git(p, "stash", "pop")
        return out

    # HEAD after the merge. up-to-date iff the fast-forward moved nothing —
    # detected by SHA compare instead of grepping merge stdout for "Already up
    # to date", which is more robust and i18n-proof (git localizes that line).
    rc_after, after_sha, _ = git(p, "rev-parse", "HEAD")

    if rc_before == 0 and rc_after == 0 and before_sha == after_sha:
        out["status"] = "up-to-date"
    else:
        out["status"] = "pulled"
        # Compact one-line diffstat of what the fast-forward brought in. Unlike
        # the full `output` (per-file listing, verbose-only), this is bounded to
        # a single line, so pull-all includes it too — it's the whole point of
        # showing "what actually pulled something".
        if rc_before == 0 and before_sha:
            rc_stat, stat, _ = git(p, "diff", "--shortstat", before_sha, "HEAD")
            if rc_stat == 0 and stat:
                out["stat"] = stat
        if verbose:
            out["output"] = mout

    if stashed:
        prc, ppout, pperr = git(p, "stash", "pop")
        if prc != 0:
            out["status"] = "pulled-with-pop-conflict"
            # `git stash pop` writes conflict notices to stdout, not stderr;
            # fall back so the message isn't lost.
            out["pop_error"] = pperr or ppout

    return out


# --- subcommands ---

def cmd_bootstrap_discover(args: argparse.Namespace) -> None:
    root = resolve_path(args.root)
    if not root.is_dir():
        sys.stderr.write(f"ERROR: root is not a directory: {root}\n")
        sys.exit(2)

    cfg = load_config()
    known = {resolve_path(r["path"]) for r in cfg["repos"]}

    found: list[dict] = []
    for dirpath, dirnames, _ in os.walk(root):
        # Skip noise dirs in-place so os.walk doesn't descend into them.
        dirnames[:] = [d for d in dirnames if d not in NOISE_DIRS]

        if ".git" in dirnames:
            repo = Path(dirpath).resolve()
            dirnames[:] = []  # don't descend into a found repo's subdirs
            branch = detect_default_branch(repo)
            found.append({
                "path": str(repo),
                "default_branch": branch,
                "in_config": repo in known,
            })

    found.sort(key=lambda r: r["path"])
    print(json.dumps({"root": str(root), "repos": found}, indent=2))


def cmd_add(args: argparse.Namespace) -> None:
    p = resolve_path(args.path)
    if not is_git_repo(p):
        sys.stderr.write(f"ERROR: not a git repo: {p}\n")
        sys.exit(2)
    branch = args.branch or detect_default_branch(p)

    cfg = load_config()
    for r in cfg["repos"]:
        if resolve_path(r["path"]) == p:
            r["path"] = str(p)
            r["branch"] = branch
            save_config(cfg)
            print(json.dumps({"updated": {"path": str(p), "branch": branch}}, indent=2))
            return

    cfg["repos"].append({"path": str(p), "branch": branch})
    cfg["repos"].sort(key=lambda r: r["path"])
    save_config(cfg)
    print(json.dumps({"added": {"path": str(p), "branch": branch}}, indent=2))


def cmd_remove(args: argparse.Namespace) -> None:
    p = resolve_path(args.path)
    cfg = load_config()
    before = len(cfg["repos"])
    cfg["repos"] = [r for r in cfg["repos"] if resolve_path(r["path"]) != p]
    if len(cfg["repos"]) == before:
        sys.stderr.write(f"ERROR: not in config: {p}\n")
        sys.exit(2)
    save_config(cfg)
    print(json.dumps({"removed": str(p)}, indent=2))


def cmd_set_action(args: argparse.Namespace) -> None:
    """Set the default dirty-repo action — global (no --repo) or per-repo.

    Global: writes top-level default_dirty_action. Per-repo: writes the entry's
    dirty_action, or removes it when the action is the `inherit` sentinel.
    """
    cfg = load_config()

    if args.repo is None:
        if args.action == "inherit":
            sys.stderr.write("ERROR: 'inherit' is only valid with --repo\n")
            sys.exit(2)
        cfg["default_dirty_action"] = args.action
        save_config(cfg)
        print(json.dumps({"default_dirty_action": args.action}, indent=2))
        return

    p = resolve_path(args.repo)
    entry = next((r for r in cfg["repos"] if resolve_path(r["path"]) == p), None)
    if not entry:
        sys.stderr.write(f"ERROR: not in config: {p}\n")
        sys.exit(2)

    if args.action == "inherit":
        entry.pop("dirty_action", None)
        save_config(cfg)
        print(json.dumps({"repo": entry["path"], "dirty_action": None}, indent=2))
        return

    entry["dirty_action"] = args.action
    save_config(cfg)
    print(json.dumps({"repo": entry["path"], "dirty_action": args.action}, indent=2))


def cmd_list(args: argparse.Namespace) -> None:
    del args
    cfg = load_config()
    cfg["config_path"] = str(CONFIG_PATH)
    # Display-only: cmd_list never saves, so this injected default must not persist.
    cfg.setdefault("default_dirty_action", "ask")
    print(json.dumps(cfg, indent=2))


def resolve_dirty_action(cfg: dict, entry: dict) -> str:
    """Effective dirty action for one repo: per-repo override -> global default -> 'ask'.

    A per-repo `dirty_action` always wins (including an explicit "ask"). Otherwise
    the top-level `default_dirty_action` applies; absent/invalid falls back to "ask".
    """
    a = entry.get("dirty_action")
    if a in VALID_DIRTY_ACTIONS:
        return a
    d = cfg.get("default_dirty_action")
    return d if d in VALID_DIRTY_ACTIONS else "ask"


def _status_then_pull(work: tuple[dict, str]) -> dict:
    """One repo's pull-all unit of work: status-check, then act per resolved action.

    Self-contained (reads/writes only its own repo) so pull-all can run many of
    these concurrently without shared state. For a dirty repo the pre-resolved
    `action` decides: `skip` -> report a `skipped` status untouched; `stash` ->
    inline stash-pull-pop (same path as `pull-one --stash`); `ask` -> report
    `dirty` so the skill can prompt (unchanged behavior).
    """
    entry, action = work
    s = repo_status(entry["path"], entry["branch"])
    if s["status"] == "ready":
        return pull_repo(entry["path"], entry["branch"], stash=False, verbose=False)
    if s["status"] == "dirty":
        if action == "skip":
            s["status"] = "skipped"
            s["reason"] = "dirty"
            return s
        if action == "stash":
            return pull_repo(entry["path"], entry["branch"], stash=True, verbose=False)
        s["effective_action"] = "ask"
        return s
    return s


def cmd_pull_all(args: argparse.Namespace) -> None:
    del args
    cfg = load_config()
    repos = cfg["repos"]
    if not repos:
        print(json.dumps({
            "empty": True,
            "config_path": str(CONFIG_PATH),
            "hint": "run `bootstrap-discover --root DIR` then `add PATH` for each",
        }, indent=2))
        return

    # Resolve each repo's dirty action up front (cmd_pull_all holds cfg), then
    # fan out. `executor.map` yields results in submission order, so output
    # stays in config order regardless of which pull finishes first — the
    # step-5 summary relies on that ordering.
    work = [(r, resolve_dirty_action(cfg, r)) for r in repos]
    with ThreadPoolExecutor(max_workers=min(MAX_PULL_WORKERS, len(repos))) as ex:
        results = list(ex.map(_status_then_pull, work))

    # Collapse already-current repos into a bare count rather than a full entry
    # each. They need no per-repo action in the skill flow, so emitting one
    # object apiece just burns the reading agent's context — 18 up-to-date repos
    # would be ~90 lines of indented JSON the agent reads only to render a single
    # summary line. `results` keeps only repos that need attention or changed.
    up_to_date = sum(1 for r in results if r["status"] == "up-to-date")
    results = [r for r in results if r["status"] != "up-to-date"]

    print(json.dumps({"results": results, "up_to_date": up_to_date}, indent=2))


def cmd_pull_one(args: argparse.Namespace) -> None:
    p = resolve_path(args.path)
    cfg = load_config()
    entry = next((r for r in cfg["repos"] if resolve_path(r["path"]) == p), None)
    if not entry:
        sys.stderr.write(f"ERROR: not in config: {p}\n")
        sys.exit(2)
    status = repo_status(entry["path"], entry["branch"])
    # Mirror pull-all's safety gate: only pull when ready. The single exception
    # is dirty + explicit --stash, which is exactly what the dirty-repo prompt
    # in the skill flow asks for.
    if status["status"] != "ready" and not (status["status"] == "dirty" and args.stash):
        print(json.dumps(status, indent=2))
        return
    print(json.dumps(pull_repo(entry["path"], entry["branch"], stash=args.stash), indent=2))


def cmd_fix_bare(args: argparse.Namespace) -> None:
    """Unset a stray core.bare=true on a real working tree, then re-report status.

    Guarded twice: the path must be in config (like pull-one/remove), and it must
    still read as misconfigured-bare at call time — so we never flip core.bare on
    a genuinely bare repo or one already fixed. After unsetting, re-run the status
    check so the caller knows whether a follow-up pull-one is safe.
    """
    p = resolve_path(args.path)
    cfg = load_config()
    entry = next((r for r in cfg["repos"] if resolve_path(r["path"]) == p), None)
    if not entry:
        sys.stderr.write(f"ERROR: not in config: {p}\n")
        sys.exit(2)

    if not is_misconfigured_bare(p):
        sys.stderr.write(
            f"ERROR: {p} is not a misconfigured-bare repo (core.bare is not a "
            "stray flag on a real working tree); refusing to touch it.\n"
        )
        sys.exit(2)

    rc, out, err = git(p, "config", "core.bare", "false")
    if rc != 0:
        print(json.dumps({
            "path": str(p),
            "action": "unset-bare",
            "status": "fix-failed",
            "error": (err or out).strip() or "git config core.bare false failed",
        }, indent=2))
        return

    status_after = repo_status(entry["path"], entry["branch"])
    result: dict = {
        "path": str(p),
        "action": "unset-bare",
        "branch": entry["branch"],
        "status_after": status_after["status"],
    }
    # Presence check, not truthiness: repo_status() sets current_branch for every
    # real repo (detached HEAD included, as the truthy "(detached)"), omitting the
    # key only on its early returns (missing / not-a-repo / bare-misconfig). We
    # want to mirror exactly those omissions, so test for the key, not its value.
    if "current_branch" in status_after:
        result["current_branch"] = status_after["current_branch"]
    # status_after may still be a problem state — notably `bare-misconfig` again,
    # if the worktree tooling that causes this re-set core.bare between our unset
    # and this re-check (the exact recurrence this feature exists for). Carry its
    # error through so the caller isn't left blind to why the repo still won't pull.
    if status_after.get("error"):
        result["error"] = status_after["error"]
    print(json.dumps(result, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="update_repos_cli.py", description="Pull all configured git repos.")
    sp = parser.add_subparsers(dest="cmd", required=True)

    p_disc = sp.add_parser("bootstrap-discover", help="Walk a directory for git repos.")
    p_disc.add_argument("--root", required=True, help="Directory to scan for .git folders.")
    p_disc.set_defaults(func=cmd_bootstrap_discover)

    p_add = sp.add_parser("add", help="Add (or update) one repo in the config.")
    p_add.add_argument("path")
    p_add.add_argument("--branch", help="Branch to pull. Defaults to origin/HEAD if detectable.")
    p_add.set_defaults(func=cmd_add)

    p_rm = sp.add_parser("remove", help="Remove one repo from the config.")
    p_rm.add_argument("path")
    p_rm.set_defaults(func=cmd_remove)

    p_sa = sp.add_parser("set-action", help="Set the default dirty-repo action (global, or per-repo with --repo).")
    p_sa.add_argument(
        "action",
        choices=[*VALID_DIRTY_ACTIONS, "inherit"],
        help="ask|skip|stash. 'inherit' (only valid with --repo) clears a per-repo override.",
    )
    p_sa.add_argument("--repo", help="Set this repo's override instead of the global default.")
    p_sa.set_defaults(func=cmd_set_action)

    p_ls = sp.add_parser("list", help="Print the current config as JSON.")
    p_ls.set_defaults(func=cmd_list)

    p_pa = sp.add_parser("pull-all", help="Pull every clean+on-branch repo; report the rest.")
    p_pa.set_defaults(func=cmd_pull_all)

    p_po = sp.add_parser("pull-one", help="Pull a single repo, optionally stash-pull-pop.")
    p_po.add_argument("path")
    p_po.add_argument("--stash", action="store_true", help="Stash before pulling, pop after.")
    p_po.set_defaults(func=cmd_pull_one)

    p_fb = sp.add_parser("fix-bare", help="Unset a stray core.bare=true on a real working tree.")
    p_fb.add_argument("path")
    p_fb.set_defaults(func=cmd_fix_bare)

    return parser


def main() -> None:
    install_signal_handlers()
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
