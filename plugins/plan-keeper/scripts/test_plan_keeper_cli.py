#!/usr/bin/env python3
"""Smoke tests for plan_keeper_cli.py and plan-keeper-cli-allow.sh.

Stdlib-only — no pytest needed. Run from anywhere:

    python3 plugins/plan-keeper/scripts/test_plan_keeper_cli.py

Or via unittest discovery:

    python3 -m unittest discover -s plugins/plan-keeper/scripts -p 'test_plan_keeper_cli.py'

Tests invoke the CLI as a subprocess so exit codes, argparse behavior,
and stdout/stderr separation are exercised exactly as a dispatched
agent would see them. Isolation: HOME=<tmpdir> per test so the CLI's
`Path.home() / "plans"` resolves under the tempdir, never touching
the user's real ~/plans/. Mirrors the precedent at
plugins/harness/skills/task-list-runner/test_task_list_cli.py.
"""
import importlib.util
import json
import os
import subprocess
import sys as _sys
import tempfile
import unittest
import urllib.error
from datetime import date
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

CLI = Path(__file__).parent / "plan_keeper_cli.py"
ALLOW_SCRIPT = Path(__file__).parent / "plan-keeper-cli-allow.sh"


def _import_cli_module():
    """Import plan_keeper_cli.py as a module for in-process testing.

    Network subcommands are tested by patching urllib.request.urlopen and
    calling the CLI's internal functions directly. The subprocess pattern
    (run_cli) can't reach through the process boundary to patch urllib.
    """
    spec = importlib.util.spec_from_file_location("plan_keeper_cli_under_test", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    _sys.modules["plan_keeper_cli_under_test"] = module
    spec.loader.exec_module(module)
    return module


def run_cli(
    *args: str,
    stdin: str = "",
    home: Path,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the CLI with isolated $HOME so it can't touch real ~/plans/."""
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        ["python3", str(CLI), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
        timeout=10,
    )


def run_allow(cmd: str) -> str:
    """Pipe a fake PreToolUse JSON to the allow-script; return its stdout."""
    import json

    payload = json.dumps({"tool_input": {"command": cmd}})
    result = subprocess.run(
        ["bash", str(ALLOW_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout


class IsolatedHomeTestCase(unittest.TestCase):
    """Each test gets a fresh $HOME pointing at a tempdir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.plans_root = self.home / "plans"
        # Use a non-git cwd so derive_repo's git path is a clean miss.
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestRepoDerivation(IsolatedHomeTestCase):
    def test_no_override_uses_cwd_basename(self) -> None:
        r = run_cli("repo", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "workdir")

    def test_override_normalizes_whitespace_and_case(self) -> None:
        r = run_cli("repo", "--override", "General Folder", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "general-folder")

    def test_override_preserves_underscores(self) -> None:
        r = run_cli("repo", "--override", "herds_mobile_app", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "herds_mobile_app")

    def test_override_rejects_empty(self) -> None:
        r = run_cli("repo", "--override", "", home=self.home, cwd=self.cwd)
        # Empty --override falls back to auto-derive (falsy guard), so it
        # uses cwd basename. The path-traversal guard only fires for
        # non-empty traversal strings. This case is documented behavior.
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "workdir")

    def test_override_rejects_dot(self) -> None:
        r = run_cli("repo", "--override", ".", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid repo name", r.stderr)

    def test_override_rejects_dotdot(self) -> None:
        r = run_cli("repo", "--override", "..", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid repo name", r.stderr)

    def test_override_rejects_path_traversal(self) -> None:
        r = run_cli("repo", "--override", "../etc", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid repo name", r.stderr)

    def test_override_rejects_slash(self) -> None:
        r = run_cli("repo", "--override", "foo/bar", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid repo name", r.stderr)

    def test_override_rejects_backslash(self) -> None:
        r = run_cli("repo", "--override", "foo\\bar", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid repo name", r.stderr)


class TestSave(IsolatedHomeTestCase):
    def test_writes_file_at_expected_path(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "test plan",
            stdin="# Test plan\nbody\n",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        today = date.today().isoformat()
        expected = self.plans_root / "scratch" / f"{today}-test-plan.md"
        self.assertEqual(r.stdout.strip(), str(expected))
        self.assertTrue(expected.exists())
        self.assertEqual(expected.read_text(), "# Test plan\nbody\n")

    def test_slugifies_topic_with_punctuation_and_case(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "Hello, World!! (v2)",
            stdin="x\n",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        today = date.today().isoformat()
        expected = self.plans_root / "scratch" / f"{today}-hello-world-v2.md"
        self.assertTrue(expected.exists())

    def test_preserves_underscores_in_topic(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "multi_event parent_title",
            stdin="x\n",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        today = date.today().isoformat()
        expected = self.plans_root / "scratch" / f"{today}-multi_event-parent_title.md"
        self.assertTrue(expected.exists())

    def test_creates_repo_dir_if_missing(self) -> None:
        new_repo = self.plans_root / "brand-new-repo"
        self.assertFalse(new_repo.exists())
        r = run_cli(
            "save", "--override", "brand-new-repo", "--topic", "a",
            stdin="x\n",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(new_repo.is_dir())

    def test_collision_default_fail(self) -> None:
        common = ["save", "--override", "scratch", "--topic", "dup"]
        r1 = run_cli(*common, stdin="x\n", home=self.home)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        r2 = run_cli(*common, stdin="y\n", home=self.home)
        self.assertEqual(r2.returncode, 2)
        self.assertIn("existing:", r2.stderr)
        self.assertIn("suggestion:", r2.stderr)
        self.assertIn("-2.md", r2.stderr)

    def test_collision_suffix(self) -> None:
        common = ["save", "--override", "scratch", "--topic", "dup"]
        run_cli(*common, stdin="x\n", home=self.home)
        r2 = run_cli(*common, "--on-collision", "suffix", stdin="y\n", home=self.home)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertTrue(r2.stdout.strip().endswith("-2.md"))

    def test_collision_overwrite(self) -> None:
        common = ["save", "--override", "scratch", "--topic", "dup"]
        run_cli(*common, stdin="first\n", home=self.home)
        r2 = run_cli(*common, "--on-collision", "overwrite", stdin="second\n", home=self.home)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        written = Path(r2.stdout.strip())
        self.assertEqual(written.read_text(), "second\n")


class TestArchive(IsolatedHomeTestCase):
    def _save_one(self, topic: str = "plan to archive") -> Path:
        r = run_cli(
            "save", "--override", "scratch", "--topic", topic,
            stdin="# Body\nsome text\n",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        return Path(r.stdout.strip())

    def test_happy_path_moves_and_unlinks(self) -> None:
        source = self._save_one()
        r = run_cli(
            "archive", "--override", "scratch", "--file", source.name,
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        target = self.plans_root / "scratch" / "done" / source.name
        self.assertEqual(r.stdout.strip(), str(target))
        self.assertTrue(target.exists())
        self.assertFalse(source.exists(), "source should be unlinked")

    def test_stamp_format(self) -> None:
        source = self._save_one()
        run_cli("archive", "--override", "scratch", "--file", source.name, home=self.home)
        target = self.plans_root / "scratch" / "done" / source.name
        body = target.read_text()
        today = date.today().isoformat()
        # Body should end with: ...content\n\n---\n*Completed: YYYY-MM-DD*\n
        self.assertTrue(
            body.endswith(f"\n\n---\n*Completed: {today}*\n"),
            f"unexpected stamp tail: {body[-80:]!r}",
        )

    def test_completed_date_override(self) -> None:
        source = self._save_one()
        run_cli(
            "archive", "--override", "scratch", "--file", source.name,
            "--completed-date", "2020-01-15",
            home=self.home,
        )
        body = (self.plans_root / "scratch" / "done" / source.name).read_text()
        self.assertIn("*Completed: 2020-01-15*", body)

    def test_missing_source_exits_3(self) -> None:
        r = run_cli(
            "archive", "--override", "scratch", "--file", "nonexistent.md",
            home=self.home,
        )
        self.assertEqual(r.returncode, 3)
        self.assertIn("plan not found", r.stderr)

    def test_rejects_file_with_slash(self) -> None:
        r = run_cli(
            "archive", "--override", "scratch", "--file", "../etc/passwd",
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("basename only", r.stderr)

    def test_rejects_file_with_backslash(self) -> None:
        r = run_cli(
            "archive", "--override", "scratch", "--file", "foo\\bar.md",
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("basename only", r.stderr)

    def test_rejects_file_dot(self) -> None:
        r = run_cli(
            "archive", "--override", "scratch", "--file", ".",
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("basename only", r.stderr)

    def test_rejects_file_dotdot(self) -> None:
        r = run_cli(
            "archive", "--override", "scratch", "--file", "..",
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("basename only", r.stderr)

    def test_rejects_file_empty(self) -> None:
        r = run_cli(
            "archive", "--override", "scratch", "--file", "",
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("basename only", r.stderr)

    def test_collision_fail(self) -> None:
        # Make a victim plan in done/ first
        source = self._save_one("collide me")
        run_cli("archive", "--override", "scratch", "--file", source.name, home=self.home)
        # Save same-name plan again
        run_cli(
            "save", "--override", "scratch", "--topic", "collide me",
            stdin="x\n",
            home=self.home,
        )
        # Second archive should collide in done/
        r = run_cli(
            "archive", "--override", "scratch", "--file", source.name,
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("existing:", r.stderr)
        self.assertIn("suggestion:", r.stderr)

    def test_collision_suffix(self) -> None:
        source = self._save_one("collide me")
        run_cli("archive", "--override", "scratch", "--file", source.name, home=self.home)
        run_cli(
            "save", "--override", "scratch", "--topic", "collide me",
            stdin="x\n",
            home=self.home,
        )
        r = run_cli(
            "archive", "--override", "scratch", "--file", source.name,
            "--on-collision", "suffix",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip().endswith("-2.md"))


class TestList(IsolatedHomeTestCase):
    def test_empty(self) -> None:
        r = run_cli("list", "--override", "empty-repo", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_lists_active_newest_first(self) -> None:
        # Save two plans with different date-prefixed names by varying topic
        # then manually rename to control the sort order.
        run_cli(
            "save", "--override", "scratch", "--topic", "first",
            stdin="x\n", home=self.home,
        )
        run_cli(
            "save", "--override", "scratch", "--topic", "second",
            stdin="x\n", home=self.home,
        )
        # Rename to force a known sort order (newest YYYY-MM-DD prefix first)
        d = self.plans_root / "scratch"
        (d / sorted(p.name for p in d.glob("*.md"))[0]).rename(d / "2026-05-19-first.md")
        (d / sorted(p.name for p in d.glob("*.md") if p.name != "2026-05-19-first.md")[0]).rename(
            d / "2026-05-20-second.md"
        )
        r = run_cli("list", "--override", "scratch", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().split("\n")
        self.assertEqual(lines, ["2026-05-20-second.md", "2026-05-19-first.md"])

    def test_state_done(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "to be archived",
            stdin="x\n", home=self.home,
        )
        fname = Path(r.stdout.strip()).name
        run_cli("archive", "--override", "scratch", "--file", fname, home=self.home)
        active = run_cli("list", "--override", "scratch", home=self.home)
        done = run_cli("list", "--override", "scratch", "--state", "done", home=self.home)
        self.assertEqual(active.stdout, "")
        self.assertIn(fname, done.stdout)

    def test_active_glob_excludes_done_subdir(self) -> None:
        """`list` of active plans must never enumerate files in done/."""
        r = run_cli(
            "save", "--override", "scratch", "--topic", "archived plan",
            stdin="x\n", home=self.home,
        )
        fname = Path(r.stdout.strip()).name
        run_cli("archive", "--override", "scratch", "--file", fname, home=self.home)
        # Save a fresh active plan
        run_cli(
            "save", "--override", "scratch", "--topic", "still active",
            stdin="x\n", home=self.home,
        )
        active = run_cli("list", "--override", "scratch", home=self.home)
        self.assertNotIn(fname, active.stdout, "archived plan leaked into active list")
        self.assertIn("still-active", active.stdout)


class TestListRepos(IsolatedHomeTestCase):
    def test_counts_per_state(self) -> None:
        # Two repos, varied content
        run_cli("save", "--override", "alpha", "--topic", "a", stdin="x\n", home=self.home)
        run_cli("save", "--override", "alpha", "--topic", "b", stdin="x\n", home=self.home)
        r = run_cli("save", "--override", "beta", "--topic", "c", stdin="x\n", home=self.home)
        run_cli("archive", "--override", "beta", "--file", Path(r.stdout.strip()).name, home=self.home)
        out = run_cli("list-repos", home=self.home)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.strip().split("\n")
        self.assertIn("alpha: active=2", lines)
        self.assertIn("beta: done=1", lines)

    def test_skips_empty_dirs(self) -> None:
        # Create an empty subdir under ~/plans/ — should not appear
        (self.plans_root / "ghost").mkdir(parents=True)
        run_cli("save", "--override", "real", "--topic", "x", stdin="x\n", home=self.home)
        out = run_cli("list-repos", home=self.home)
        self.assertIn("real:", out.stdout)
        self.assertNotIn("ghost", out.stdout)


class TestAllowScript(unittest.TestCase):
    """Black-box tests for the PreToolUse allow-script's regex."""

    def assert_match(self, cmd: str) -> None:
        out = run_allow(cmd)
        self.assertIn("permissionDecision", out, f"expected match for: {cmd}")

    def assert_no_match(self, cmd: str) -> None:
        out = run_allow(cmd)
        self.assertEqual(out, "", f"unexpected match for: {cmd}")

    # --- Match cases ---

    def test_dev_path_unquoted(self) -> None:
        self.assert_match(
            "python3 /repo/plugins/plan-keeper/scripts/plan_keeper_cli.py list"
        )

    def test_dev_path_double_quoted(self) -> None:
        self.assert_match(
            'python3 "/repo/plugins/plan-keeper/scripts/plan_keeper_cli.py" save'
        )

    def test_dev_path_single_quoted(self) -> None:
        self.assert_match(
            "python3 '/repo/plugins/plan-keeper/scripts/plan_keeper_cli.py' archive --file foo.md"
        )

    def test_installed_cache_path(self) -> None:
        self.assert_match(
            "python3 /home/u/.claude/plugins/cache/wh/plan-keeper/1.1.0/scripts/plan_keeper_cli.py list"
        )

    # --- Non-match cases (the new tighter regex must reject these) ---

    def test_rejects_python_c_exploit(self) -> None:
        """The original substring check would have approved this — the
        tightened regex must not."""
        self.assert_no_match(
            'python3 -c "import os; os.system(\'evil\')" /any/plan-keeper/scripts/plan_keeper_cli.py'
        )

    def test_rejects_python_m_unrelated(self) -> None:
        self.assert_no_match(
            "python3 -m unrelated_module /any/plan-keeper/scripts/plan_keeper_cli.py"
        )

    def test_rejects_other_script_with_token_in_args(self) -> None:
        self.assert_no_match(
            "python3 /a/b/other_script.py /plan-keeper/scripts/plan_keeper_cli.py"
        )

    def test_rejects_python3_version(self) -> None:
        self.assert_no_match("python3 --version")

    def test_rejects_missing_scripts_segment(self) -> None:
        self.assert_no_match(
            "python3 /a/b/plan-keeper/plan_keeper_cli.py list"
        )

    def test_rejects_missing_plan_keeper_segment(self) -> None:
        self.assert_no_match(
            "python3 /a/b/scripts/plan_keeper_cli.py list"
        )


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
        result = run_cli("repo", "--full", home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "herds-social/herds")

    def test_full_parses_ssh_github(self) -> None:
        self._init_git_repo("git@github.com:herds-social/herds.git")
        result = run_cli("repo", "--full", home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "herds-social/herds")

    def test_full_parses_https_no_dotgit(self) -> None:
        self._init_git_repo("https://github.com/herds-social/herds")
        result = run_cli("repo", "--full", home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "herds-social/herds")

    def test_full_unparsable_returns_unknown_prefix(self) -> None:
        # No git remote at all — falls back to cwd basename with unknown/ prefix.
        result = run_cli("repo", "--full", home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "unknown/workdir")


class TestFileMetaGet(IsolatedHomeTestCase):
    def _write_plan(self, content: str) -> Path:
        path = self.cwd / "plan.md"
        path.write_text(content, encoding="utf-8")
        return path

    def test_no_frontmatter_returns_empty_fields(self) -> None:
        path = self._write_plan("# Just a heading\n\nBody.\n")
        result = run_cli(
            "file-meta", "get", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data, {"Ticket": "", "Ticket System": "", "Completed on": ""})

    def test_full_frontmatter_parses(self) -> None:
        path = self._write_plan(
            "---\n"
            "Ticket: ENG-123\n"
            "Ticket System: linear\n"
            "Completed on: 2026-05-20\n"
            "---\n"
            "\n# Heading\n"
        )
        result = run_cli(
            "file-meta", "get", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data, {
            "Ticket": "ENG-123",
            "Ticket System": "linear",
            "Completed on": "2026-05-20",
        })

    def test_partial_frontmatter_returns_present_fields(self) -> None:
        path = self._write_plan(
            "---\n"
            "Ticket: ENG-99\n"
            "Ticket System: linear\n"
            "---\n"
            "# H\n"
        )
        result = run_cli(
            "file-meta", "get", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["Ticket"], "ENG-99")
        self.assertEqual(data["Completed on"], "")

    def test_malformed_frontmatter_missing_colon_exits_5(self) -> None:
        path = self._write_plan("---\nTicket ENG-123\n---\n")  # missing colon
        result = run_cli(
            "file-meta", "get", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 5)
        self.assertIn("malformed", result.stderr.lower())

    def test_malformed_frontmatter_no_closing_exits_5(self) -> None:
        # Opening --- but no closing --- before EOF.
        path = self._write_plan("---\nTicket: ENG-1\n")
        result = run_cli(
            "file-meta", "get", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 5)
        self.assertIn("closing", result.stderr.lower())

    def test_malformed_frontmatter_unknown_field_exits_5(self) -> None:
        path = self._write_plan("---\nUnknownField: x\n---\n")
        result = run_cli(
            "file-meta", "get", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 5)
        self.assertIn("unknown field", result.stderr.lower())

    def test_missing_file_exits_3(self) -> None:
        result = run_cli(
            "file-meta", "get", "--file", str(self.cwd / "nope.md"),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 3)


class TestFileMetaSet(IsolatedHomeTestCase):
    def _write_plan(self, content: str) -> Path:
        path = self.cwd / "plan.md"
        path.write_text(content, encoding="utf-8")
        return path

    def test_creates_frontmatter_on_bare_file(self) -> None:
        path = self._write_plan("# Heading\n\nBody.\n")
        result = run_cli(
            "file-meta", "set",
            "--file", str(path),
            "--ticket", "ENG-123",
            "--ticket-system", "linear",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        new_text = path.read_text(encoding="utf-8")
        self.assertEqual(
            new_text,
            "---\n"
            "Ticket: ENG-123\n"
            "Ticket System: linear\n"
            "---\n"
            "\n# Heading\n\nBody.\n"
        )

    def test_updates_existing_frontmatter_in_place(self) -> None:
        path = self._write_plan(
            "---\n"
            "Ticket: OLD-1\n"
            "Ticket System: jira\n"
            "---\n"
            "\n# H\n"
        )
        result = run_cli(
            "file-meta", "set",
            "--file", str(path),
            "--ticket", "ENG-99",
            "--ticket-system", "linear",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        new_text = path.read_text(encoding="utf-8")
        self.assertIn("Ticket: ENG-99", new_text)
        self.assertIn("Ticket System: linear", new_text)
        self.assertNotIn("OLD-1", new_text)
        self.assertNotIn("Ticket System: jira", new_text)

    def test_preserves_unmodified_fields(self) -> None:
        path = self._write_plan(
            "---\n"
            "Ticket: KEEP-1\n"
            "Ticket System: linear\n"
            "Completed on: 2026-05-19\n"
            "---\n"
            "\n# H\n"
        )
        # Only setting --completed-on, Ticket fields should stay.
        result = run_cli(
            "file-meta", "set",
            "--file", str(path),
            "--completed-on", "2026-05-20",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        new_text = path.read_text(encoding="utf-8")
        self.assertIn("Ticket: KEEP-1", new_text)
        self.assertIn("Completed on: 2026-05-20", new_text)

    def test_omits_empty_fields(self) -> None:
        path = self._write_plan("# H\n\nBody.\n")
        result = run_cli(
            "file-meta", "set",
            "--file", str(path),
            "--ticket", "ENG-1",
            "--ticket-system", "linear",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        new_text = path.read_text(encoding="utf-8")
        # Completed on was never set, so the line should be absent.
        self.assertNotIn("Completed on:", new_text)

    def test_requires_at_least_one_flag(self) -> None:
        # No --ticket, --ticket-system, or --completed-on should exit 2.
        path = self._write_plan("# Original\n")
        original = path.read_text(encoding="utf-8")
        result = run_cli(
            "file-meta", "set",
            "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)
        # Original file content unchanged.
        self.assertEqual(path.read_text(encoding="utf-8"), original)


class TestFileMetaStrip(IsolatedHomeTestCase):
    def _write_plan(self, content: str) -> Path:
        path = self.cwd / "plan.md"
        path.write_text(content, encoding="utf-8")
        return path

    def test_strips_frontmatter(self) -> None:
        path = self._write_plan(
            "---\n"
            "Ticket: ENG-1\n"
            "---\n"
            "\n# Body\n\nWords.\n"
        )
        result = run_cli(
            "file-meta", "strip", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "# Body\n\nWords.\n")

    def test_no_frontmatter_returns_input_verbatim(self) -> None:
        path = self._write_plan("# Bare\n\nNo frontmatter here.\n")
        result = run_cli(
            "file-meta", "strip", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "# Bare\n\nNo frontmatter here.\n")


class TestTicketSystemConfig(IsolatedHomeTestCase):
    def test_list_no_config(self) -> None:
        result = run_cli(
            "ticket-system-config", "list",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), [])

    def test_save_then_get(self) -> None:
        payload = (
            '{"apiKey": "k", "defaults": {"teamId": "t"}, '
            '"cache": {"teams": [{"id": "t", "name": "Eng"}]}}'
        )
        result = run_cli(
            "ticket-system-config", "save", "--name", "linear",
            stdin=payload, home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # Now read back.
        result = run_cli(
            "ticket-system-config", "get", "--name", "linear",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiKey"], "k")
        self.assertEqual(data["defaults"]["teamId"], "t")

    def test_get_missing_system_exits_3(self) -> None:
        result = run_cli(
            "ticket-system-config", "get", "--name", "linear",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 3)

    def test_list_after_save_returns_configured(self) -> None:
        run_cli(
            "ticket-system-config", "save", "--name", "linear",
            stdin='{"apiKey": "k"}',
            home=self.home, cwd=self.cwd,
        )
        result = run_cli(
            "ticket-system-config", "list",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), ["linear"])

    def test_save_then_list_two_systems(self) -> None:
        run_cli("ticket-system-config", "save", "--name", "linear",
                stdin='{"apiKey": "k1"}', home=self.home, cwd=self.cwd)
        run_cli("ticket-system-config", "save", "--name", "jira",
                stdin='{"apiToken": "t"}', home=self.home, cwd=self.cwd)
        result = run_cli(
            "ticket-system-config", "list",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(sorted(json.loads(result.stdout)), ["jira", "linear"])

    def test_save_sets_chmod_600(self) -> None:
        run_cli("ticket-system-config", "save", "--name", "linear",
                stdin='{"apiKey": "k"}', home=self.home, cwd=self.cwd)
        repo_dir = self.plans_root / "workdir"
        config = repo_dir / ".plankeeper.json"
        self.assertTrue(config.exists())
        mode = config.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, oct(mode))

    def test_save_rejects_invalid_json(self) -> None:
        result = run_cli(
            "ticket-system-config", "save", "--name", "linear",
            stdin="not json",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)


class TestTicketApiLinearViewer(unittest.TestCase):
    """Network tests run in-process with urllib patched. No subprocess."""

    def setUp(self) -> None:
        self.cli = _import_cli_module()

    def _mock_urlopen_returning(self, status: int, body: dict):
        """Build a urlopen-style context-manager mock with a fixed JSON body."""
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.status = status
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_viewer_returns_identity_on_200(self) -> None:
        response_body = {
            "data": {"viewer": {"id": "u1", "name": "Paul", "email": "p@x.com"}}
        }
        mock_resp = self._mock_urlopen_returning(200, response_body)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            result = self.cli.linear_viewer(api_key="lin_test_key")
        self.assertEqual(result, {"id": "u1", "name": "Paul", "email": "p@x.com"})
        # Verify the request itself.
        call_args = mock_open.call_args
        req = call_args[0][0]  # first positional arg is the Request object
        self.assertEqual(req.full_url, "https://api.linear.app/graphql")
        self.assertEqual(req.get_header("Authorization"), "lin_test_key")
        body = json.loads(req.data.decode("utf-8"))
        self.assertIn("viewer", body["query"])

    def test_viewer_raises_on_401(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="https://api.linear.app/graphql",
                code=401, msg="Unauthorized", hdrs=Message(), fp=None,
            ),
        ):
            with self.assertRaises(self.cli.PlanKeeperCliError) as ctx:
                self.cli.linear_viewer(api_key="bad_key")
        self.assertEqual(ctx.exception.code, 3)

    def test_viewer_raises_on_network_error(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with self.assertRaises(self.cli.PlanKeeperCliError) as ctx:
                self.cli.linear_viewer(api_key="k")
        self.assertEqual(ctx.exception.code, 4)


class TestTicketApiLinearLists(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()

    def _mock_response(self, body: dict):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.status = 200
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_teams_returns_node_array(self) -> None:
        response = {"data": {"teams": {
            "nodes": [{"id": "t1", "name": "Engineering"}, {"id": "t2", "name": "Design"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch("urllib.request.urlopen", return_value=self._mock_response(response)):
            result = self.cli.linear_teams(api_key="k")
        self.assertEqual(result, [
            {"id": "t1", "name": "Engineering"},
            {"id": "t2", "name": "Design"},
        ])

    def test_teams_paginates_multiple_pages(self) -> None:
        page1 = {"data": {"teams": {
            "nodes": [{"id": "t1", "name": "Engineering"}],
            "pageInfo": {"endCursor": "cur1", "hasNextPage": True},
        }}}
        page2 = {"data": {"teams": {
            "nodes": [{"id": "t2", "name": "Design"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch(
            "urllib.request.urlopen",
            side_effect=[self._mock_response(page1), self._mock_response(page2)],
        ) as mock_open:
            result = self.cli.linear_teams(api_key="k")
        self.assertEqual(len(result), 2)
        self.assertEqual(mock_open.call_count, 2)
        # Second call should pass after=cur1 in its variables.
        second_call_body = json.loads(mock_open.call_args_list[1][0][0].data)
        self.assertEqual(second_call_body["variables"]["after"], "cur1")

    def test_projects_includes_team_ids(self) -> None:
        response = {"data": {"projects": {
            "nodes": [{
                "id": "p1",
                "name": "Backend",
                "teams": {"nodes": [{"id": "t1"}, {"id": "t2"}]},
            }],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch("urllib.request.urlopen", return_value=self._mock_response(response)):
            result = self.cli.linear_projects(api_key="k")
        self.assertEqual(result, [{"id": "p1", "name": "Backend", "teamIds": ["t1", "t2"]}])

    def test_labels_preserves_optional_team_scope(self) -> None:
        response = {"data": {"issueLabels": {
            "nodes": [
                {"id": "l1", "name": "plan", "team": None},  # workspace-wide
                {"id": "l2", "name": "bug",  "team": {"id": "t1"}},  # team-scoped
            ],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch("urllib.request.urlopen", return_value=self._mock_response(response)):
            result = self.cli.linear_labels(api_key="k")
        self.assertEqual(result, [
            {"id": "l1", "name": "plan", "teamId": None},
            {"id": "l2", "name": "bug", "teamId": "t1"},
        ])

    def test_users_returns_name_and_email(self) -> None:
        response = {"data": {"users": {
            "nodes": [{"id": "u1", "name": "Paul", "email": "p@x.com"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch("urllib.request.urlopen", return_value=self._mock_response(response)):
            result = self.cli.linear_users(api_key="k")
        self.assertEqual(result, [{"id": "u1", "name": "Paul", "email": "p@x.com"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
