#!/usr/bin/env python3
"""Stdlib unittest suite for discover_repos.py.

Tests invoke the script as a subprocess so exit codes, stdout/stderr
separation, and argument-handling are exercised exactly as a dispatched
agent would see them.

Isolation strategy:
  - Each test gets a fresh TemporaryDirectory.
  - HOME is overridden to that directory so ~/code, ~/dev, etc. resolve
    inside the tmpdir and never touch the real filesystem.
  - A stub `gh` binary is placed at <tmpdir>/bin/gh (chmod +x) and
    PATH is set to <tmpdir>/bin:/usr/bin:/bin so the real gh is shadowed.

Run from anywhere:

    python3 plugins/groundcrew-setup/tests/test_discover_repos.py -v

Or via unittest discovery:

    python3 -m unittest discover -s plugins/groundcrew-setup/tests -p 'test_discover_repos.py'
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "discover_repos.py"

_PYTHON3 = sys.executable


def _make_stub_gh(bin_dir: Path, exit_code: int = 0, output: str = "[]") -> None:
    """Write a fake `gh` executable to bin_dir that emits `output` and exits with exit_code."""
    gh = bin_dir / "gh"
    # POSIX single-quote escape so the JSON survives embedding in a single-quoted printf.
    escaped = output.replace("'", "'\\''")
    gh.write_text(
        f"#!/bin/sh\nprintf '%s' '{escaped}'\nexit {exit_code}\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_git_config(path: Path, url: str) -> None:
    """Create a fake .git/config file at `path` with the given remote origin URL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'[remote "origin"]\n\turl = {url}\n')


def _run_script(
    *args: str,
    home: Path,
    bin_dir: Path,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Invoke discover_repos.py with isolated HOME and PATH."""
    path_val = f"{bin_dir}:/usr/bin:/bin"
    env: dict[str, str] = {
        "HOME": str(home),
        "PATH": path_val,
        "TMPDIR": tempfile.gettempdir(),
        # Keep LANG for sane output on macOS
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_PYTHON3, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


class DiscoverReposTestCase(unittest.TestCase):
    """Each test gets a fresh tmpdir with a stub gh on PATH."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.bin_dir = self.tmpdir / "bin"
        self.bin_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    def run_script(self, *args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        return _run_script(
            *args,
            home=self.tmpdir,
            bin_dir=self.bin_dir,
            extra_env=extra_env,
        )

    def assertValidJson(self, text: str) -> list:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout is not valid JSON: {exc!r}; stdout={text!r}")
        self.assertIsInstance(data, list, f"expected JSON array; got {type(data).__name__}")
        return data

    # ==================================================================
    # Test 1: no gh, no local → empty array
    # ==================================================================
    def test_no_gh_no_local_empty_array(self) -> None:
        """gh not on PATH, no scan dirs exist → [] with exit 0.

        Stubs `gh` with a binary that always exits non-zero (simulating an
        unauthenticated or missing-config gh CLI). A bare empty bin_dir was
        non-deterministic — a real `gh` from /usr/bin would leak through PATH
        and change the expected output on machines where it's globally
        authenticated.
        """
        # Shadow any real gh on PATH with a stub that always fails.
        _make_stub_gh(self.bin_dir, exit_code=1, output="")
        r = _run_script(home=self.tmpdir, bin_dir=self.bin_dir)
        self.assertEqual(r.returncode, 0, f"expected exit 0; stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)
        self.assertEqual(data, [])

    # ==================================================================
    # Test 2: gh-only → sources: ["gh"], sorted
    # ==================================================================
    def test_gh_only_emits_gh_sources(self) -> None:
        """Stub gh returns two repos; no local scan dirs → both repos with sources: [gh]."""
        gh_output = json.dumps([
            {"nameWithOwner": "foo/bar"},
            {"nameWithOwner": "baz/qux"},
        ])
        _make_stub_gh(self.bin_dir, output=gh_output)

        r = self.run_script()
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)

        self.assertEqual(len(data), 2)
        # Sorted alphabetically: baz/qux before foo/bar
        self.assertEqual(data[0]["owner"], "baz")
        self.assertEqual(data[0]["repo"], "qux")
        self.assertEqual(data[0]["sources"], ["gh"])
        self.assertEqual(data[1]["owner"], "foo")
        self.assertEqual(data[1]["repo"], "bar")
        self.assertEqual(data[1]["sources"], ["gh"])

    # ==================================================================
    # Test 3: local-only → sources: ["local"]
    # ==================================================================
    def test_local_only_emits_local_sources(self) -> None:
        """gh returns empty; local dev dir has one repo → sources: [local]."""
        _make_stub_gh(self.bin_dir, output="[]")

        _make_git_config(
            self.tmpdir / "dev" / "foo" / ".git" / "config",
            "git@github.com:foo/bar.git",
        )

        r = self.run_script()
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0], {"owner": "foo", "repo": "bar", "sources": ["local"]})

    # ==================================================================
    # Test 4: overlap → sources: ["gh", "local"]
    # ==================================================================
    def test_overlap_emits_both_sources(self) -> None:
        """gh and local both report foo/bar → single entry with sources: [gh, local]."""
        gh_output = json.dumps([{"nameWithOwner": "foo/bar"}])
        _make_stub_gh(self.bin_dir, output=gh_output)

        _make_git_config(
            self.tmpdir / "dev" / "foo-bar" / ".git" / "config",
            "git@github.com:foo/bar.git",
        )

        r = self.run_script()
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["owner"], "foo")
        self.assertEqual(data[0]["repo"], "bar")
        self.assertEqual(data[0]["sources"], ["gh", "local"])

    # ==================================================================
    # Test 5: gh exits non-zero → falls back to local only
    # ==================================================================
    def test_gh_failure_falls_back_to_local(self) -> None:
        """Stub gh exits 1 (auth failure); local has foo/bar → that single local entry."""
        _make_stub_gh(self.bin_dir, exit_code=1, output="")

        _make_git_config(
            self.tmpdir / "dev" / "myrepo" / ".git" / "config",
            "git@github.com:foo/bar.git",
        )

        r = self.run_script()
        self.assertEqual(r.returncode, 0, f"script should not fail; stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["sources"], ["local"])

    # ==================================================================
    # Test 6: multiple URL formats all parsed correctly
    # ==================================================================
    def test_multiple_url_formats(self) -> None:
        """SSH, HTTPS+.git, and HTTPS without .git are all parsed correctly."""
        _make_stub_gh(self.bin_dir, output="[]")

        dev = self.tmpdir / "dev"
        _make_git_config(dev / "a" / ".git" / "config", "git@github.com:foo/a.git")
        _make_git_config(dev / "b" / ".git" / "config", "https://github.com/foo/b.git")
        _make_git_config(dev / "c" / ".git" / "config", "https://github.com/foo/c")

        r = self.run_script()
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)

        repos = {f"{e['owner']}/{e['repo']}" for e in data}
        self.assertIn("foo/a", repos)
        self.assertIn("foo/b", repos)
        self.assertIn("foo/c", repos)
        self.assertEqual(len(repos), 3)

    # ==================================================================
    # Test 7: non-GitHub URL is silently skipped
    # ==================================================================
    def test_non_github_url_skipped(self) -> None:
        """A GitLab remote must NOT appear in the output."""
        _make_stub_gh(self.bin_dir, output="[]")

        _make_git_config(
            self.tmpdir / "dev" / "gitlab-repo" / ".git" / "config",
            "git@gitlab.com:foo/bar.git",
        )

        r = self.run_script()
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)
        self.assertEqual(data, [], f"GitLab repo should be skipped; got {data!r}")

    # ==================================================================
    # Test 8: node_modules/ excluded from traversal
    # ==================================================================
    def test_excluded_subdirs_not_traversed(self) -> None:
        """A .git/config buried inside node_modules/ must NOT be found."""
        _make_stub_gh(self.bin_dir, output="[]")

        _make_git_config(
            self.tmpdir / "dev" / "node_modules" / "somepkg" / ".git" / "config",
            "git@github.com:evil/pkg.git",
        )

        r = self.run_script()
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)
        repos = {f"{e['owner']}/{e['repo']}" for e in data}
        self.assertNotIn("evil/pkg", repos, "node_modules repos must be excluded")

    # ==================================================================
    # Test 9: --workspace-dir flag includes that directory
    # ==================================================================
    def test_workspace_dir_flag_included(self) -> None:
        """Repos inside a --workspace-dir path are discovered."""
        _make_stub_gh(self.bin_dir, output="[]")

        extra_scan = self.tmpdir / "extra-workspace"
        _make_git_config(
            extra_scan / "myproject" / ".git" / "config",
            "git@github.com:acme/myproject.git",
        )

        r = self.run_script("--workspace-dir", str(extra_scan))
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)

        repos = {f"{e['owner']}/{e['repo']}" for e in data}
        self.assertIn("acme/myproject", repos)

    # ==================================================================
    # Test 10: output is sorted alphabetically by owner/repo
    # ==================================================================
    def test_output_sorted_alphabetically(self) -> None:
        """Repos are emitted in alphabetical owner/repo order regardless of input order."""
        gh_output = json.dumps([
            {"nameWithOwner": "zzz/last"},
            {"nameWithOwner": "aaa/first"},
            {"nameWithOwner": "mmm/middle"},
        ])
        _make_stub_gh(self.bin_dir, output=gh_output)

        r = self.run_script()
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)

        keys = [f"{e['owner']}/{e['repo']}" for e in data]
        self.assertEqual(keys, sorted(keys), f"output not sorted: {keys!r}")

    # ==================================================================
    # Test 11: --help exits 0 and prints usage
    # ==================================================================
    def test_help_exits_0(self) -> None:
        """--help must exit 0 and not crash."""
        r = self.run_script("--help")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")

    # ==================================================================
    # Test 12: repo nested 3 levels deep IS found
    # ==================================================================
    def test_repo_nested_three_levels_deep_found(self) -> None:
        """A repo at ~/dev/group/org/repo/.git/config must be discovered."""
        _make_stub_gh(self.bin_dir, output="[]")

        _make_git_config(
            self.tmpdir / "dev" / "group" / "org" / "repo" / ".git" / "config",
            "git@github.com:group-org/myrepo.git",
        )

        r = self.run_script()
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)

        repos = {f"{e['owner']}/{e['repo']}" for e in data}
        self.assertIn("group-org/myrepo", repos, "3-levels-deep repo should be found with maxdepth 5")
        self.assertEqual(len(data), 1)

    # ==================================================================
    # Test 13: repo nested 4 levels deep is NOT found
    # ==================================================================
    def test_repo_nested_four_levels_deep_excluded(self) -> None:
        """A repo at ~/dev/a/b/c/repo/.git/config must NOT be discovered."""
        _make_stub_gh(self.bin_dir, output="[]")

        _make_git_config(
            self.tmpdir / "dev" / "a" / "b" / "c" / "repo" / ".git" / "config",
            "git@github.com:a-b-c/myrepo.git",
        )

        r = self.run_script()
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)

        repos = {f"{e['owner']}/{e['repo']}" for e in data}
        self.assertNotIn("a-b-c/myrepo", repos, "4-levels-deep repo must be excluded with maxdepth 5")


    # ==================================================================
    # Test 14: --workspace-dir without a value errors with exit 1
    # ==================================================================
    def test_workspace_dir_without_value_errors(self) -> None:
        """Calling --workspace-dir with no following argument exits non-zero and says so."""
        r = self.run_script("--workspace-dir")
        # argparse: exit 2 with "expected one argument" on missing option value.
        self.assertEqual(r.returncode, 2, f"expected exit 2; stderr={r.stderr!r}")
        self.assertIn("--workspace-dir", r.stderr, f"expected --workspace-dir in stderr; got {r.stderr!r}")
        self.assertIn("argument", r.stderr.lower(), f"expected error message in stderr; got {r.stderr!r}")

    # ==================================================================
    # Test 15: --workspace-dir that duplicates a default scan dir is deduped
    # ==================================================================
    def test_workspace_dir_already_in_defaults_deduped(self) -> None:
        """Passing --workspace-dir ~/dev (a default scan dir) scans it only once."""
        _make_stub_gh(self.bin_dir, output="[]")

        _make_git_config(
            self.tmpdir / "dev" / "foo" / ".git" / "config",
            "git@github.com:testorg/foo.git",
        )

        # ~/dev is one of the default scan dirs; passing it explicitly must not
        # cause a double scan (which would produce duplicate entries or wrong sources).
        r = self.run_script("--workspace-dir", str(self.tmpdir / "dev"))
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}")
        data = self.assertValidJson(r.stdout)

        # Repo must appear exactly once.
        matching = [e for e in data if e["owner"] == "testorg" and e["repo"] == "foo"]
        self.assertEqual(len(matching), 1, f"expected exactly one entry; got {data!r}")
        self.assertEqual(matching[0]["sources"], ["local"], f"expected sources: [local]; got {matching[0]!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
