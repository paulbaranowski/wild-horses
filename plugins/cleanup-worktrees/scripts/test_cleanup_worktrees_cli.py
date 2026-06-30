#!/usr/bin/env python3
"""Tests for cleanup_worktrees_cli.py and cleanup-worktrees-cli-allow.sh.

Stdlib-only — no pytest needed. Run from anywhere:

    python3 plugins/cleanup-worktrees/scripts/test_cleanup_worktrees_cli.py

Or via unittest discovery:

    python3 -m unittest discover -s plugins/cleanup-worktrees/scripts -p 'test_cleanup_worktrees_cli.py'

Tests invoke the CLI as a subprocess so exit codes, argparse behavior, and
stdout/stderr separation are exercised exactly as a dispatched agent would see
them. Isolation: HOME=<tmpdir> per test, so the CLI's config path
(~/.config/wild-horses/cleanup-worktrees/config.json) resolves under the tempdir
and never touches real config. git is exercised for real (no mocks); only `gh`
is stubbed, via a fake `gh` injected on PATH that reads a JSON map from $GH_STUB.

Safety note: a worktree is only ever "cleanable" when its commits already live
on a remote-tracking ref (deleting it then loses nothing). The fixtures push
such branches to the bare remote; the data-loss-protection tests deliberately
create branches with commits on NO remote and assert they are skipped, never
removed.

Mirrors plugins/update-git-repos/scripts/test_update_repos_cli.py.
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

CLI = Path(__file__).parent / "cleanup_worktrees_cli.py"
ALLOW_SCRIPT = Path(__file__).parent / "cleanup-worktrees-cli-allow.sh"

_spec = importlib.util.spec_from_file_location("cleanup_worktrees_cli", CLI)
assert _spec and _spec.loader
cleanup_worktrees_cli = importlib.util.module_from_spec(_spec)
# Register before exec so dataclass annotation resolution (PEP 563 strings) can
# find the module's namespace via sys.modules on Python 3.12+.
sys.modules["cleanup_worktrees_cli"] = cleanup_worktrees_cli
_spec.loader.exec_module(cleanup_worktrees_cli)

OLD_DATE = "2020-01-01T00:00:00"

GH_STUB = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] != ["pr", "list"]:
    sys.exit(0)
head = None
for i, a in enumerate(args):
    if a == "--head" and i + 1 < len(args):
        head = args[i + 1]
mapping = json.loads(os.environ.get("GH_STUB", "{}"))
print(json.dumps(mapping.get(head, [])))
"""

GH_STUB_FAIL = """#!/usr/bin/env python3
import sys
sys.stderr.write("gh: simulated failure\\n")
sys.exit(1)
"""

GH_STUB_AUTH = """#!/usr/bin/env python3
import sys
sys.stderr.write("You are not logged in to any GitHub hosts. Run gh auth login.\\n")
sys.exit(1)
"""


def git(cwd: Path, *args: str, env: Optional[dict] = None) -> str:
    """Run git in `cwd`, asserting success; return stripped stdout."""
    full_env = {**os.environ, **(env or {})}
    res = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True, env=full_env,
    )
    return res.stdout.strip()


class CleanupWorktreesTestCase(unittest.TestCase):
    """Each test gets a fresh $HOME and a seeded repo group under it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.config_path = self.home / ".config" / "wild-horses" / "cleanup-worktrees" / "config.json"
        self.scratch = self.home / "scratch"   # bare remotes (outside any scanned root)
        self.scratch.mkdir()
        self.work = self.home / "work"          # the repo + its worktrees live here
        self.work.mkdir()
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self._write_gh_stub(GH_STUB)
        self.gh_map: dict = {}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- harness helpers -----------------------------------------------------

    def _write_gh_stub(self, body: str) -> None:
        gh = self.bin / "gh"
        gh.write_text(body)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def env(self) -> dict:
        return {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "GH_STUB": json.dumps(self.gh_map),
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com",
        }

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["python3", str(CLI), *args],
            capture_output=True, text=True,
            env={**os.environ, **self.env()},
            timeout=60,
        )

    def scan(self) -> dict:
        res = self.run_cli("scan")
        self.assertEqual(res.returncode, 0, f"scan failed: {res.stderr}")
        return json.loads(res.stdout)

    def write_config(self, **overrides) -> None:
        cfg = {"repos": [{"path": str(self.repo)}], "parents": [], "stale_days": 30, "last_confirmed_at": None}
        cfg.update(overrides)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(cfg))

    def seed_repo(self) -> None:
        """Create a bare remote + a main clone with `main` pushed, and register
        it. Individual add_*_worktree helpers then attach worktrees."""
        self.bare = self.scratch / "origin.git"
        git(self.scratch, "init", "--bare", "-b", "main", str(self.bare), env=self.env())
        seed = self.scratch / "seed"
        git(self.scratch, "init", "-b", "main", str(seed), env=self.env())
        (seed / "README.md").write_text("seed\n")
        git(seed, "add", "README.md", env=self.env())
        git(seed, "commit", "-m", "init", env=self.env())
        git(seed, "remote", "add", "origin", str(self.bare), env=self.env())
        git(seed, "push", "origin", "main", env=self.env())

        self.repo = (self.work / "repo").resolve()
        git(self.work, "clone", "-b", "main", str(self.bare), str(self.repo), env=self.env())
        git(self.repo, "config", "user.email", "t@example.com", env=self.env())
        git(self.repo, "config", "user.name", "Test", env=self.env())
        git(self.repo, "fetch", "origin", env=self.env())
        self.main_sha = git(self.repo, "rev-parse", "HEAD", env=self.env())

    def wt_path(self, name: str) -> Path:
        return (self.work / "wts" / name).resolve()

    def add_plain_worktree(self, name: str, *, old: bool = False) -> Path:
        """A worktree on a fresh branch off main with one extra commit that is
        pushed NOWHERE (no upstream, on no remote) — i.e. it carries unique
        unpushed work. The safety classifier must treat this as `unpushed`."""
        p = self.wt_path(name)
        git(self.repo, "worktree", "add", "-b", name, str(p), self.main_sha, env=self.env())
        (p / f"{name}.txt").write_text("x\n")
        git(p, "add", f"{name}.txt", env=self.env())
        env = self.env()
        if old:
            env = {**env, "GIT_AUTHOR_DATE": OLD_DATE, "GIT_COMMITTER_DATE": OLD_DATE}
        git(p, "commit", "-m", f"work on {name}", env=env)
        return p

    def add_pushed_branch_worktree(self, name: str, *, old: bool = False) -> Path:
        """A worktree on a divergent branch whose commit IS pushed to
        origin/<name> — so it is safe to clean (deleting loses nothing). Recent
        by default; `old=True` backdates the commit so it classifies as stale."""
        pusher = self.scratch / f"pusher-{name}"
        git(self.scratch, "clone", "-b", "main", str(self.bare), str(pusher), env=self.env())
        git(pusher, "checkout", "-b", name, env=self.env())
        (pusher / f"{name}.txt").write_text("remote work\n")
        git(pusher, "add", f"{name}.txt", env=self.env())
        cenv = self.env()
        if old:
            cenv = {**cenv, "GIT_AUTHOR_DATE": OLD_DATE, "GIT_COMMITTER_DATE": OLD_DATE}
        git(pusher, "commit", "-m", f"work {name}", env=cenv)
        git(pusher, "push", "origin", name, env=self.env())
        git(self.repo, "fetch", "origin", env=self.env())
        p = self.wt_path(name)
        git(self.repo, "worktree", "add", "--track", "-b", name, str(p), f"origin/{name}", env=self.env())
        return p

    def add_squash_merged_worktree(self, name: str, *, post_commit: bool = False) -> Path:
        """A worktree whose feature commit was squash-merged into main and whose
        remote branch was then deleted — so the commit is on no remote ref, but
        IS the head of a MERGED PR. The classifier must treat it as cleanable
        (the work is incorporated), NOT as unpushed. With post_commit, an extra
        commit is made AFTER the merge: genuine unpushed work that stays skipped.
        """
        pusher = self.scratch / f"pusher-{name}"
        git(self.scratch, "clone", "-b", "main", str(self.bare), str(pusher), env=self.env())
        git(pusher, "checkout", "-b", name, env=self.env())
        (pusher / f"{name}.txt").write_text("feature work\n")
        git(pusher, "add", f"{name}.txt", env=self.env())
        git(pusher, "commit", "-m", f"feature {name}", env=self.env())
        git(pusher, "push", "origin", name, env=self.env())
        git(self.repo, "fetch", "origin", env=self.env())
        p = self.wt_path(name)
        git(self.repo, "worktree", "add", "--track", "-b", name, str(p), f"origin/{name}", env=self.env())
        c1 = git(p, "rev-parse", "HEAD", env=self.env())
        # Squash-merge into main from a separate clone, then delete the remote branch.
        sq = self.scratch / f"squash-{name}"
        git(self.scratch, "clone", "-b", "main", str(self.bare), str(sq), env=self.env())
        (sq / f"squashed-{name}.txt").write_text("squashed\n")
        git(sq, "add", f"squashed-{name}.txt", env=self.env())
        git(sq, "commit", "-m", f"squash {name} (#1)", env=self.env())
        git(sq, "push", "origin", "main", env=self.env())
        git(self.repo, "push", "origin", f":refs/heads/{name}", env=self.env())
        git(self.repo, "fetch", "--prune", "origin", env=self.env())
        if post_commit:
            (p / "post.txt").write_text("after merge\n")
            git(p, "add", "post.txt", env=self.env())
            git(p, "commit", "-m", "work after merge", env=self.env())
        self.gh_map[name] = [{"number": 1, "state": "MERGED",
                              "mergedAt": "2026-06-01T00:00:00Z", "headRefOid": c1}]
        return p

    def add_merged_to_default_worktree(self, name: str) -> Path:
        """A worktree whose tip is exactly origin/main — an ancestor of it."""
        p = self.wt_path(name)
        git(self.repo, "worktree", "add", "-b", name, str(p), self.main_sha, env=self.env())
        return p

    def add_tracking_worktree(self, name: str, *, prune_remote: bool, local_commit: bool) -> Path:
        """A worktree tracking origin/<name> (seeded at the main commit). With
        prune_remote the remote branch is deleted+pruned (-> upstream gone). With
        local_commit it gets a commit that is on no remote (unique unpushed work).
        """
        git(self.repo, "push", "origin", f"main:refs/heads/{name}", env=self.env())
        git(self.repo, "fetch", "origin", env=self.env())
        p = self.wt_path(name)
        git(self.repo, "worktree", "add", "--track", "-b", name, str(p), f"origin/{name}", env=self.env())
        if local_commit:
            (p / f"{name}.txt").write_text("y\n")
            git(p, "add", f"{name}.txt", env=self.env())
            git(p, "commit", "-m", f"local work {name}", env=self.env())
        if prune_remote:
            git(self.repo, "push", "origin", f":refs/heads/{name}", env=self.env())
            git(self.repo, "fetch", "--prune", "origin", env=self.env())
        return p

    # --- scan classification -------------------------------------------------

    def test_scan_classifies_each_reason(self) -> None:
        self.seed_repo()
        gone = self.add_tracking_worktree("gone-feature", prune_remote=True, local_commit=False)
        merged = self.add_merged_to_default_worktree("merged-feature")
        stale = self.add_pushed_branch_worktree("stale-feature", old=True)
        prm = self.add_pushed_branch_worktree("pr-merged-feature")
        prc = self.add_pushed_branch_worktree("pr-closed-feature")
        self.gh_map = {
            "pr-merged-feature": [{"number": 101, "state": "MERGED", "mergedAt": "2026-05-10T00:00:00Z"}],
            "pr-closed-feature": [{"number": 102, "state": "CLOSED", "closedAt": "2026-06-01T00:00:00Z"}],
        }
        self.write_config()

        data = self.scan()
        by_path = {c["path"]: c for c in data["cleanable"]}
        self.assertEqual(by_path[str(gone)]["reason"], "upstream-gone")
        self.assertEqual(by_path[str(merged)]["reason"], "merged-to-default")
        self.assertEqual(by_path[str(stale)]["reason"], "stale")
        self.assertEqual(by_path[str(prm)]["reason"], "pr-merged")
        self.assertIn("PR #101", by_path[str(prm)]["reason_detail"])
        self.assertEqual(by_path[str(prc)]["reason"], "pr-closed")
        self.assertIn("PR #102", by_path[str(prc)]["reason_detail"])

    def test_scan_excludes_main_worktree(self) -> None:
        self.seed_repo()
        self.add_pushed_branch_worktree("stale-feature", old=True)
        self.write_config()
        data = self.scan()
        all_paths = [c["path"] for c in data["cleanable"]] + [s["path"] for s in data["skipped"]]
        self.assertNotIn(str(self.repo), all_paths)

    def test_scan_skips_dirty_locked_unpushed(self) -> None:
        self.seed_repo()
        dirty = self.add_pushed_branch_worktree("dirty-feature")
        (dirty / "dirty.txt").write_text("uncommitted\n")  # untracked -> dirty
        locked = self.add_pushed_branch_worktree("locked-feature")
        git(self.repo, "worktree", "lock", str(locked), env=self.env())
        unpushed = self.add_tracking_worktree("unpushed-feature", prune_remote=False, local_commit=True)
        self.write_config()

        data = self.scan()
        skipped = {s["path"]: s["reason"] for s in data["skipped"]}
        self.assertEqual(skipped[str(dirty)], "dirty")
        self.assertEqual(skipped[str(locked)], "locked")
        self.assertEqual(skipped[str(unpushed)], "unpushed")
        clean_paths = {c["path"] for c in data["cleanable"]}
        for p in (dirty, locked, unpushed):
            self.assertNotIn(str(p), clean_paths)

    def test_scan_protects_stale_with_unpushed_commit(self) -> None:
        # An OLD branch whose commit is on no remote must NOT be cleaned as stale
        # — that would force-delete unique commits. It must be skipped unpushed.
        self.seed_repo()
        orphan = self.add_plain_worktree("orphan-feature", old=True)
        self.write_config()
        data = self.scan()
        skipped = {s["path"]: s["reason"] for s in data["skipped"]}
        self.assertEqual(skipped[str(orphan)], "unpushed")
        self.assertNotIn(str(orphan), {c["path"] for c in data["cleanable"]})

    def test_scan_protects_upstream_gone_with_local_commit(self) -> None:
        # Upstream deleted, but a local commit was made after — that commit is on
        # no remote, so the worktree must be skipped unpushed, not cleaned.
        self.seed_repo()
        gone_local = self.add_tracking_worktree("gone-local", prune_remote=True, local_commit=True)
        self.write_config()
        data = self.scan()
        skipped = {s["path"]: s["reason"] for s in data["skipped"]}
        self.assertEqual(skipped[str(gone_local)], "unpushed")
        self.assertNotIn(str(gone_local), {c["path"] for c in data["cleanable"]})

    def test_scan_cleans_squash_merged_deleted_branch(self) -> None:
        # The dominant GitHub flow: squash-merge + delete branch. The original
        # commit is on no remote ref but is covered by the merged PR head, so it
        # must be cleanable (upstream-gone wins the reason), NOT skipped unpushed.
        self.seed_repo()
        sq = self.add_squash_merged_worktree("squashed-feature")
        self.write_config()
        data = self.scan()
        by_path = {c["path"]: c for c in data["cleanable"]}
        self.assertIn(str(sq), by_path)
        self.assertEqual(by_path[str(sq)]["reason"], "upstream-gone")
        self.assertNotIn(str(sq), {s["path"] for s in data["skipped"]})

    def test_scan_protects_post_merge_commit(self) -> None:
        # Squash-merged, but a commit was made AFTER the merge. That commit is
        # covered by neither a remote nor the merged PR head -> stay protected.
        self.seed_repo()
        sq = self.add_squash_merged_worktree("squashed-wip", post_commit=True)
        self.write_config()
        data = self.scan()
        skipped = {s["path"]: s["reason"] for s in data["skipped"]}
        self.assertEqual(skipped[str(sq)], "unpushed")
        self.assertNotIn(str(sq), {c["path"] for c in data["cleanable"]})

    def test_scan_excludes_unmatched_worktree(self) -> None:
        self.seed_repo()
        active = self.add_pushed_branch_worktree("active-feature")  # recent, pushed, no PR
        self.write_config()
        data = self.scan()
        all_paths = [c["path"] for c in data["cleanable"]] + [s["path"] for s in data["skipped"]]
        self.assertNotIn(str(active), all_paths)

    def test_scan_dedupes_repo_reachable_two_ways(self) -> None:
        self.seed_repo()
        stale = self.add_pushed_branch_worktree("stale-feature", old=True)
        # Reachable both as a direct repo AND as a child of the `work` parent.
        self.write_config(repos=[{"path": str(self.repo)}], parents=[{"path": str(self.work)}])
        data = self.scan()
        matches = [c for c in data["cleanable"] if c["path"] == str(stale)]
        self.assertEqual(len(matches), 1)

    def test_scan_gh_failure_falls_back(self) -> None:
        self._write_gh_stub(GH_STUB_FAIL)
        self.seed_repo()
        stale = self.add_pushed_branch_worktree("stale-feature", old=True)
        self.write_config()
        data = self.scan()
        by_path = {c["path"]: c for c in data["cleanable"]}
        # gh failed, but stale (a non-gh signal) still classifies it.
        self.assertEqual(by_path[str(stale)]["reason"], "stale")
        self.assertTrue(any(e["error"] in ("gh-failed", "gh-unavailable") for e in data["errors"]))

    def test_scan_gh_auth_failure_marks_unavailable(self) -> None:
        self._write_gh_stub(GH_STUB_AUTH)
        self.seed_repo()
        # A recent pushed branch with no other reason reaches the gh check.
        self.add_pushed_branch_worktree("pr-feature")
        self.write_config()
        data = self.scan()
        self.assertTrue(any(e["error"] == "gh-unavailable" for e in data["errors"]))

    def test_scan_respects_stale_days(self) -> None:
        self.seed_repo()
        recent = self.add_pushed_branch_worktree("recent-feature")  # ~now, pushed
        self.write_config(stale_days=1)
        data = self.scan()
        # A commit dated ~now is not older than 1 day -> not stale -> excluded.
        self.assertNotIn(str(recent), {c["path"] for c in data["cleanable"]})

    # --- remove --------------------------------------------------------------

    def test_remove_removes_and_prunes_branch(self) -> None:
        self.seed_repo()
        stale = self.add_pushed_branch_worktree("stale-feature", old=True)
        self.write_config()
        res = self.run_cli("remove", "--paths", str(stale))
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual([r["path"] for r in out["removed"]], [str(stale)])
        self.assertFalse(stale.exists())
        branches = git(self.repo, "branch", "--list", "stale-feature", env=self.env())
        self.assertEqual(branches, "")  # branch fully pushed -> branch -D pruned it
        self.assertNotIn("warning", out["removed"][0])
        self.assertGreater(out["total_bytes_reclaimed"], 0)

    def test_remove_revalidates_now_dirty(self) -> None:
        self.seed_repo()
        stale = self.add_pushed_branch_worktree("stale-feature", old=True)
        self.write_config()
        (stale / "surprise.txt").write_text("changed after scan\n")  # becomes dirty
        res = self.run_cli("remove", "--paths", str(stale))
        out = json.loads(res.stdout)
        self.assertEqual(out["removed"], [])
        self.assertEqual(out["skipped"][0]["reason"], "now-dirty")
        self.assertTrue(stale.exists())  # untouched

    def test_remove_revalidates_unpushed(self) -> None:
        # Even if something upstream of `remove` mislabels a worktree as
        # cleanable, the remove-time re-validation must refuse a path carrying
        # commits on no remote.
        self.seed_repo()
        gone_local = self.add_tracking_worktree("gone-local", prune_remote=True, local_commit=True)
        self.write_config()
        res = self.run_cli("remove", "--paths", str(gone_local))
        out = json.loads(res.stdout)
        self.assertEqual(out["removed"], [])
        self.assertEqual(out["skipped"][0]["reason"], "unpushed")
        self.assertTrue(gone_local.exists())

    def test_remove_squash_merged_branch(self) -> None:
        # Squash-merged + deleted branch: remove must succeed and prune the
        # branch (the merged PR head covers its commit).
        self.seed_repo()
        sq = self.add_squash_merged_worktree("squashed-feature")
        self.write_config()
        res = self.run_cli("remove", "--paths", str(sq))
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual([r["path"] for r in out["removed"]], [str(sq)])
        self.assertFalse(sq.exists())
        self.assertEqual(git(self.repo, "branch", "--list", "squashed-feature", env=self.env()), "")

    def test_prune_branch_keeps_unpushed_commits(self) -> None:
        # Direct unit test of the branch-deletion guard: a branch carrying
        # commits preserved nowhere durable is kept (warned), never force-deleted.
        self.seed_repo()
        self.add_tracking_worktree("gone-local", prune_remote=True, local_commit=True)
        entry: dict = {}
        cleanup_worktrees_cli._prune_branch(self.repo, "gone-local", entry, None)
        self.assertEqual(entry.get("warning"), "branch-prune-skipped")
        self.assertNotEqual(git(self.repo, "branch", "--list", "gone-local", env=self.env()), "")

    def test_remove_refuses_main_worktree(self) -> None:
        self.seed_repo()
        self.add_pushed_branch_worktree("stale-feature", old=True)
        self.write_config()
        res = self.run_cli("remove", "--paths", str(self.repo))
        out = json.loads(res.stdout)
        self.assertEqual(out["skipped"][0]["reason"], "main-worktree")
        self.assertTrue(self.repo.exists())

    def test_remove_refuses_outside_home(self) -> None:
        self.seed_repo()
        self.write_config()
        outside = tempfile.mkdtemp()  # not under self.home
        try:
            res = self.run_cli("remove", "--paths", outside)
            out = json.loads(res.stdout)
            self.assertEqual(out["errors"][0]["error"], "outside-home")
        finally:
            os.rmdir(outside)

    def test_remove_already_gone(self) -> None:
        self.seed_repo()
        self.write_config()
        ghost = str(self.work / "wts" / "never-existed")
        res = self.run_cli("remove", "--paths", ghost)
        out = json.loads(res.stdout)
        self.assertEqual(out["skipped"][0]["reason"], "already-gone")

    # --- config --------------------------------------------------------------

    def test_config_add_and_remove_repo(self) -> None:
        self.seed_repo()
        add = self.run_cli("config", "add-repo", str(self.repo))
        self.assertEqual(add.returncode, 0, add.stderr)
        cfg = json.loads(self.run_cli("config", "list").stdout)
        self.assertIn(str(self.repo), [r["path"] for r in cfg["repos"]])

        rm = self.run_cli("config", "remove", str(self.repo))
        self.assertEqual(json.loads(rm.stdout)["kind"], "repo")
        cfg = json.loads(self.run_cli("config", "list").stdout)
        self.assertEqual(cfg["repos"], [])

    def test_config_add_parent_with_depth(self) -> None:
        self.run_cli("config", "add-parent", str(self.home / "grafts"), "--depth", "3")
        cfg = json.loads(self.run_cli("config", "list").stdout)
        parent = next(p for p in cfg["parents"] if p["path"] == str((self.home / "grafts").resolve()))
        self.assertEqual(parent["depth"], 3)

    def test_config_rejects_path_outside_home(self) -> None:
        outside = tempfile.mkdtemp()
        try:
            res = self.run_cli("config", "add-repo", outside)
            self.assertEqual(res.returncode, 2)
            self.assertIn("outside $HOME", res.stderr)
        finally:
            os.rmdir(outside)

    def test_config_set_stale_days(self) -> None:
        res = self.run_cli("config", "set-stale-days", "14")
        self.assertEqual(json.loads(res.stdout)["stale_days"], 14)
        cfg = json.loads(self.run_cli("config", "list").stdout)
        self.assertEqual(cfg["stale_days"], 14)

    def test_config_confirm_stamps(self) -> None:
        res = self.run_cli("config", "confirm")
        stamp = json.loads(res.stdout)["confirmed_at"]
        self.assertTrue(stamp.endswith("Z"))
        cfg = json.loads(self.run_cli("config", "list").stdout)
        self.assertEqual(cfg["last_confirmed_at"], stamp)

    def test_config_list_empty_default(self) -> None:
        cfg = json.loads(self.run_cli("config", "list").stdout)
        self.assertEqual(cfg["repos"], [])
        self.assertEqual(cfg["parents"], [])
        self.assertEqual(cfg["stale_days"], 30)
        self.assertIsNone(cfg["last_confirmed_at"])

    def test_corrupt_config_exits_nonzero(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text("{not json")
        res = self.run_cli("config", "list")
        self.assertEqual(res.returncode, 3)
        self.assertIn("corrupt config", res.stderr)

    def test_atomic_write_leaves_no_tmp(self) -> None:
        self.run_cli("config", "set-stale-days", "5")
        tmp = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        self.assertFalse(tmp.exists())


class AllowScriptTestCase(unittest.TestCase):
    """The PreToolUse allow-script approves only the bundled CLI invocation.

    With no CLAUDE_PLUGIN_ROOT (the default here) the script falls back to the
    two known layout shapes; tests that set `plugin_root` exercise the production
    path, which matches the real CLI file by inode.
    """

    def run_allow(self, cmd: str, plugin_root: Optional[Path] = None) -> str:
        payload = json.dumps({"tool_input": {"command": cmd}})
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
        if plugin_root is not None:
            env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
        res = subprocess.run(
            ["bash", str(ALLOW_SCRIPT)], input=payload,
            capture_output=True, text=True, timeout=5, env=env,
        )
        return res.stdout

    def test_approves_plugin_cli(self) -> None:
        cmd = "python3 /Users/x/plugins/cleanup-worktrees/scripts/cleanup_worktrees_cli.py scan"
        self.assertIn("allow", self.run_allow(cmd))

    def test_approves_installed_cache_path(self) -> None:
        cmd = 'python3 "/Users/x/.claude/plugins/cache/wild-horses/cleanup-worktrees/0.1.0/scripts/cleanup_worktrees_cli.py" scan'
        self.assertIn("allow", self.run_allow(cmd))

    def test_approves_exact_plugin_root(self) -> None:
        # Production path: the real CLI under its real plugin root, matched by inode.
        plugin_root = CLI.parent.parent
        self.assertIn("allow", self.run_allow(f"python3 {CLI} scan", plugin_root=plugin_root))

    def test_rejects_planted_copy_with_plugin_root(self) -> None:
        # A different file whose path string contains the dev layout must be
        # rejected when CLAUDE_PLUGIN_ROOT is set (inode mismatch).
        plugin_root = CLI.parent.parent
        cmd = "python3 /tmp/evil/plugins/cleanup-worktrees/scripts/cleanup_worktrees_cli.py scan"
        self.assertEqual(self.run_allow(cmd, plugin_root=plugin_root), "")

    def test_rejects_stray_script_elsewhere(self) -> None:
        cmd = "python3 /tmp/cleanup_worktrees_cli.py scan"
        self.assertEqual(self.run_allow(cmd), "")

    def test_rejects_stray_in_plugin_named_dir(self) -> None:
        # Contains `/cleanup-worktrees/` and ends in the script name, but is in
        # neither the dev nor the install layout -> must NOT be approved.
        cmd = "python3 /tmp/cleanup-worktrees/scripts/cleanup_worktrees_cli.py scan"
        self.assertEqual(self.run_allow(cmd), "")

    def test_rejects_command_chaining(self) -> None:
        cmd = "python3 /Users/x/plugins/cleanup-worktrees/scripts/cleanup_worktrees_cli.py scan; rm -rf /"
        self.assertEqual(self.run_allow(cmd), "")

    def test_rejects_dash_c_prefix(self) -> None:
        cmd = "python3 -c 'evil' /Users/x/plugins/cleanup-worktrees/scripts/cleanup_worktrees_cli.py"
        self.assertEqual(self.run_allow(cmd), "")


if __name__ == "__main__":
    unittest.main()
