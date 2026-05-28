#!/usr/bin/env python3
"""Smoke tests for update_repos_cli.py and update-repos-cli-allow.sh.

Stdlib-only — no pytest needed. Run from anywhere:

    python3 plugins/wrangle/scripts/test_update_repos_cli.py

Or via unittest discovery:

    python3 -m unittest discover -s plugins/wrangle/scripts -p 'test_update_repos_cli.py'

Tests invoke the CLI as a subprocess so exit codes, argparse behavior,
and stdout/stderr separation are exercised exactly as a dispatched
agent would see them. Isolation: HOME=<tmpdir> per test so the CLI's
config path (~/.config/wild-horses/wrangle/repos.json) resolves
under the tempdir, never touching the user's real config.

Mirrors the precedent at plugins/plan-keeper/scripts/test_plan_keeper_cli.py.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

CLI = Path(__file__).parent / "update_repos_cli.py"
ALLOW_SCRIPT = Path(__file__).parent / "update-repos-cli-allow.sh"


def run_cli(
    *args: str,
    home: Path,
    cwd: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the CLI with isolated $HOME so it can't touch real ~/.config/.

    `env_extra` overlays extra env vars (e.g. a shimmed PATH or
    WRANGLE_GIT_TIMEOUT) for tests that exercise git's timeout/prompt handling.
    """
    env = {**os.environ, "HOME": str(home)}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["python3", str(CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
        timeout=15,
    )


def run_allow(cmd: str) -> str:
    """Pipe a fake PreToolUse JSON to the allow-script; return stdout."""
    payload = json.dumps({"tool_input": {"command": cmd}})
    result = subprocess.run(
        ["bash", str(ALLOW_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout


def git(cwd: Path, *args: str) -> None:
    """Run git in `cwd`, asserting success. Suppresses output."""
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def make_remote_and_clone(
    clone_root: Path,
    scratch: Path,
    name: str,
    branch: str = "main",
) -> tuple[Path, Path]:
    """Create a bare `name`.git remote (under `scratch`) with one commit on
    `branch`, and a working clone under `clone_root`.

    Returns (bare_path, clone_path), both resolved so they compare cleanly
    against the CLI's output (the CLI calls `.resolve()`, which follows
    `/var → /private/var` on macOS).

    Bare + the seed working tree are kept under `scratch` (NOT `clone_root`)
    so bootstrap-discover tests can walk `clone_root` without picking them up.
    """
    bare = scratch / f"{name}.git"
    git(scratch, "init", "--bare", "-b", branch, str(bare))

    seed = scratch / f"{name}-seed"
    git(scratch, "init", "-b", branch, str(seed))
    git(seed, "config", "user.email", "t@example.com")
    git(seed, "config", "user.name", "Test")
    (seed / "README.md").write_text("seed\n")
    git(seed, "add", "README.md")
    git(seed, "commit", "-m", "init")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "origin", branch)

    clone = clone_root / name
    git(clone_root, "clone", "-b", branch, str(bare), str(clone))
    git(clone, "config", "user.email", "t@example.com")
    git(clone, "config", "user.name", "Test")
    return bare.resolve(), clone.resolve()


def commit_to_bare(bare: Path, scratch: Path, branch: str, filename: str = "extra.txt") -> None:
    """Push a new commit to `bare` so a clone can fast-forward.

    The pusher clone goes under `scratch` (outside any discover root)."""
    pusher = scratch / f"pusher-{filename}"
    git(scratch, "clone", "-b", branch, str(bare), str(pusher))
    git(pusher, "config", "user.email", "t@example.com")
    git(pusher, "config", "user.name", "Test")
    (pusher / filename).write_text("hi\n")
    git(pusher, "add", filename)
    git(pusher, "commit", "-m", f"add {filename}")
    git(pusher, "push", "origin", branch)


class IsolatedHomeTestCase(unittest.TestCase):
    """Each test gets a fresh $HOME pointing at a tempdir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.config_path = self.home / ".config" / "wild-horses" / "wrangle" / "repos.json"
        # `work` is the discoverable surface (bootstrap-discover walks it);
        # `scratch` holds bare remotes and seed clones so they don't pollute
        # discovery results.
        self.work = self.home / "work"
        self.work.mkdir()
        self.scratch = self.home / "scratch"
        self.scratch.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_config(self, repos: list) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps({"repos": repos}))

    def write_raw_config(self, raw: str) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(raw)


class TestConfigValidation(IsolatedHomeTestCase):
    """Regression coverage for the CodeRabbit Thread 2 finding:
    `load_config()` previously trusted entries blindly, so malformed JSON
    would crash with KeyError mid-output. Validation now rejects upfront."""

    def test_missing_config_lists_empty(self) -> None:
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["repos"], [])

    def test_missing_repos_key_exits_3(self) -> None:
        self.write_raw_config('{"other": []}')
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("missing 'repos' list", r.stderr)

    def test_repos_not_a_list_exits_3(self) -> None:
        self.write_raw_config('{"repos": "oops"}')
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("missing 'repos' list", r.stderr)

    def test_corrupt_json_exits_3(self) -> None:
        self.write_raw_config("{not json")
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("corrupt config", r.stderr)

    def test_json_root_is_list_exits_3(self) -> None:
        # `json.loads` accepts any valid JSON value, not just objects. A
        # list/string/number at the root would TypeError on `"repos" not in
        # data` without the top-level dict guard.
        self.write_raw_config('[{"path": "/tmp/r", "branch": "main"}]')
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("missing 'repos' list", r.stderr)

    def test_json_root_is_scalar_exits_3(self) -> None:
        self.write_raw_config("42")
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("missing 'repos' list", r.stderr)

    def test_entry_not_a_dict_exits_3(self) -> None:
        self.write_config(["not-a-dict"])
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("invalid repo entry at index 0", r.stderr)

    def test_entry_missing_branch_exits_3(self) -> None:
        self.write_config([{"path": "/tmp/repo"}])
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("invalid repo entry at index 0", r.stderr)

    def test_entry_missing_path_exits_3(self) -> None:
        self.write_config([{"branch": "main"}])
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("invalid repo entry at index 0", r.stderr)

    def test_entry_empty_path_exits_3(self) -> None:
        self.write_config([{"path": "", "branch": "main"}])
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("invalid repo entry at index 0", r.stderr)

    def test_entry_empty_branch_exits_3(self) -> None:
        self.write_config([{"path": "/tmp/r", "branch": ""}])
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("invalid repo entry at index 0", r.stderr)

    def test_entry_non_string_path_exits_3(self) -> None:
        self.write_config([{"path": 123, "branch": "main"}])
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("invalid repo entry at index 0", r.stderr)

    def test_error_message_names_bad_index(self) -> None:
        # First entry valid, second entry invalid — error should point at index 1.
        _, good = make_remote_and_clone(self.work, self.scratch, "good")
        self.write_config([
            {"path": str(good), "branch": "main"},
            {"path": "/tmp/missing-branch"},
        ])
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("index 1", r.stderr)

    def test_invalid_default_dirty_action_exits_3(self) -> None:
        self.write_raw_config('{"repos": [], "default_dirty_action": "nope"}')
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("invalid default_dirty_action", r.stderr)

    def test_valid_default_dirty_action_ok(self) -> None:
        self.write_raw_config('{"repos": [], "default_dirty_action": "skip"}')
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["default_dirty_action"], "skip")

    def test_invalid_per_repo_dirty_action_exits_3(self) -> None:
        self.write_config([{"path": "/tmp/r", "branch": "main", "dirty_action": "bogus"}])
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("invalid dirty_action", r.stderr)
        self.assertIn("index 0", r.stderr)

    def test_valid_per_repo_dirty_action_ok(self) -> None:
        self.write_config([{"path": "/tmp/r", "branch": "main", "dirty_action": "stash"}])
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["repos"][0]["dirty_action"], "stash")

    def test_list_defaults_dirty_action_to_ask(self) -> None:
        r = run_cli("list", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["default_dirty_action"], "ask")


class TestAddRemoveList(IsolatedHomeTestCase):
    def test_add_records_repo_with_detected_branch(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        r = run_cli("add", str(repo), home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["added"]["path"], str(repo))
        self.assertEqual(data["added"]["branch"], "main")

    def test_add_rejects_non_git_path(self) -> None:
        plain = self.work / "not-a-repo"
        plain.mkdir()
        r = run_cli("add", str(plain), home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a git repo", r.stderr)

    def test_add_explicit_branch_overrides_detection(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        r = run_cli("add", str(repo), "--branch", "release", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["added"]["branch"], "release")

    def test_add_same_path_twice_updates_entry(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        run_cli("add", str(repo), home=self.home)
        r = run_cli("add", str(repo), "--branch", "feature", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        # Stored as updated, not added a second time.
        self.assertIn("updated", r.stdout)
        listed = json.loads(run_cli("list", home=self.home).stdout)
        self.assertEqual(len(listed["repos"]), 1)
        self.assertEqual(listed["repos"][0]["branch"], "feature")

    def test_remove_drops_entry(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        run_cli("add", str(repo), home=self.home)
        r = run_cli("remove", str(repo), home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        listed = json.loads(run_cli("list", home=self.home).stdout)
        self.assertEqual(listed["repos"], [])

    def test_remove_unknown_path_exits_2(self) -> None:
        r = run_cli("remove", "/nope", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not in config", r.stderr)


class TestBootstrapDiscover(IsolatedHomeTestCase):
    def test_finds_repos_and_marks_in_config(self) -> None:
        _, a = make_remote_and_clone(self.work, self.scratch, "alpha")
        _, b = make_remote_and_clone(self.work, self.scratch, "beta")
        run_cli("add", str(a), home=self.home)
        r = run_cli("bootstrap-discover", "--root", str(self.work), home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        found = {entry["path"]: entry for entry in data["repos"]}
        self.assertIn(str(a), found)
        self.assertIn(str(b), found)
        self.assertTrue(found[str(a)]["in_config"])
        self.assertFalse(found[str(b)]["in_config"])
        # Default branch detected per repo.
        self.assertEqual(found[str(a)]["default_branch"], "main")

    def test_skips_noise_dirs(self) -> None:
        # A "repo" buried inside node_modules/ should NOT be discovered.
        nm = self.work / "node_modules" / "fake-pkg"
        nm.mkdir(parents=True)
        git(nm, "init", "-b", "main")
        r = run_cli("bootstrap-discover", "--root", str(self.work), home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        paths = {entry["path"] for entry in json.loads(r.stdout)["repos"]}
        self.assertNotIn(str(nm.resolve()), paths)

    def test_does_not_descend_into_found_repo(self) -> None:
        # Nested git dir inside a discovered repo must be skipped.
        _, outer = make_remote_and_clone(self.work, self.scratch, "outer")
        nested = outer / "vendor" / "lib"
        nested.mkdir(parents=True)
        git(nested, "init", "-b", "main")
        r = run_cli("bootstrap-discover", "--root", str(self.work), home=self.home)
        paths = {entry["path"] for entry in json.loads(r.stdout)["repos"]}
        self.assertIn(str(outer), paths)
        self.assertNotIn(str(nested.resolve()), paths)


class TestPullAll(IsolatedHomeTestCase):
    def test_dirty_skip_default_reports_skipped(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        (repo / "README.md").write_text("dirty\n")
        self.write_raw_config(json.dumps({
            "default_dirty_action": "skip",
            "repos": [{"path": str(repo), "branch": "main"}],
        }))
        r = run_cli("pull-all", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)["results"][0]
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "dirty")

    def test_dirty_stash_default_pulls_and_pops(self) -> None:
        bare, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        commit_to_bare(bare, self.scratch, "main")
        (repo / "README.md").write_text("dirty\n")
        self.write_raw_config(json.dumps({
            "default_dirty_action": "stash",
            "repos": [{"path": str(repo), "branch": "main"}],
        }))
        r = run_cli("pull-all", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)["results"][0]
        self.assertEqual(result["status"], "pulled")
        self.assertTrue((repo / "extra.txt").exists())          # remote commit landed
        self.assertEqual((repo / "README.md").read_text(), "dirty\n")  # local edit popped back

    def test_dirty_ask_default_reports_dirty(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        (repo / "README.md").write_text("dirty\n")
        # No default_dirty_action set -> resolves to ask -> unchanged behavior.
        self.write_config([{"path": str(repo), "branch": "main"}])
        r = run_cli("pull-all", home=self.home)
        result = json.loads(r.stdout)["results"][0]
        self.assertEqual(result["status"], "dirty")

    def test_per_repo_override_beats_global(self) -> None:
        bare, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        commit_to_bare(bare, self.scratch, "main")
        (repo / "README.md").write_text("dirty\n")
        # Global skip, but this repo is overridden to stash -> it must pull.
        self.write_raw_config(json.dumps({
            "default_dirty_action": "skip",
            "repos": [{"path": str(repo), "branch": "main", "dirty_action": "stash"}],
        }))
        r = run_cli("pull-all", home=self.home)
        result = json.loads(r.stdout)["results"][0]
        self.assertEqual(result["status"], "pulled")
        self.assertTrue((repo / "extra.txt").exists())

    def test_empty_config_returns_empty_marker(self) -> None:
        r = run_cli("pull-all", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data["empty"])

    def test_reports_wrong_branch_without_pulling(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        # Switch to a different branch so config-branch mismatch fires.
        git(repo, "checkout", "-b", "feature")
        self.write_config([{"path": str(repo), "branch": "main"}])
        r = run_cli("pull-all", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        results = json.loads(r.stdout)["results"]
        self.assertEqual(results[0]["status"], "wrong-branch")
        self.assertEqual(results[0]["current_branch"], "feature")

    def test_reports_dirty_without_pulling(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        # Touch a tracked file so the working tree is dirty.
        (repo / "README.md").write_text("dirty\n")
        self.write_config([{"path": str(repo), "branch": "main"}])
        r = run_cli("pull-all", home=self.home)
        results = json.loads(r.stdout)["results"]
        self.assertEqual(results[0]["status"], "dirty")
        self.assertTrue(results[0]["dirty"])

    def test_reports_missing(self) -> None:
        self.write_config([{"path": str(self.work / "nope"), "branch": "main"}])
        r = run_cli("pull-all", home=self.home)
        results = json.loads(r.stdout)["results"]
        self.assertEqual(results[0]["status"], "missing")

    def test_pulls_clean_on_branch_repo(self) -> None:
        bare, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        commit_to_bare(bare, self.scratch, "main")
        self.write_config([{"path": str(repo), "branch": "main"}])
        r = run_cli("pull-all", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)["results"][0]
        self.assertEqual(result["status"], "pulled")
        # pull-all suppresses the verbose `output` field — that's the
        # asymmetric design between pull-all and pull-one.
        self.assertNotIn("output", result)
        # But it DOES carry the one-line diffstat, so the summary can show
        # what actually landed. commit_to_bare adds one file.
        self.assertIn("stat", result)
        self.assertIn("changed", result["stat"])
        # And the new commit actually landed.
        self.assertTrue((repo / "extra.txt").exists())

    def test_up_to_date_when_no_new_commits(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        self.write_config([{"path": str(repo), "branch": "main"}])
        r = run_cli("pull-all", home=self.home)
        result = json.loads(r.stdout)["results"][0]
        self.assertEqual(result["status"], "up-to-date")
        # Nothing was pulled, so there is no diffstat to report.
        self.assertNotIn("stat", result)

    def test_results_follow_config_order_across_mixed_outcomes(self) -> None:
        # pull-all fans repos out across threads, so a slow pull must not let
        # its result jump ahead of a fast one. The output has to mirror config
        # order exactly — the step-5 summary depends on it. Config order here is
        # deliberately NOT alphabetical, to prove ordering isn't an accident of
        # sorting somewhere.
        bare_g, gamma = make_remote_and_clone(self.work, self.scratch, "gamma")
        commit_to_bare(bare_g, self.scratch, "main")  # gamma will fast-forward
        _, alpha = make_remote_and_clone(self.work, self.scratch, "alpha")  # up-to-date
        _, beta = make_remote_and_clone(self.work, self.scratch, "beta")
        git(beta, "checkout", "-b", "feature")  # config says main -> wrong-branch

        self.write_config([
            {"path": str(gamma), "branch": "main"},
            {"path": str(alpha), "branch": "main"},
            {"path": str(beta), "branch": "main"},
        ])
        r = run_cli("pull-all", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        results = json.loads(r.stdout)["results"]

        self.assertEqual([x["path"] for x in results], [str(gamma), str(alpha), str(beta)])
        self.assertEqual([x["status"] for x in results], ["pulled", "up-to-date", "wrong-branch"])


class TestPullTimeout(IsolatedHomeTestCase):
    """The parallel pull-all must never let one hung remote wedge the batch.
    git() bounds every call with a timeout; a killed pull surfaces as
    `timed-out` instead of blocking forever."""

    def _install_git_shim_that_hangs_on_pull(self) -> dict[str, str]:
        """Put a `git` shim first on PATH that hangs on `pull` but defers to
        real git for everything else, so status checks still classify the repo
        as ready and only the network pull stalls. Returns env overlay."""
        real_git = shutil.which("git")
        assert real_git, "git must be on PATH for this test"
        shim_dir = self.home / "bin"
        shim_dir.mkdir()
        shim = shim_dir / "git"
        # `exec sleep` replaces the shim process, so killing the timed-out child
        # kills the sleep directly (no orphan lingering past the test).
        shim.write_text(
            "#!/bin/sh\n"
            'for a in "$@"; do\n'
            '  if [ "$a" = "pull" ]; then exec sleep 30; fi\n'
            "done\n"
            f'exec "{real_git}" "$@"\n'
        )
        shim.chmod(0o755)
        return {"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}", "WRANGLE_GIT_TIMEOUT": "1"}

    def test_hanging_pull_is_bounded_and_reported(self) -> None:
        bare, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        commit_to_bare(bare, self.scratch, "main")  # a real pull would fast-forward
        self.write_config([{"path": str(repo), "branch": "main"}])

        env_extra = self._install_git_shim_that_hangs_on_pull()
        start = time.monotonic()
        r = run_cli("pull-all", home=self.home, env_extra=env_extra)
        elapsed = time.monotonic() - start

        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)["results"][0]
        self.assertEqual(result["status"], "timed-out")
        self.assertIn("error", result)
        # Bounded well below the shim's 30s sleep — the timeout actually fired.
        self.assertLess(elapsed, 20)
        # The pull was killed, so the new commit must NOT have landed.
        self.assertFalse((repo / "extra.txt").exists())


class TestPullOnePreflight(IsolatedHomeTestCase):
    """Regression coverage for the CodeRabbit Thread 3 finding: `pull-one`
    previously called `pull_repo()` directly, sidestepping the same safety
    gate `pull-all` enforces. It now runs `repo_status()` first."""

    def test_wrong_branch_returns_status_without_pulling(self) -> None:
        bare, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        commit_to_bare(bare, self.scratch, "main")
        git(repo, "checkout", "-b", "feature")
        self.write_config([{"path": str(repo), "branch": "main"}])

        r = run_cli("pull-one", str(repo), home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "wrong-branch")
        # And the new commit must NOT have been pulled into the clone,
        # because pull-one refused to act on a wrong-branch repo.
        self.assertFalse((repo / "extra.txt").exists())

    def test_dirty_without_stash_returns_status_without_pulling(self) -> None:
        bare, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        commit_to_bare(bare, self.scratch, "main")
        (repo / "README.md").write_text("dirty\n")
        self.write_config([{"path": str(repo), "branch": "main"}])

        r = run_cli("pull-one", str(repo), home=self.home)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "dirty")
        self.assertFalse((repo / "extra.txt").exists())

    def test_dirty_with_stash_pulls_and_pops(self) -> None:
        bare, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        commit_to_bare(bare, self.scratch, "main")
        (repo / "README.md").write_text("dirty\n")
        self.write_config([{"path": str(repo), "branch": "main"}])

        r = run_cli("pull-one", str(repo), "--stash", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "pulled")
        # New commit landed AND the local dirty edit was popped back.
        self.assertTrue((repo / "extra.txt").exists())
        self.assertEqual((repo / "README.md").read_text(), "dirty\n")

    def test_missing_returns_status_without_pulling(self) -> None:
        self.write_config([{"path": str(self.work / "ghost"), "branch": "main"}])
        r = run_cli("pull-one", str(self.work / "ghost"), home=self.home)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "missing")

    def test_not_in_config_exits_2(self) -> None:
        self.write_config([])
        r = run_cli("pull-one", str(self.work / "anywhere"), home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not in config", r.stderr)

    def test_ready_pull_includes_verbose_output(self) -> None:
        # The asymmetric design: pull-one is the human-facing single-repo verb,
        # so it includes git pull's stdout under `output`. pull-all omits it.
        bare, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        commit_to_bare(bare, self.scratch, "main")
        self.write_config([{"path": str(repo), "branch": "main"}])
        r = run_cli("pull-one", str(repo), home=self.home)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "pulled")
        self.assertIn("output", data)
        # The diffstat rides along with the verbose output too.
        self.assertIn("stat", data)
        self.assertIn("changed", data["stat"])


class TestAllowListShellInjection(IsolatedHomeTestCase):
    """Regression coverage for the CodeRabbit Thread 4 finding: the allow
    regex only constrained the prefix, so `python3 ...cli.py ; uname -a`
    would still auto-approve. A pre-filter for shell metacharacters now
    rejects any chained command before the regex even runs."""

    LEGIT = "python3 /opt/plugins/wrangle/scripts/update_repos_cli.py pull-all"

    def test_legitimate_invocation_is_allowed(self) -> None:
        self.assertIn("permissionDecision", run_allow(self.LEGIT))

    def test_legitimate_with_args_is_allowed(self) -> None:
        cmd = "python3 /opt/plugins/wrangle/scripts/update_repos_cli.py pull-one /tmp/r --stash"
        self.assertIn("permissionDecision", run_allow(cmd))

    def test_semicolon_chain_blocked(self) -> None:
        self.assertEqual(run_allow(f"{self.LEGIT} ; uname -a"), "")

    def test_and_chain_blocked(self) -> None:
        self.assertEqual(run_allow(f"{self.LEGIT} && rm -rf /"), "")

    def test_or_chain_blocked(self) -> None:
        self.assertEqual(run_allow(f"{self.LEGIT} || echo hi"), "")

    def test_pipe_blocked(self) -> None:
        self.assertEqual(run_allow(f"{self.LEGIT} | tee /tmp/x"), "")

    def test_redirect_blocked(self) -> None:
        self.assertEqual(run_allow(f"{self.LEGIT} > /tmp/x"), "")

    def test_input_redirect_blocked(self) -> None:
        self.assertEqual(run_allow(f"{self.LEGIT} < /tmp/x"), "")

    def test_command_substitution_blocked(self) -> None:
        cmd = 'python3 /opt/plugins/wrangle/scripts/update_repos_cli.py add "$(echo /tmp)"'
        self.assertEqual(run_allow(cmd), "")

    def test_backtick_substitution_blocked(self) -> None:
        cmd = "python3 /opt/plugins/wrangle/scripts/update_repos_cli.py add `pwd`"
        self.assertEqual(run_allow(cmd), "")

    def test_newline_chain_blocked(self) -> None:
        # `\n` isn't covered by the metacharacter list and POSIX `.` doesn't
        # match it either, so the allow regex would happily ignore everything
        # after the newline. The case-prefilter must explicitly reject it.
        self.assertEqual(run_allow(f"{self.LEGIT}\nuname -a"), "")

    def test_carriage_return_chain_blocked(self) -> None:
        self.assertEqual(run_allow(f"{self.LEGIT}\runame -a"), "")

    def test_path_outside_plugin_dir_not_allowed(self) -> None:
        # Anchoring on /wrangle/ in the path prevents a stray
        # `update_repos_cli.py` elsewhere in the workspace from being approved.
        cmd = "python3 /tmp/random/scripts/update_repos_cli.py pull-all"
        self.assertEqual(run_allow(cmd), "")

    def test_dash_c_payload_not_allowed(self) -> None:
        # The first positional arg must BE the script — not a `-c` payload
        # that merely mentions a matching path string.
        cmd = 'python3 -c "import os; os.system(\'evil\')" /opt/plugins/wrangle/scripts/update_repos_cli.py'
        self.assertEqual(run_allow(cmd), "")

    def test_non_python_invocation_not_allowed(self) -> None:
        cmd = "bash /opt/plugins/wrangle/scripts/update_repos_cli.py"
        self.assertEqual(run_allow(cmd), "")


class TestSetAction(IsolatedHomeTestCase):
    def test_set_global_default(self) -> None:
        r = run_cli("set-action", "skip", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["default_dirty_action"], "skip")
        listed = json.loads(run_cli("list", home=self.home).stdout)
        self.assertEqual(listed["default_dirty_action"], "skip")

    def test_set_global_rejects_bad_value(self) -> None:
        r = run_cli("set-action", "bogus", home=self.home)
        # argparse `choices` rejects it before our code runs.
        self.assertEqual(r.returncode, 2)

    def test_global_inherit_rejected(self) -> None:
        r = run_cli("set-action", "inherit", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("only valid with --repo", r.stderr)

    def test_set_per_repo_override(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        run_cli("add", str(repo), home=self.home)
        r = run_cli("set-action", "stash", "--repo", str(repo), home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["dirty_action"], "stash")
        listed = json.loads(run_cli("list", home=self.home).stdout)
        self.assertEqual(listed["repos"][0]["dirty_action"], "stash")

    def test_set_per_repo_explicit_ask(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        run_cli("add", str(repo), home=self.home)
        run_cli("set-action", "ask", "--repo", str(repo), home=self.home)
        listed = json.loads(run_cli("list", home=self.home).stdout)
        self.assertEqual(listed["repos"][0]["dirty_action"], "ask")

    def test_inherit_clears_per_repo(self) -> None:
        _, repo = make_remote_and_clone(self.work, self.scratch, "alpha")
        run_cli("add", str(repo), home=self.home)
        run_cli("set-action", "skip", "--repo", str(repo), home=self.home)
        r = run_cli("set-action", "inherit", "--repo", str(repo), home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(json.loads(r.stdout)["dirty_action"])
        listed = json.loads(run_cli("list", home=self.home).stdout)
        self.assertNotIn("dirty_action", listed["repos"][0])

    def test_set_unknown_repo_exits_2(self) -> None:
        r = run_cli("set-action", "skip", "--repo", "/nope", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not in config", r.stderr)


if __name__ == "__main__":
    unittest.main()
