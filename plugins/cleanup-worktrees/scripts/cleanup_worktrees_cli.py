#!/usr/bin/env python3
"""cleanup-worktrees CLI.

Discovers git worktrees under configured roots, classifies each as cleanable or
skipped against a fixed taxonomy (merged PR, upstream gone, merged into the
default branch, stale, or — for skips — dirty / locked / unpushed), and removes
the selected ones safely (re-validate, never ``--force``, prune the orphaned
branch). Config lives at ~/.config/wild-horses/cleanup-worktrees/config.json.

Every subcommand prints JSON on stdout. Exit code 0 means the CLI itself
succeeded — per-worktree failures are reported inside the JSON, not via the exit
code. A non-zero exit means a CLI-level error (bad path, corrupt config, a path
outside $HOME).

The classifier never deletes work that is not pushed or merged: dirty, locked,
and unpushed worktrees short-circuit to ``skipped`` and are excluded from the
cleanable set. ``remove`` re-validates every path before touching it, because
state can change between a scan and the removal.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

CONFIG_DIR = Path.home() / ".config" / "wild-horses" / "cleanup-worktrees"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_STALE_DAYS = 30

# Default depth when a parent entry omits one: only immediate children are
# inspected as repos/checkouts.
DEFAULT_PARENT_DEPTH = 1

# Every subprocess call is bounded so one hung `gh` (network) or a pathological
# `du` can't wedge a scan. Generous enough for a large `du` on a multi-GB tree.
CMD_TIMEOUT_SECONDS = 120.0

# Sizing fans out: `du` on a 4 GB tree takes seconds, and a scan may face dozens
# of cleanable worktrees. Capped so we don't spawn an unbounded herd of `du`.
MAX_SIZE_WORKERS = 8

# Synthetic return code for a call we killed on timeout (matches coreutils
# `timeout(1)`), so a timeout reads distinctly from a normal non-zero exit.
CMD_TIMEOUT_RC = 124

CleanReason = Literal["upstream-gone", "pr-merged", "pr-closed", "merged-to-default", "stale"]
SkipReason = Literal["dirty", "locked", "unpushed"]

# Reasons a worktree is excluded from the cleanable set entirely, before any
# cleanable reason is considered. First match in this order wins the skip.
SKIP_ORDER: tuple[SkipReason, ...] = ("dirty", "locked", "unpushed")


# --- typed results -----------------------------------------------------------


@dataclass(frozen=True)
class RunResult:
    """Outcome of one subprocess call. `timed_out` distinguishes a killed call
    from an ordinary non-zero exit so callers can report it specifically."""

    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def timed_out(self) -> bool:
        return self.rc == CMD_TIMEOUT_RC


@dataclass
class Worktree:
    """One worktree as reported by `git worktree list --porcelain`.

    `is_main` marks the primary checkout of its object-store group (always the
    first entry git lists); it is never cleanable. `main_path` is that primary
    checkout's path — every mutating git call runs with `-C main_path`, because
    `git worktree remove` refuses to operate on its own working directory.
    """

    path: Path
    main_path: Path
    branch: Optional[str]
    head: Optional[str]
    is_bare: bool
    is_main: bool
    locked: bool
    lock_reason: Optional[str]


@dataclass
class ScanError:
    path: str
    error: str
    detail: str = ""


@dataclass
class Classification:
    """Result of classifying one worktree.

    Exactly one of `clean_reason` / `skip_reason` is set, or both are None when
    the worktree matched no reason and is excluded from output entirely.
    """

    clean_reason: Optional[CleanReason] = None
    skip_reason: Optional[SkipReason] = None
    detail: str = ""
    errors: list[ScanError] = field(default_factory=list)


# --- config ------------------------------------------------------------------


def default_config() -> dict:
    return {"repos": [], "parents": [], "stale_days": DEFAULT_STALE_DAYS, "last_confirmed_at": None}


def load_config() -> dict:
    """Read and validate the config, exiting non-zero (3) on corruption.

    A missing file is not an error — it yields the empty default so a first run
    can `config add-*` into it.
    """
    if not CONFIG_PATH.exists():
        return default_config()
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as e:
        sys.stderr.write(f"ERROR: corrupt config at {CONFIG_PATH}: {e}\n")
        sys.exit(3)
    if not isinstance(data, dict):
        sys.stderr.write(f"ERROR: config root must be an object at {CONFIG_PATH}\n")
        sys.exit(3)
    _validate_path_list(data, "repos", require_path=True)
    _validate_path_list(data, "parents", require_path=True)
    data.setdefault("repos", [])
    data.setdefault("parents", [])
    sd = data.get("stale_days", DEFAULT_STALE_DAYS)
    if not isinstance(sd, int) or isinstance(sd, bool) or sd < 1:
        sys.stderr.write(f"ERROR: stale_days must be a positive integer in {CONFIG_PATH}\n")
        sys.exit(3)
    data["stale_days"] = sd
    data.setdefault("last_confirmed_at", None)
    return data


def _validate_path_list(data: dict, key: str, *, require_path: bool) -> None:
    items = data.get(key, [])
    if not isinstance(items, list):
        sys.stderr.write(f"ERROR: '{key}' must be a list in {CONFIG_PATH}\n")
        sys.exit(3)
    for i, item in enumerate(items):
        if not isinstance(item, dict) or (require_path and not _nonempty_str(item.get("path"))):
            sys.stderr.write(
                f"ERROR: invalid {key} entry at index {i} in {CONFIG_PATH}; "
                "expected {'path': non-empty str}\n"
            )
            sys.exit(3)
        depth = item.get("depth")
        if depth is not None and (not isinstance(depth, int) or isinstance(depth, bool) or depth < 1):
            sys.stderr.write(
                f"ERROR: invalid depth at {key}[{i}] in {CONFIG_PATH}; expected a positive integer\n"
            )
            sys.exit(3)


def _nonempty_str(v: object) -> bool:
    return isinstance(v, str) and bool(v)


def write_atomic(path: Path, content: str) -> None:
    """Write via a unique tmp file + fsync + os.replace so a crash never leaves a
    half-written config that the next run would choke on.

    The tmp name is per-call (mkstemp) rather than a shared `<name>.tmp`, so two
    `config` commands running at once can't trample each other's tmp file and
    publish the wrong payload. The tmp is removed on any failure before
    os.replace; after a successful replace it no longer exists (it was renamed).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent, text=True)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def save_config(cfg: dict) -> None:
    write_atomic(CONFIG_PATH, json.dumps(cfg, indent=2) + "\n")


# --- subprocess --------------------------------------------------------------


def run(args: list[str], *, cwd: Optional[Path] = None, timeout: float = CMD_TIMEOUT_SECONDS) -> RunResult:
    """Run a command with prompts disabled and a hard timeout.

    git/gh prompts are forced off (GIT_TERMINAL_PROMPT, batch-mode ssh) so a repo
    needing credentials fails fast instead of blocking on stdin that never comes.
    A timeout surfaces as rc=CMD_TIMEOUT_RC rather than raising, so a single slow
    remote degrades one worktree's classification instead of aborting the scan.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError:
        return RunResult(127, "", f"{args[0]} not found")
    except subprocess.TimeoutExpired:
        return RunResult(CMD_TIMEOUT_RC, "", f"{args[0]} timed out after {timeout:.0f}s")
    return RunResult(proc.returncode, proc.stdout.strip(), proc.stderr.strip())


def git(cwd: Path, *args: str) -> RunResult:
    return run(["git", "-C", str(cwd), *args])


# --- discovery ---------------------------------------------------------------


def resolve_path(s: str) -> Path:
    return Path(s).expanduser().resolve()


def is_git_dir(path: Path) -> bool:
    """A path is a checkout if it has a `.git` entry (dir for the main worktree,
    file for a linked one), or is itself a bare repo (HEAD + objects/)."""
    if (path / ".git").exists():
        return True
    return (path / "HEAD").exists() and (path / "objects").is_dir()


def parse_worktree_list(porcelain: str) -> list[dict]:
    """Parse `git worktree list --porcelain` into one dict per worktree.

    Blocks are blank-line separated. git always lists the primary worktree
    first, so the caller can mark index 0 as main. Recognized keys: worktree
    (path), HEAD (sha), branch (refs/heads/x), bare, detached, locked [reason].
    """
    blocks: list[dict] = []
    current: dict = {}
    for line in porcelain.splitlines():
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
            continue
        key, _, rest = line.partition(" ")
        if key == "worktree":
            current = {"path": rest}
        elif key == "HEAD":
            current["head"] = rest
        elif key == "branch":
            current["branch"] = rest
        elif key == "bare":
            current["bare"] = True
        elif key == "detached":
            current["detached"] = True
        elif key == "locked":
            current["locked"] = True
            current["lock_reason"] = rest
    if current:
        blocks.append(current)
    return blocks


def _short_branch(ref: Optional[str]) -> Optional[str]:
    if ref and ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    return None


def worktrees_for_group(anchor: Path) -> list[Worktree]:
    """All worktrees sharing `anchor`'s object store, as Worktree objects.

    `anchor` may be any checkout in the group (or a bare repo); git resolves the
    whole group from the shared common dir. Returns [] if the path isn't a repo.
    """
    res = git(anchor, "worktree", "list", "--porcelain")
    if not res.ok:
        return []
    blocks = parse_worktree_list(res.out)
    if not blocks:
        return []
    main_path = resolve_path(blocks[0]["path"])
    out: list[Worktree] = []
    for i, b in enumerate(blocks):
        out.append(
            Worktree(
                path=resolve_path(b["path"]),
                main_path=main_path,
                branch=_short_branch(b.get("branch")),
                head=b.get("head"),
                is_bare=bool(b.get("bare")),
                is_main=(i == 0),
                locked=bool(b.get("locked")),
                lock_reason=(b.get("lock_reason") or None),
            )
        )
    return out


def iter_parent_checkouts(parent: Path, depth: int) -> list[Path]:
    """Directories under `parent` (up to `depth` levels deep) that are git
    checkouts. Stops descending into a directory the moment it is one, so the
    nested `.git` is never walked."""
    found: list[Path] = []

    def walk(d: Path, remaining: int) -> None:
        if is_git_dir(d):
            found.append(d)
            return  # don't descend into a checkout's internals
        if remaining <= 0:
            return
        try:
            children = sorted(p for p in d.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            walk(child, remaining - 1)

    if not parent.is_dir():
        return found
    try:
        children = sorted(p for p in parent.iterdir() if p.is_dir())
    except OSError:
        return found
    for child in children:
        walk(child, depth - 1)
    return found


def discover_worktrees(cfg: dict) -> tuple[list[Worktree], list[ScanError]]:
    """Every candidate worktree under the configured roots, deduped by canonical
    path. Excludes bare repos, the main worktree of each group, and anything
    outside $HOME. The companion ScanError list carries unreadable roots.
    """
    home = Path.home().resolve()
    errors: list[ScanError] = []
    anchors: list[Path] = []

    for repo in cfg.get("repos", []):
        anchors.append(resolve_path(repo["path"]))
    for parent in cfg.get("parents", []):
        p = resolve_path(parent["path"])
        depth = parent.get("depth") or DEFAULT_PARENT_DEPTH
        if not p.is_dir():
            errors.append(ScanError(path=str(p), error="parent-missing", detail="not a directory"))
            continue
        anchors.extend(iter_parent_checkouts(p, depth))

    by_path: dict[Path, Worktree] = {}
    seen_groups: set[Path] = set()
    for anchor in anchors:
        if not anchor.exists():
            errors.append(ScanError(path=str(anchor), error="missing", detail="path does not exist"))
            continue
        group = worktrees_for_group(anchor)
        if not group:
            continue
        if group[0].main_path in seen_groups:
            continue  # same object-store group reached from another root
        seen_groups.add(group[0].main_path)
        for wt in group:
            if wt.is_main or wt.is_bare:
                continue
            if home not in wt.path.parents and wt.path != home:
                errors.append(ScanError(path=str(wt.path), error="outside-home", detail="refused: not under $HOME"))
                continue
            by_path.setdefault(wt.path, wt)
    return list(by_path.values()), errors


# --- classification ----------------------------------------------------------


def detect_default_branch(main_path: Path) -> str:
    """origin/HEAD -> main -> master -> the main worktree's current branch."""
    res = git(main_path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if res.ok and res.out.startswith("origin/"):
        return res.out.split("/", 1)[1]
    for candidate in ("main", "master"):
        if git(main_path, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{candidate}").ok:
            return candidate
    cur = git(main_path, "branch", "--show-current")
    return cur.out if cur.ok and cur.out else "main"


@dataclass
class GhAvailability:
    """gh is queried once-per-(group, branch) and cached. If gh is missing or
    unauthenticated we set `unavailable` so we stop retrying and record it once.
    """

    unavailable: bool = False
    reason: str = ""


def _looks_like_auth_failure(stderr: str) -> bool:
    """Heuristic: does gh's stderr indicate a global auth problem (vs a
    repo-specific failure)? Matches gh's "not logged in" / "authentication"
    phrasing without coupling to an exact message."""
    low = stderr.lower()
    return any(s in low for s in ("logged in", "authentication", "gh auth login", "not authenticated"))


def gh_prs_for(wt: Worktree, cache: dict[tuple[Path, str], list[dict]], gh: GhAvailability) -> Optional[list[dict]]:
    """PRs whose head is `wt.branch`, run from inside the worktree so `gh` reads
    the right repo from `origin`. Cached per (group, branch). Returns None when
    gh is unavailable (caller treats PR signals as unknown)."""
    if gh.unavailable or not wt.branch:
        return None
    key = (wt.main_path, wt.branch)
    if key in cache:
        return cache[key]
    res = run(
        ["gh", "pr", "list", "--head", wt.branch, "--state", "all",
         "--json", "number,state,mergedAt,closedAt,headRefOid", "--limit", "10"],
        cwd=wt.path,
    )
    if not res.ok:
        if res.rc == 127:
            gh.unavailable = True
            gh.reason = "gh not installed"
        elif _looks_like_auth_failure(res.err):
            # A global auth/login failure recurs for every branch; mark gh
            # unavailable once so we stop re-spawning it (and re-erroring) for
            # the rest of the scan. A repo-specific failure (e.g. no GitHub
            # remote) is NOT global, so it stays a per-worktree gh-failed error.
            gh.unavailable = True
            gh.reason = "gh not authenticated"
        return None
    try:
        prs = json.loads(res.out) if res.out else []
    except json.JSONDecodeError:
        return None
    cache[key] = prs
    return prs


def _date_part(iso: Optional[str]) -> str:
    return iso.split("T", 1)[0] if iso else ""


def _unpushed_count(wt: Worktree, merged_pr_head: Optional[str] = None) -> Optional[int]:
    """Number of commits on this worktree preserved by NEITHER a remote-tracking
    ref NOR a merged PR's head commit.

    This is the data-safety predicate behind the `unpushed` skip and the
    branch-deletion guard: a branch carrying commits that exist nowhere durable
    would lose them if its ref were deleted. `git rev-list <rev> --not --remotes
    [<merged_pr_head>]` answers exactly that. Unlike `@{upstream}..HEAD`, it does
    NOT silently no-op when the branch has no upstream or its upstream was
    deleted (`gone`) — the cases where unique commits would otherwise slip past
    the guard and be force-deleted.

    `merged_pr_head` is the head commit of a MERGED PR for this branch. A
    squash/rebase merge with the remote branch deleted leaves the original
    commits on no remote ref, but they ARE reachable from the merged PR's head,
    so excluding it stops the gate from misreading merged work as unpushed —
    while still protecting any commit made AFTER the merge (it is reachable from
    neither). Returns None when it cannot be computed (a rev that won't resolve);
    callers treat that conservatively as "cannot confirm pushed" and skip.
    """
    rev = wt.branch or wt.head or "HEAD"
    if merged_pr_head:
        res = git(wt.path, "rev-list", "--count", rev, "--not", "--remotes", merged_pr_head)
        if res.ok and res.out.isdigit():
            return int(res.out)
        # merged head not in the local object store (or a bad rev): fall through
        # to the remotes-only count, which is the conservative (more-protective)
        # answer.
    res = git(wt.path, "rev-list", "--count", rev, "--not", "--remotes")
    if res.ok and res.out.isdigit():
        return int(res.out)
    return None


def _first_merged_head(prs: Optional[list[dict]]) -> Optional[str]:
    """headRefOid of the first MERGED PR in `prs`, or None."""
    if not prs:
        return None
    merged = next((p for p in prs if p.get("state") == "MERGED"), None)
    return merged.get("headRefOid") if merged else None


def _unpushed_classification(count: Optional[int], *, since_scan: bool = False) -> Optional[Classification]:
    """Map an `_unpushed_count` result to a skip Classification (or None when 0).
    `since_scan` tunes the wording for the remove-time re-validation path."""
    suffix = " since scan" if since_scan else ""
    if count is None:
        return Classification(skip_reason="unpushed", detail=f"could not verify push state{suffix}")
    if count > 0:
        return Classification(skip_reason="unpushed",
                              detail=f"{count} unpushed commit{'s' if count != 1 else ''}{suffix}")
    return None


def _classify_dirty_or_locked(wt: Worktree) -> Optional[Classification]:
    """The two cheap, gh-free skips. Checked before anything that needs network."""
    status = git(wt.path, "status", "--porcelain")
    if status.ok and status.out:
        n = len(status.out.splitlines())
        return Classification(skip_reason="dirty", detail=f"{n} uncommitted file{'s' if n != 1 else ''}")
    if wt.locked:
        return Classification(skip_reason="locked", detail=wt.lock_reason or "")
    return None


def _classify_clean(
    wt: Worktree,
    *,
    default_branch: str,
    stale_cutoff: float,
    gh_cache: dict[tuple[Path, str], list[dict]],
    gh: GhAvailability,
) -> Classification:
    """Cleanable checks, in priority order; first hit wins. Returns an empty
    Classification (both reasons None) when nothing matches."""
    errors: list[ScanError] = []

    if wt.branch and _upstream_gone(wt):
        return Classification(clean_reason="upstream-gone")

    prs = gh_prs_for(wt, gh_cache, gh)
    if prs is None and wt.branch and not gh.unavailable:
        errors.append(ScanError(path=str(wt.path), error="gh-failed", detail="PR lookup failed"))
    elif prs is not None:
        merged = next((p for p in prs if p.get("state") == "MERGED"), None)
        if merged:
            return Classification(clean_reason="pr-merged",
                                  detail=f"PR #{merged['number']} merged {_date_part(merged.get('mergedAt'))}".strip(),
                                  errors=errors)
        closed = next((p for p in prs if p.get("state") == "CLOSED"), None)
        if closed:
            return Classification(clean_reason="pr-closed",
                                  detail=f"PR #{closed['number']} closed {_date_part(closed.get('closedAt'))}".strip(),
                                  errors=errors)

    if wt.head and _merged_to_default(wt, default_branch):
        return Classification(clean_reason="merged-to-default", errors=errors)

    stale = _stale_detail(wt, stale_cutoff)
    if stale is not None:
        return Classification(clean_reason="stale", detail=stale, errors=errors)

    return Classification(errors=errors)


def _upstream_gone(wt: Worktree) -> bool:
    res = git(wt.path, "for-each-ref", "--format=%(upstream:track)", f"refs/heads/{wt.branch}")
    return res.ok and "gone" in res.out


def _merged_to_default(wt: Worktree, default_branch: str) -> bool:
    target = f"refs/remotes/origin/{default_branch}"
    if not git(wt.path, "rev-parse", "--verify", "--quiet", target).ok:
        return False
    return git(wt.path, "merge-base", "--is-ancestor", wt.head or "HEAD", target).ok


def _stale_detail(wt: Worktree, stale_cutoff: float) -> Optional[str]:
    res = git(wt.path, "log", "-n", "1", "--format=%ct", "HEAD")
    if not res.ok or not res.out.isdigit():
        return None
    ts = int(res.out)
    if ts >= stale_cutoff:
        return None
    day = time.strftime("%Y-%m-%d", time.gmtime(ts))
    return f"last commit {day}"


def classify_worktree(
    wt: Worktree,
    *,
    default_branch: str,
    stale_cutoff: float,
    gh_cache: dict[tuple[Path, str], list[dict]],
    gh: GhAvailability,
) -> Classification:
    """Classify one worktree: dirty/locked skip, then the data-safety unpushed
    gate, then the cleanable reasons.

    The unpushed gate is the subtle part. A branch whose commits all live on a
    remote-tracking ref is trivially safe. When some commits are on no remote, we
    consult the PR signal: a MERGED PR's head commit covers squash/rebase-merged
    work (the remote branch was deleted but the work is incorporated), so those
    commits don't count as unpushed — but a commit made AFTER the merge is
    covered by nothing and keeps the worktree protected. Only when commits are
    preserved nowhere durable do we skip as `unpushed`.
    """
    dl = _classify_dirty_or_locked(wt)
    if dl is not None:
        return dl

    base = _unpushed_count(wt)
    if base == 0:
        # Everything is already on a remote — definitely safe; assign a reason.
        return _classify_clean(
            wt, default_branch=default_branch, stale_cutoff=stale_cutoff, gh_cache=gh_cache, gh=gh
        )

    # Some commits are on no remote. They are only safe if a MERGED PR's head
    # covers them (squash/rebase merge + deleted branch). Consult gh.
    prs = gh_prs_for(wt, gh_cache, gh)
    merged_head = _first_merged_head(prs)
    if merged_head:
        refined = _unpushed_count(wt, merged_pr_head=merged_head)
        if refined == 0:
            return _classify_clean(
                wt, default_branch=default_branch, stale_cutoff=stale_cutoff, gh_cache=gh_cache, gh=gh
            )
        return _unpushed_classification(refined) or Classification()

    # No merged PR covers these commits → genuine unpushed work. Surface a
    # gh-failed note if the lookup itself failed (we then can't rule a merge in).
    cls = _unpushed_classification(base) or Classification()
    if prs is None and wt.branch and not gh.unavailable:
        cls.errors.append(ScanError(path=str(wt.path), error="gh-failed", detail="PR lookup failed"))
    return cls


# --- sizing ------------------------------------------------------------------


def dir_size_bytes(path: Path) -> int:
    """Allocated size of a worktree via POSIX `du -sk` (1024-byte blocks).

    `-k` is portable across BSD (macOS) and GNU `du`, unlike GNU's `-b`. Block
    size is what actually frees on removal, so it is the honest "reclaimable"
    figure. Returns 0 if `du` fails (the worktree still removes; size is cosmetic).
    """
    res = run(["du", "-sk", str(path)])
    if not res.ok:
        return 0
    first = res.out.split("\t", 1)[0].split(None, 1)[0]
    try:
        return int(first) * 1024
    except ValueError:
        return 0


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{size:.1f}{unit}" if unit != "B" else f"{n}B"
        size /= 1024
    return f"{size:.1f}T"


# --- scan --------------------------------------------------------------------


def cmd_scan(args: argparse.Namespace) -> None:
    del args
    cfg = load_config()
    now = time.time()
    stale_cutoff = now - cfg["stale_days"] * 86400

    worktrees, errors = discover_worktrees(cfg)
    gh_cache: dict[tuple[Path, str], list[dict]] = {}
    gh = GhAvailability()
    default_branch_cache: dict[Path, str] = {}

    cleanable_raw: list[tuple[Worktree, Classification]] = []
    skipped: list[dict] = []
    for wt in worktrees:
        if wt.main_path not in default_branch_cache:
            default_branch_cache[wt.main_path] = detect_default_branch(wt.main_path)
        cls = classify_worktree(
            wt,
            default_branch=default_branch_cache[wt.main_path],
            stale_cutoff=stale_cutoff,
            gh_cache=gh_cache,
            gh=gh,
        )
        errors.extend(cls.errors)
        if cls.skip_reason is not None:
            skipped.append({"path": str(wt.path), "reason": cls.skip_reason, "detail": cls.detail})
        elif cls.clean_reason is not None:
            cleanable_raw.append((wt, cls))

    cleanable = _size_and_index(cleanable_raw)
    if gh.unavailable and gh.reason:
        errors.append(ScanError(path="", error="gh-unavailable", detail=gh.reason))

    print(json.dumps({
        "scanned": len(worktrees),
        "cleanable": cleanable,
        "skipped": skipped,
        "errors": [vars(e) for e in errors],
    }, indent=2))


def _size_and_index(cleanable_raw: list[tuple[Worktree, Classification]]) -> list[dict]:
    """Size cleanable worktrees concurrently, then assign 1-based display indices
    in discovery order (stable, so the index the user picks is reproducible)."""
    if not cleanable_raw:
        return []
    with ThreadPoolExecutor(max_workers=min(MAX_SIZE_WORKERS, len(cleanable_raw))) as ex:
        sizes = list(ex.map(lambda pair: dir_size_bytes(pair[0].path), cleanable_raw))
    out: list[dict] = []
    for i, ((wt, cls), size) in enumerate(zip(cleanable_raw, sizes), start=1):
        out.append({
            "index": i,
            "path": str(wt.path),
            "repo": str(wt.main_path),
            "branch": wt.branch,
            "reason": cls.clean_reason,
            "reason_detail": cls.detail,
            "size_bytes": size,
            "size_human": human_size(size),
        })
    return out


# --- remove ------------------------------------------------------------------


def cmd_remove(args: argparse.Namespace) -> None:
    home = Path.home().resolve()
    removed: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    groups_to_prune: set[Path] = set()
    total = 0

    for raw in args.paths:
        target = resolve_path(raw)
        outcome = _remove_one(target, home, groups_to_prune)
        if outcome["kind"] == "removed":
            removed.append(outcome["entry"])
            total += outcome["entry"]["bytes_reclaimed"]
        elif outcome["kind"] == "skipped":
            skipped.append(outcome["entry"])
        else:
            errors.append(outcome["entry"])

    for main_path in groups_to_prune:
        git(main_path, "worktree", "prune")

    print(json.dumps({
        "removed": removed,
        "skipped": skipped,
        "errors": errors,
        "total_bytes_reclaimed": total,
        "total_human": human_size(total),
    }, indent=2))


def _remove_one(target: Path, home: Path, groups_to_prune: set[Path]) -> dict:
    """Re-validate and remove one worktree. Returns {kind, entry} where kind is
    'removed' | 'skipped' | 'error'. Never uses --force; a failed re-validation
    leaves the worktree untouched."""
    if home not in target.parents and target != home:
        return {"kind": "error", "entry": {"path": str(target), "error": "outside-home"}}
    if not target.exists():
        # Path vanished out-of-band (manually deleted). Its object-store group
        # can't be located from a path that no longer exists, so its stale
        # `git worktree list` admin entry isn't pruned here in isolation; a
        # sibling removal in the same group (which does prune) or a later run
        # cleans it up. `git worktree prune` is harmless to defer.
        return {"kind": "skipped", "entry": {"path": str(target), "reason": "already-gone"}}

    group = worktrees_for_group(target)
    wt = next((w for w in group if w.path == target), None)
    if wt is None:
        return {"kind": "error", "entry": {"path": str(target), "error": "not-a-worktree"}}
    if wt.is_main or wt.is_bare:
        return {"kind": "skipped", "entry": {"path": str(target), "reason": "main-worktree"}}

    # A MERGED PR's head covers squash/rebase-merged commits, so re-validation
    # and branch-pruning treat them as safe (consistent with how `scan`
    # classified the worktree). gh unavailable → None → most protective.
    merged_head = _merged_pr_head_for(wt)
    revalid = _revalidate(wt, merged_head)
    if revalid is not None:
        return {"kind": "skipped", "entry": {"path": str(target), "reason": revalid[0], "detail": revalid[1]}}

    size = dir_size_bytes(target)
    rm = git(wt.main_path, "worktree", "remove", str(target))
    if not rm.ok:
        return {"kind": "error", "entry": {"path": str(target), "error": "remove-failed", "detail": rm.err or rm.out}}

    groups_to_prune.add(wt.main_path)
    entry: dict = {"path": str(target), "branch": wt.branch, "bytes_reclaimed": size}
    if wt.branch:
        _prune_branch(wt.main_path, wt.branch, entry, merged_head)
    return {"kind": "removed", "entry": entry}


def _merged_pr_head_for(wt: Worktree) -> Optional[str]:
    """headRefOid of a MERGED PR for this worktree's branch, used at remove time
    to recognize squash/rebase-merged commits as safe (mirrors `scan`). Returns
    None when there is no merged PR, no branch, or gh is unavailable — None is
    the conservative answer (the worktree is then protected as if unpushed)."""
    if not wt.branch:
        return None
    res = run(["gh", "pr", "list", "--head", wt.branch, "--state", "merged",
               "--json", "headRefOid", "--limit", "1"], cwd=wt.path)
    if not res.ok:
        return None
    try:
        prs = json.loads(res.out) if res.out else []
    except json.JSONDecodeError:
        return None
    return prs[0]["headRefOid"] if prs and prs[0].get("headRefOid") else None


def _prune_branch(main_path: Path, branch: str, entry: dict, merged_pr_head: Optional[str]) -> None:
    """Delete the orphaned branch ref, but ONLY when every commit on it is
    preserved by a remote-tracking ref or a merged PR's head.

    The worktree (the disk hog) is already gone; the branch is just metadata. But
    `branch -D` force-deletes regardless of merge state, so deleting a branch
    that carries commits preserved nowhere durable would make those commits
    unreachable — silent data loss. This guard is belt-and-suspenders with the
    remove-time re-validation (which already skips such worktrees): if the branch
    still carries uncovered commits, keep the ref and warn instead, so the
    commits stay recoverable via `git checkout <branch>`. `merged_pr_head` lets a
    squash/rebase-merged branch (its work covered by the merged PR) be pruned.
    """
    args = ["rev-list", "--count", branch, "--not", "--remotes"]
    if merged_pr_head:
        args.append(merged_pr_head)
    unique = git(main_path, *args)
    if not unique.ok or not unique.out.isdigit():
        # Retry without the merged head (a bad/absent sha): conservative count.
        unique = git(main_path, "rev-list", "--count", branch, "--not", "--remotes")
    if not unique.ok or not unique.out.isdigit():
        entry["warning"] = "branch-prune-skipped"
        entry["warning_detail"] = "could not verify branch is fully pushed; branch kept"
        return
    if int(unique.out) > 0:
        entry["warning"] = "branch-prune-skipped"
        entry["warning_detail"] = f"{int(unique.out)} commit(s) preserved on no remote; branch kept"
        return
    prune = git(main_path, "branch", "-D", branch)
    if not prune.ok:
        entry["warning"] = "branch-prune-skipped"
        entry["warning_detail"] = prune.err or prune.out


def _revalidate(wt: Worktree, merged_pr_head: Optional[str]) -> Optional[tuple[str, str]]:
    """Re-run the skip checks at removal time (state drifts between scan and
    remove). Returns (reason, detail) if the worktree must be skipped, else None.
    `merged_pr_head` mirrors `scan`'s squash/rebase-merge allowance."""
    status = git(wt.path, "status", "--porcelain")
    if status.ok and status.out:
        n = len(status.out.splitlines())
        return ("now-dirty", f"{n} uncommitted file{'s' if n != 1 else ''} since scan")
    if wt.locked:
        return ("locked", wt.lock_reason or "")
    skip = _unpushed_classification(_unpushed_count(wt, merged_pr_head=merged_pr_head), since_scan=True)
    if skip is not None:
        return (skip.skip_reason or "unpushed", skip.detail)
    return None


# --- config subcommands ------------------------------------------------------


def _require_under_home(p: Path) -> None:
    home = Path.home().resolve()
    if home not in p.parents and p != home:
        sys.stderr.write(f"ERROR: refusing a path outside $HOME: {p}\n")
        sys.exit(2)


def cmd_config_list(args: argparse.Namespace) -> None:
    del args
    cfg = load_config()
    cfg["config_path"] = str(CONFIG_PATH)
    print(json.dumps(cfg, indent=2))


def _add_path_entry(key: str, path_str: str, extra: Optional[dict] = None) -> None:
    p = resolve_path(path_str)
    _require_under_home(p)
    cfg = load_config()
    for entry in cfg[key]:
        if resolve_path(entry["path"]) == p:
            if extra:
                entry.update(extra)
            entry["path"] = str(p)
            save_config(cfg)
            print(json.dumps({"updated": str(p)}, indent=2))
            return
    new_entry = {"path": str(p)}
    if extra:
        new_entry.update(extra)
    cfg[key].append(new_entry)
    cfg[key].sort(key=lambda e: e["path"])
    save_config(cfg)
    print(json.dumps({"added": str(p)}, indent=2))


def cmd_config_add_repo(args: argparse.Namespace) -> None:
    _add_path_entry("repos", args.path)


def cmd_config_add_parent(args: argparse.Namespace) -> None:
    # Reject a bad depth here, before it reaches disk: a saved depth < 1 would
    # make the very next load_config() exit 3, bricking the config until it is
    # hand-edited.
    if args.depth is not None and args.depth < 1:
        sys.stderr.write("ERROR: depth must be >= 1\n")
        sys.exit(2)
    extra = {"depth": args.depth} if args.depth is not None else None
    _add_path_entry("parents", args.path, extra)


def cmd_config_remove(args: argparse.Namespace) -> None:
    p = resolve_path(args.path)
    cfg = load_config()
    for key in ("repos", "parents"):
        before = len(cfg[key])
        cfg[key] = [e for e in cfg[key] if resolve_path(e["path"]) != p]
        if len(cfg[key]) != before:
            save_config(cfg)
            print(json.dumps({"removed": str(p), "kind": "repo" if key == "repos" else "parent"}, indent=2))
            return
    sys.stderr.write(f"ERROR: not in config: {p}\n")
    sys.exit(2)


def cmd_config_set_stale_days(args: argparse.Namespace) -> None:
    if args.days < 1:
        sys.stderr.write("ERROR: stale-days must be >= 1\n")
        sys.exit(2)
    cfg = load_config()
    cfg["stale_days"] = args.days
    save_config(cfg)
    print(json.dumps({"stale_days": args.days}, indent=2))


def cmd_config_confirm(args: argparse.Namespace) -> None:
    del args
    cfg = load_config()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cfg["last_confirmed_at"] = stamp
    save_config(cfg)
    print(json.dumps({"confirmed_at": stamp}, indent=2))


# --- arg parsing -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cleanup_worktrees_cli.py",
                                     description="Discover, classify, and remove cleanable git worktrees.")
    sp = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sp.add_parser("scan", help="Discover and classify every worktree under configured roots.")
    p_scan.set_defaults(func=cmd_scan)

    p_rm = sp.add_parser("remove", help="Re-validate and remove the specified worktree paths.")
    p_rm.add_argument("--paths", nargs="+", required=True, help="Canonical worktree paths to remove.")
    p_rm.set_defaults(func=cmd_remove)

    p_cfg = sp.add_parser("config", help="Inspect or edit the config.")
    csp = p_cfg.add_subparsers(dest="config_cmd", required=True)

    csp.add_parser("list", help="Print the resolved config as JSON.").set_defaults(func=cmd_config_list)

    p_ar = csp.add_parser("add-repo", help="Add a direct repo path.")
    p_ar.add_argument("path")
    p_ar.set_defaults(func=cmd_config_add_repo)

    p_ap = csp.add_parser("add-parent", help="Add a parent dir whose subdirs are auto-scanned.")
    p_ap.add_argument("path")
    p_ap.add_argument("--depth", type=int, help="Levels below the parent to descend (default 1).")
    p_ap.set_defaults(func=cmd_config_add_parent)

    p_cr = csp.add_parser("remove", help="Remove a repo or parent entry by path.")
    p_cr.add_argument("path")
    p_cr.set_defaults(func=cmd_config_remove)

    p_sd = csp.add_parser("set-stale-days", help="Set the stale threshold in days.")
    p_sd.add_argument("days", type=int)
    p_sd.set_defaults(func=cmd_config_set_stale_days)

    csp.add_parser("confirm", help="Record approval of the current resolved roots.").set_defaults(
        func=cmd_config_confirm
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
