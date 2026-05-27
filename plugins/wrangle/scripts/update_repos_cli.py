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
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "wild-horses" / "wrangle"
CONFIG_PATH = CONFIG_DIR / "repos.json"

NOISE_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".tox", ".cache", "target", "dist", "build", ".next"}


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


def git(cwd: Path, *args: str) -> tuple[int, str, str]:
    """Run a git command. Returns (rc, stdout, stderr) with both trimmed."""
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True,
    )
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

    `verbose=True` (the default, used by pull-one) includes the full `git pull`
    stdout as `output`. `verbose=False` (used by pull-all) omits it — batch
    callers only consume `status`, and including a diff stat for every repo
    overflows tool-output buffers on real-world configs.
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

    rc, pout, perr = git(p, "pull", "--ff-only", "origin", branch)
    if rc != 0:
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


def cmd_list(args: argparse.Namespace) -> None:
    del args
    cfg = load_config()
    cfg["config_path"] = str(CONFIG_PATH)
    print(json.dumps(cfg, indent=2))


def cmd_pull_all(args: argparse.Namespace) -> None:
    del args
    cfg = load_config()
    if not cfg["repos"]:
        print(json.dumps({
            "empty": True,
            "config_path": str(CONFIG_PATH),
            "hint": "run `bootstrap-discover --root DIR` then `add PATH` for each",
        }, indent=2))
        return

    results: list[dict] = []
    for r in cfg["repos"]:
        s = repo_status(r["path"], r["branch"])
        if s["status"] == "ready":
            s = pull_repo(r["path"], r["branch"], stash=False, verbose=False)
        results.append(s)

    print(json.dumps({"results": results}, indent=2))


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
