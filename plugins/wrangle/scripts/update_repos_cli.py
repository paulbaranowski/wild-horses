#!/usr/bin/env python3
"""update-git-repos CLI.

Maintains ~/.config/wild-horses/wrangle/repos.json and runs
`git pull --ff-only` against each configured repo. All subcommands print
JSON on stdout; non-zero exit means the command itself failed to run
(not a per-repo error — those are reported inside the JSON).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "wild-horses" / "wrangle"
CONFIG_PATH = CONFIG_DIR / "repos.json"

NOISE_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".tox", ".cache", "target", "dist", "build", ".next"}

VALID_DIRTY_ACTIONS = ("ask", "skip", "stash")

# pull-all fans repos out across threads (the work is network/subprocess-bound,
# so the GIL isn't the bottleneck). Capped so a large config doesn't spawn
# dozens of simultaneous `git` processes.
MAX_PULL_WORKERS = 8

# Every git call is bounded by this, so one slow or unreachable remote can't
# hang the (parallel) batch forever. Generous enough for a large first fetch;
# override with WRANGLE_GIT_TIMEOUT (seconds) for unusually large repos.
GIT_TIMEOUT_SECONDS = 120.0

# Synthetic return code for a git call we killed on timeout. Matches the
# convention of timeout(1) / coreutils so it reads the same as babysit-pr's
# `timeout 600 ...` handling.
GIT_TIMEOUT_RC = 124


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
    """Per-call git timeout, overridable via WRANGLE_GIT_TIMEOUT (seconds)."""
    raw = os.environ.get("WRANGLE_GIT_TIMEOUT")
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


def git(cwd: Path, *args: str) -> tuple[int, str, str]:
    """Run a git command. Returns (rc, stdout, stderr) with both trimmed.

    Bounded by git_timeout() and run with prompts disabled (git_env), so one
    slow/unreachable/auth-needing remote can't hang the batch. A timeout
    surfaces as rc=GIT_TIMEOUT_RC with a marker on stderr; callers map that to
    a `timed-out` status.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True,
            env=git_env(),
            timeout=git_timeout(),
        )
    except subprocess.TimeoutExpired:
        return GIT_TIMEOUT_RC, "", "timed out"
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def is_git_repo(path: Path) -> bool:
    if not path.exists():
        return False
    rc, _, _ = git(path, "rev-parse", "--git-dir")
    return rc == 0


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
      missing, not-a-repo, wrong-branch, dirty, ready
    """
    p = Path(repo_path).expanduser()
    out: dict = {"path": str(p), "branch": branch}

    if not p.exists():
        out["status"] = "missing"
        return out
    if not is_git_repo(p):
        out["status"] = "not-a-repo"
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
    """Pull one repo. Always runs `git pull --ff-only origin <branch>`.

    A `pulled` result always carries a one-line `stat` (git's `--shortstat`) so
    callers can report what landed. `verbose=True` (the default, used by
    pull-one) additionally includes the full `git pull` stdout — the per-file
    listing — as `output`. `verbose=False` (used by pull-all) omits `output`:
    one diffstat line per repo is fine, but a full per-file listing for every
    repo overflows tool-output buffers on real-world configs.
    """
    p = Path(repo_path).expanduser()
    out: dict = {"path": str(p), "branch": branch}

    stashed = False
    if stash:
        # -u stashes untracked too, in case the user wants a clean state.
        rc, sout, serr = git(p, "stash", "push", "-u", "-m", "update-git-repos auto-stash")
        if rc != 0:
            out["status"] = "stash-failed"
            out["error"] = serr or sout
            return out
        stashed = "No local changes to save" not in sout

    # HEAD before the pull, so we can diff it against the new tip to report
    # exactly what landed. Captured even when the rev-parse fails (unborn
    # HEAD) — the stat is then simply omitted below.
    rc_before, before_sha, _ = git(p, "rev-parse", "HEAD")

    rc, pout, perr = git(p, "pull", "--ff-only", "origin", branch)
    if rc != 0:
        if rc == GIT_TIMEOUT_RC:
            out["status"] = "timed-out"
            out["error"] = f"git pull exceeded the {git_timeout():.0f}s timeout"
        else:
            out["status"] = "pull-failed"
            out["error"] = (perr or pout).strip()
        if stashed:
            # Try to restore their work so we don't strand it.
            git(p, "stash", "pop")
        return out

    if "Already up to date" in pout or "Already up-to-date" in pout:
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
            out["output"] = pout

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

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
