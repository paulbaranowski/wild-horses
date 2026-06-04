#!/usr/bin/env python3
"""Repo derivation, slugify, and name/extension validation (naming.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import subprocess
import unittest

from support import (
    IsolatedHomeTestCase,
    run_cli,
)


class TestRepoDerivation(IsolatedHomeTestCase):
    def test_bare_repo_without_subcommand_is_usage_error(self) -> None:
        # `repo` is a pure parent (required subcommand). Bare `repo` must fail
        # with argparse's exit-2 usage error rather than silently doing nothing.
        r = run_cli("repo", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("error", r.stderr.lower())

    def test_no_override_uses_cwd_basename(self) -> None:
        r = run_cli("repo", "name", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "workdir")

    def test_override_normalizes_whitespace_and_case(self) -> None:
        r = run_cli("repo", "name", "--override", "General Folder", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "general-folder")

    def test_override_preserves_underscores(self) -> None:
        r = run_cli("repo", "name", "--override", "herds_mobile_app", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "herds_mobile_app")

    def test_override_rejects_empty(self) -> None:
        r = run_cli("repo", "name", "--override", "", home=self.home, cwd=self.cwd)
        # Empty --override falls back to auto-derive (falsy guard), so it
        # uses cwd basename. The path-traversal guard only fires for
        # non-empty traversal strings. This case is documented behavior.
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "workdir")

    def test_override_rejects_dot(self) -> None:
        r = run_cli("repo", "name", "--override", ".", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid repo name", r.stderr)

    def test_override_rejects_dotdot(self) -> None:
        r = run_cli("repo", "name", "--override", "..", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid repo name", r.stderr)

    def test_override_rejects_path_traversal(self) -> None:
        r = run_cli("repo", "name", "--override", "../etc", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid repo name", r.stderr)

    def test_override_rejects_slash(self) -> None:
        r = run_cli("repo", "name", "--override", "foo/bar", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid repo name", r.stderr)

    def test_override_rejects_backslash(self) -> None:
        r = run_cli("repo", "name", "--override", "foo\\bar", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid repo name", r.stderr)

class TestRepoFull(IsolatedHomeTestCase):
    def _init_git_repo(self, remote_url: str) -> None:
        """Initialize a minimal git repo in self.cwd with the given origin URL."""
        subprocess.run(["git", "init", "-q"], cwd=self.cwd, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=self.cwd,
            check=True,
        )

    def test_full_parses_https_github(self) -> None:
        self._init_git_repo("https://github.com/herds-social/herds.git")
        result = run_cli("repo", "name", "--full", home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "herds-social/herds")

    def test_full_parses_ssh_github(self) -> None:
        self._init_git_repo("git@github.com:herds-social/herds.git")
        result = run_cli("repo", "name", "--full", home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "herds-social/herds")

    def test_full_parses_https_no_dotgit(self) -> None:
        self._init_git_repo("https://github.com/herds-social/herds")
        result = run_cli("repo", "name", "--full", home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "herds-social/herds")

    def test_full_unparsable_returns_unknown_prefix(self) -> None:
        # No git remote at all — falls back to cwd basename with unknown/ prefix.
        result = run_cli("repo", "name", "--full", home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "unknown/workdir")


if __name__ == "__main__":
    unittest.main(verbosity=2)
