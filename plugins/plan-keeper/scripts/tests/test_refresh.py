#!/usr/bin/env python3
"""Tests for refresh_worktree_cli.py — the worktree fast-forward CLI plan-do
runs before handing a plan off to an execution engine.

Each test builds a throwaway origin + clone with real git so the fetch/`--ff-only`
path is exercised end to end (not mocked). Part of the plan_keeper test suite;
shared harness lives in support.py.

Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
REFRESH_CLI = _SCRIPTS_DIR / "refresh_worktree_cli.py"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t.t", "-c", "user.name=t", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True,
    )


def _refresh(repo: Path, *extra: str) -> dict:
    res = subprocess.run(
        ["python3", str(REFRESH_CLI), "refresh", "--path", str(repo), *extra],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, f"non-zero exit: {res.stderr}"
    return json.loads(res.stdout)


class TestRefreshWorktree(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _origin_and_clone(self, default_branch: str = "main") -> tuple[Path, Path]:
        origin = self.root / "origin"
        origin.mkdir()
        _git(origin, "init", "-q", "-b", default_branch)
        _git(origin, "commit", "-q", "--allow-empty", "-m", "c1")
        clone = self.root / "work"
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
        return origin, clone

    def test_up_to_date(self) -> None:
        _, clone = self._origin_and_clone()
        out = _refresh(clone)
        self.assertEqual(out["status"], "up-to-date")
        self.assertEqual(out["base"], "main")

    def test_refreshed_fast_forwards(self) -> None:
        origin, clone = self._origin_and_clone()
        _git(origin, "commit", "-q", "--allow-empty", "-m", "c2")
        out = _refresh(clone)
        self.assertEqual(out["status"], "refreshed")
        self.assertEqual(out["behind"], 1)

    def test_ahead_is_not_fast_forwarded(self) -> None:
        origin, clone = self._origin_and_clone()
        _git(clone, "commit", "-q", "--allow-empty", "-m", "local1")
        _git(origin, "commit", "-q", "--allow-empty", "-m", "c2")
        out = _refresh(clone)
        self.assertEqual(out["status"], "ahead")
        self.assertEqual(out["ahead"], 1)

    def test_dirty_is_skipped(self) -> None:
        origin, clone = self._origin_and_clone()
        _git(origin, "commit", "-q", "--allow-empty", "-m", "c2")
        # Create a tracked-file change so the tree is dirty.
        tracked = clone / "file.txt"
        tracked.write_text("v1\n")
        _git(clone, "add", "file.txt")
        _git(clone, "commit", "-q", "-m", "add file")
        tracked.write_text("v2\n")  # now dirty (uncommitted change to tracked file)
        out = _refresh(clone)
        self.assertEqual(out["status"], "dirty")

    def test_not_a_repo(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        out = _refresh(plain)
        self.assertEqual(out["status"], "not-a-repo")

    def test_base_autodetect_master(self) -> None:
        _, clone = self._origin_and_clone(default_branch="master")
        out = _refresh(clone)
        self.assertEqual(out["base"], "master")
        self.assertEqual(out["status"], "up-to-date")

    def test_explicit_base_override(self) -> None:
        origin, clone = self._origin_and_clone()
        _git(origin, "commit", "-q", "--allow-empty", "-m", "c2")
        out = _refresh(clone, "--base", "main")
        self.assertEqual(out["base"], "main")
        self.assertEqual(out["status"], "refreshed")

    def test_low_disk_floor_refuses(self) -> None:
        origin, clone = self._origin_and_clone()
        _git(origin, "commit", "-q", "--allow-empty", "-m", "c2")
        # An impossibly high floor forces the disk-free guard to trip.
        env = {**os.environ, "PLAN_KEEPER_REFRESH_MIN_FREE_GB": "100000000"}
        res = subprocess.run(
            ["python3", str(REFRESH_CLI), "refresh", "--path", str(clone)],
            capture_output=True, text=True, timeout=30, env=env,
        )
        self.assertEqual(res.returncode, 0)
        out = json.loads(res.stdout)
        self.assertEqual(out["status"], "low-disk")


if __name__ == "__main__":
    unittest.main(verbosity=2)
