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
import base64
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
        # MD files now get injected Agent/Status frontmatter
        text = expected.read_text()
        self.assertIn("Agent: claude", text)
        self.assertIn("Status: backlog", text)
        self.assertIn("# Test plan", text)

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
        # MD files now get injected Agent/Status frontmatter
        text = written.read_text()
        self.assertIn("Agent: claude", text)
        self.assertIn("Status: backlog", text)
        self.assertIn("second", text)

    def test_extension_json(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "task list",
            "--extension", "json",
            stdin='{"tasks": []}\n',
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        today = date.today().isoformat()
        expected = self.plans_root / "scratch" / f"{today}-task-list.json"
        self.assertEqual(r.stdout.strip(), str(expected))
        self.assertEqual(expected.read_text(), '{"tasks": []}\n')

    def test_extension_accepts_leading_dot(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "yaml file",
            "--extension", ".yaml",
            stdin="key: value\n",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip().endswith(".yaml"))

    def test_extension_defaults_to_md(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "no ext flag",
            stdin="# Plan\n",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip().endswith(".md"))

    def test_extension_rejects_path_separator(self) -> None:
        for bad in ("md/evil", "..", ".", "", "MD", "md.bak", "json gz"):
            r = run_cli(
                "save", "--override", "scratch", "--topic", "x",
                "--extension", bad,
                stdin="x\n",
                home=self.home,
            )
            self.assertEqual(r.returncode, 2, f"extension {bad!r} should be rejected")
            self.assertIn("invalid extension", r.stderr)

    def test_paired_save_matching_base_names(self) -> None:
        """task-list-builder output: paired json + md with matching base name.

        The skill calls save twice with the same --topic + --date, varying
        only --extension. Result: two files sharing the same date-slug stem.
        """
        common = ["save", "--override", "scratch", "--topic", "bulk edit task list"]
        r_json = run_cli(
            *common, "--extension", "json",
            stdin='{"tasks": []}\n', home=self.home,
        )
        r_md = run_cli(
            *common, "--extension", "md",
            stdin="# Bulk edit\n", home=self.home,
        )
        self.assertEqual(r_json.returncode, 0, r_json.stderr)
        self.assertEqual(r_md.returncode, 0, r_md.stderr)
        json_path = Path(r_json.stdout.strip())
        md_path = Path(r_md.stdout.strip())
        # Same stem, different suffix.
        self.assertEqual(json_path.stem, md_path.stem)
        self.assertEqual(json_path.suffix, ".json")
        self.assertEqual(md_path.suffix, ".md")

    def test_collision_suffix_preserves_extension(self) -> None:
        common = [
            "save", "--override", "scratch", "--topic", "dup",
            "--extension", "json",
        ]
        run_cli(*common, stdin='{"a":1}\n', home=self.home)
        r2 = run_cli(*common, "--on-collision", "suffix", stdin='{"a":2}\n', home=self.home)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertTrue(r2.stdout.strip().endswith("-2.json"))

    def test_from_path_moves_file_to_target(self) -> None:
        """--from-path is verbatim + always-move: source basename becomes the
        target name, and the source is unlinked after a successful write."""
        src = self.cwd / "source.json"
        src.write_text('{"k": 1}\n')
        r = run_cli(
            "save", "--override", "scratch",
            "--from-path", str(src),
            stdin="should-be-ignored",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        target = self.plans_root / "scratch" / "source.json"
        self.assertEqual(r.stdout.strip(), str(target))
        self.assertEqual(target.read_text(), '{"k": 1}\n')
        self.assertFalse(src.exists(), "source must be unlinked after successful move")

    def test_from_path_collision_does_not_unlink_source(self) -> None:
        """Critical invariant: a collision (exit 2) must not destroy the user's
        source file. Otherwise a retry could lose data."""
        src = self.cwd / "victim.json"
        src.write_text('{"k": 3}\n')
        # Pre-create a target with the same basename to force a collision.
        (self.plans_root / "scratch").mkdir(parents=True)
        (self.plans_root / "scratch" / "victim.json").write_text('{"other":1}\n')
        r = run_cli(
            "save", "--override", "scratch",
            "--from-path", str(src),
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertTrue(src.exists(), "source must survive a collision")
        self.assertEqual(src.read_text(), '{"k": 3}\n')

    def test_from_path_missing_source(self) -> None:
        r = run_cli(
            "save", "--override", "scratch",
            "--from-path", str(self.cwd / "does-not-exist.json"),
            home=self.home,
        )
        self.assertEqual(r.returncode, 3)
        self.assertIn("source not found", r.stderr)

    def test_from_path_preserves_bytes_without_trailing_newline(self) -> None:
        """Heredoc input gets a trailing \\n appended if missing; --from-path
        must NOT mutate the file. A binary or strictly-formatted file (e.g.,
        a checksum'd JSON) would be corrupted by a stray newline."""
        src = self.cwd / "no-trailing-newline.json"
        src.write_text('{"k":1}')  # deliberately no trailing newline
        r = run_cli(
            "save", "--override", "scratch",
            "--from-path", str(src),
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        written = Path(r.stdout.strip())
        self.assertEqual(written.read_bytes(), b'{"k":1}')

    def test_from_path_preserves_mtime(self) -> None:
        """shutil.move preserves mtime so the target reflects when the
        artifact was originally produced, not when it was relocated."""
        src = self.cwd / "old.json"
        src.write_text('{"k":1}\n')
        old_mtime = 1_600_000_000.0  # 2020-09-13
        os.utime(src, (old_mtime, old_mtime))
        r = run_cli(
            "save", "--override", "scratch",
            "--from-path", str(src),
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        written = Path(r.stdout.strip())
        self.assertAlmostEqual(written.stat().st_mtime, old_mtime, places=0)

    def test_from_path_keeps_source_filename_verbatim(self) -> None:
        """task-list-builder's pre-named artifact (already encoding date,
        run-id, and slug) lands in ~/plans/<repo>/ with the same basename —
        the whole point of the disk shape is no rename gymnastics."""
        src = self.cwd / "2026-05-21-a3f2-bulk-edit.task-list-builder.json"
        src.write_text('{"tasks":[]}\n')
        r = run_cli(
            "save", "--override", "scratch",
            "--from-path", str(src),
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        target = self.plans_root / "scratch" / src.name
        self.assertEqual(r.stdout.strip(), str(target))
        self.assertTrue(target.exists())
        self.assertFalse(src.exists(), "source should be moved")

    def test_topic_required_without_from_path(self) -> None:
        r = run_cli(
            "save", "--override", "scratch",
            stdin="x\n", home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("--topic is required", r.stderr)

    def test_from_path_rejects_topic_flag(self) -> None:
        """--from-path means verbatim basename; --topic implies renaming, so
        the two are mutually exclusive. Rejecting up-front keeps the LLM from
        ambiguously combining the shapes."""
        src = self.cwd / "report.json"
        src.write_text('{}\n')
        r = run_cli(
            "save", "--override", "scratch",
            "--from-path", str(src), "--topic", "rename me",
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("--topic is incompatible with --from-path", r.stderr)

    def test_from_path_rejects_extension_flag(self) -> None:
        src = self.cwd / "report.json"
        src.write_text('{}\n')
        r = run_cli(
            "save", "--override", "scratch",
            "--from-path", str(src), "--extension", "yaml",
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("--extension is incompatible with --from-path", r.stderr)

    def test_from_path_rejects_date_flag(self) -> None:
        src = self.cwd / "report.json"
        src.write_text('{}\n')
        r = run_cli(
            "save", "--override", "scratch",
            "--from-path", str(src), "--date", "2026-01-01",
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("--date is incompatible with --from-path", r.stderr)

    def test_save_injects_default_agent_and_backlog_status(self) -> None:
        """Bare `save --topic foo` produces 'Agent: claude\\nStatus: backlog\\n' frontmatter."""
        r = run_cli(
            "save", "--override", "testrepo", "--topic", "Test Plan",
            stdin="# Body\nSome plan content.\n",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        saved = Path(r.stdout.strip())
        text = saved.read_text()
        self.assertTrue(
            text.startswith("---\n"),
            f"expected frontmatter at top, got: {text[:200]!r}",
        )
        self.assertIn("Agent: claude", text)
        self.assertIn("Status: backlog", text)
        self.assertIn("# Body", text)

    def test_save_with_agent_codex(self) -> None:
        """`--agent codex` injects Agent: codex."""
        r = run_cli(
            "save", "--override", "testrepo", "--topic", "Test",
            "--agent", "codex",
            stdin="# Body\n",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0)
        text = Path(r.stdout.strip()).read_text()
        self.assertIn("Agent: codex", text)
        self.assertIn("Status: backlog", text)

    def test_save_merges_existing_frontmatter(self) -> None:
        """Body that already has frontmatter is merged, not duplicated."""
        body = "---\nTicket: ENG-1\n---\n\n# Body\n"
        r = run_cli(
            "save", "--override", "testrepo", "--topic", "Test",
            stdin=body,
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        text = Path(r.stdout.strip()).read_text()
        # All three fields present, exactly once (frontmatter has opener and closer)
        self.assertIn("---\n", text)  # has frontmatter markers
        self.assertEqual(text.count("Ticket: ENG-1"), 1)
        self.assertEqual(text.count("Agent: claude"), 1)
        self.assertEqual(text.count("Status: backlog"), 1)
        # Verify the structure: should have opening ---, then fields, then closing ---
        self.assertTrue(text.startswith("---\n"))

    def test_save_existing_agent_status_not_overwritten(self) -> None:
        """If incoming body already declares Agent/Status, the CLI does NOT overwrite."""
        body = "---\nAgent: codex\nStatus: todo\n---\n\n# Body\n"
        r = run_cli(
            "save", "--override", "testrepo", "--topic", "Test",
            "--agent", "claude",
            stdin=body,
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        text = Path(r.stdout.strip()).read_text()
        # Body's values win — --agent only fills when absent
        self.assertIn("Agent: codex", text)
        self.assertIn("Status: todo", text)
        self.assertNotIn("Agent: claude", text)

    def test_save_non_md_extension_no_frontmatter_injection(self) -> None:
        """JSON saves: body byte-for-byte, no frontmatter prepended."""
        body = '{"tasks": []}\n'
        r = run_cli(
            "save", "--override", "testrepo", "--topic", "Test",
            "--extension", "json",
            stdin=body,
            home=self.home,
        )
        self.assertEqual(r.returncode, 0)
        text = Path(r.stdout.strip()).read_text()
        self.assertEqual(text, body)  # exact match — no frontmatter
        self.assertNotIn("Agent:", text)

    def test_save_from_path_no_frontmatter_injection(self) -> None:
        """--from-path preserves source bytes; never injects."""
        source = self.cwd / "src.md"
        source.write_text("# Plain body\nNo frontmatter here.\n")
        r = run_cli(
            "save", "--override", "testrepo",
            "--from-path", str(source),
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        text = Path(r.stdout.strip()).read_text()
        self.assertNotIn("Agent:", text)
        self.assertNotIn("Status:", text)


class TestSaveKind(IsolatedHomeTestCase):
    """`save --kind` injects a `Kind:` frontmatter line on .md saves, validates
    the value, and is fill-if-absent."""

    def test_kind_written_on_md_save(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "checkout", "--kind", "prd",
            stdin="# Checkout\n\nProblem.\n", home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(r.stdout.strip()).read_text()
        self.assertIn("Kind: prd", text)
        self.assertIn("Status: backlog", text)

    def test_no_kind_means_no_kind_line(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "no kind",
            stdin="# X\n", home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(r.stdout.strip()).read_text()
        self.assertNotIn("Kind:", text)

    def test_invalid_kind_rejected(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "bad", "--kind", "blueprint",
            stdin="# X\n", home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid Kind", r.stderr)

    def test_kind_normalized_lowercase(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "caps", "--kind", "Spec",
            stdin="# X\n", home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Kind: spec", Path(r.stdout.strip()).read_text())

    def test_kind_rejected_for_non_md_extension(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "tasks",
            "--kind", "exec-plan", "--extension", "json",
            stdin="{}\n", home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("--kind only applies to .md saves", r.stderr)

    def test_kind_rejected_with_from_path(self) -> None:
        source = self.cwd / "src.md"
        source.write_text("# Body\n")
        r = run_cli(
            "save", "--override", "scratch",
            "--from-path", str(source), "--kind", "spec",
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("--kind is incompatible with --from-path", r.stderr)

    def test_kind_is_fill_if_absent(self) -> None:
        # Body already declares Kind: design — --kind prd must not stomp it.
        r = run_cli(
            "save", "--override", "scratch", "--topic", "has kind", "--kind", "prd",
            stdin="---\nKind: design\n---\n\n# Body\n", home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(r.stdout.strip()).read_text()
        self.assertIn("Kind: design", text)
        self.assertNotIn("Kind: prd", text)


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
        text = target.read_text()
        today = date.today().isoformat()
        # NEW: completion date in frontmatter at the top.
        self.assertTrue(text.startswith("---\n"), "file must start with frontmatter")
        front = text.split("\n---\n", 1)[0]
        self.assertIn(f"Completed on: {today}", front)
        # OLD: bottom stamp must NOT be present.
        self.assertNotIn("*Completed:", text)

    def test_completed_date_override(self) -> None:
        source = self._save_one()
        run_cli(
            "archive", "--override", "scratch", "--file", source.name,
            "--completed-date", "2020-01-15",
            home=self.home,
        )
        text = (self.plans_root / "scratch" / "done" / source.name).read_text()
        # NEW: completion date in frontmatter.
        self.assertTrue(text.startswith("---\n"), "file must start with frontmatter")
        front = text.split("\n---\n", 1)[0]
        self.assertIn("Completed on: 2020-01-15", front)
        # OLD: bottom stamp must NOT be present.
        self.assertNotIn("*Completed:", text)

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

    def test_includes_non_md_files(self) -> None:
        """list must surface files saved with non-.md extensions (e.g. paired
        task-list-builder .json + .md)."""
        run_cli(
            "save", "--override", "scratch", "--topic", "tasks", "--extension", "json",
            stdin='{"tasks": []}\n', home=self.home,
        )
        run_cli(
            "save", "--override", "scratch", "--topic", "tasks",
            stdin="# Tasks\n", home=self.home,
        )
        r = run_cli("list", "--override", "scratch", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        names = r.stdout.strip().split("\n")
        self.assertTrue(any(n.endswith(".json") for n in names))
        self.assertTrue(any(n.endswith(".md") for n in names))

    def test_excludes_dotfiles(self) -> None:
        """A repo's `.plankeeper.json` config sits alongside plans but must
        not appear in `list`."""
        run_cli(
            "save", "--override", "scratch", "--topic", "real plan",
            stdin="x\n", home=self.home,
        )
        # Manually drop a dotfile into the repo dir.
        (self.plans_root / "scratch" / ".plankeeper.json").write_text("{}\n")
        r = run_cli("list", "--override", "scratch", home=self.home)
        self.assertNotIn(".plankeeper.json", r.stdout)
        self.assertIn("real-plan", r.stdout)


class TestListByStatus(IsolatedHomeTestCase):
    """`list --status <set>` filters active plans by Status frontmatter,
    orders by the given tier sequence (newest-first within each), annotates
    each line as `status<TAB>filename`, and reports excluded actives on stderr.
    """

    def _write(self, name: str, status: str | None) -> None:
        d = self.plans_root / "scratch"
        d.mkdir(parents=True, exist_ok=True)
        if status is None:
            (d / name).write_text("# no frontmatter\n", encoding="utf-8")
        else:
            (d / name).write_text(
                f"---\nStatus: {status}\n---\n\n# {name}\n", encoding="utf-8"
            )

    def test_filters_and_orders_by_tier_then_newest(self) -> None:
        self._write("2026-05-28-a.md", "todo")
        self._write("2026-05-29-b.md", "in-progress")
        self._write("2026-05-27-d.md", "in-progress")
        self._write("2026-05-26-e.md", "in-review")
        r = run_cli(
            "list", "--override", "scratch", "--status", "in-progress,todo",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().split("\n")
        # in-progress group first (newest-first within), then todo group.
        self.assertEqual(
            lines,
            [
                "in-progress\t2026-05-29-b.md",
                "in-progress\t2026-05-27-d.md",
                "todo\t2026-05-28-a.md",
            ],
        )

    def test_missing_frontmatter_counts_as_backlog(self) -> None:
        self._write("2026-05-25-f.md", None)
        self._write("2026-05-30-c.md", "backlog")
        r = run_cli(
            "list", "--override", "scratch", "--status", "todo,backlog",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().split("\n")
        self.assertIn("backlog\t2026-05-30-c.md", lines)
        self.assertIn("backlog\t2026-05-25-f.md", lines)

    def test_excluded_actives_summarized_on_stderr(self) -> None:
        self._write("2026-05-28-a.md", "todo")
        self._write("2026-05-29-b.md", "in-progress")
        self._write("2026-05-26-e.md", "in-review")
        r = run_cli(
            "list", "--override", "scratch", "--status", "todo,backlog",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # Only the todo plan shows; in-progress + in-review are hidden.
        self.assertEqual(r.stdout.strip(), "todo\t2026-05-28-a.md")
        self.assertIn("2 other active plan(s) hidden", r.stderr)
        self.assertIn("in-progress×1", r.stderr)
        self.assertIn("in-review×1", r.stderr)

    def test_empty_match_with_hidden_actives(self) -> None:
        self._write("2026-05-29-b.md", "in-progress")
        r = run_cli(
            "list", "--override", "scratch", "--status", "todo,backlog",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")
        self.assertIn("1 other active plan(s) hidden", r.stderr)

    def test_foreign_frontmatter_field_honors_real_status(self) -> None:
        # A foreign field no longer breaks parsing, so the file's real Status
        # is read (not lost to a backlog fallback).
        (self.plans_root / "scratch").mkdir(parents=True, exist_ok=True)
        (self.plans_root / "scratch" / "2026-05-24-g.md").write_text(
            "---\nBogusKey: x\nStatus: todo\n---\n\n# g\n", encoding="utf-8"
        )
        r = run_cli(
            "list", "--override", "scratch", "--status", "todo,backlog",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "todo\t2026-05-24-g.md")

    def test_genuinely_malformed_frontmatter_falls_back_to_backlog(self) -> None:
        # A real parse error (line missing its ':') still can't crash the
        # listing — the file stays visible as backlog.
        (self.plans_root / "scratch").mkdir(parents=True, exist_ok=True)
        (self.plans_root / "scratch" / "2026-05-24-g.md").write_text(
            "---\nStatus todo\n---\n\n# g\n", encoding="utf-8"
        )
        r = run_cli(
            "list", "--override", "scratch", "--status", "todo,backlog",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "backlog\t2026-05-24-g.md")

    def test_no_status_flag_keeps_bare_filename_output(self) -> None:
        self._write("2026-05-28-a.md", "todo")
        r = run_cli("list", "--override", "scratch", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "2026-05-28-a.md")
        self.assertNotIn("\t", r.stdout)


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

    def test_counts_include_non_md_files(self) -> None:
        run_cli(
            "save", "--override", "alpha", "--topic", "a", "--extension", "json",
            stdin="{}\n", home=self.home,
        )
        run_cli(
            "save", "--override", "alpha", "--topic", "b",
            stdin="x\n", home=self.home,
        )
        out = run_cli("list-repos", home=self.home)
        self.assertIn("alpha: active=2", out.stdout)


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
        self.assertEqual(data, {"Ticket": "", "Ticket System": "", "Completed on": "", "Agent": "", "Status": "", "Kind": "", "Created": ""})

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
            "Agent": "",
            "Status": "",
            "Kind": "",
            "Created": "",
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

    def test_unknown_field_is_preserved_not_rejected(self) -> None:
        # Foreign frontmatter (e.g. Obsidian `tags:`) must round-trip, not
        # crash parsing — the serializer preserves it, so it's no longer lost.
        path = self._write_plan("---\nUnknownField: x\n---\n")
        result = run_cli(
            "file-meta", "get", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["UnknownField"], "x")

    def test_frontmatter_parses_agent_and_status(self) -> None:
        """Agent and Status are recognized frontmatter fields."""
        path = self._write_plan(
            "---\n"
            "Agent: codex\n"
            "Status: todo\n"
            "---\n"
            "\n"
            "# Body\n"
        )
        result = run_cli(
            "file-meta", "get", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        meta = json.loads(result.stdout)
        self.assertEqual(meta["Agent"], "codex")
        self.assertEqual(meta["Status"], "todo")

    def test_frontmatter_get_returns_empty_agent_status_when_absent(self) -> None:
        """Files without Agent/Status frontmatter still return empty strings (no KeyError)."""
        path = self._write_plan("# Just a body\n")
        result = run_cli(
            "file-meta", "get", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        meta = json.loads(result.stdout)
        self.assertEqual(meta["Agent"], "")
        self.assertEqual(meta["Status"], "")

    def test_frontmatter_passes_through_foreign_key(self) -> None:
        """A field outside the managed vocabulary is read back, not rejected."""
        path = self._write_plan("---\nFakeKey: nope\n---\n# Body\n")
        result = run_cli(
            "file-meta", "get", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["FakeKey"], "nope")

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

    def test_preserves_foreign_field_through_write(self) -> None:
        # A rewrite (setting a managed field) must not drop foreign frontmatter.
        path = self._write_plan(
            "---\n"
            "tags: [planning, infra]\n"
            "Ticket: KEEP-1\n"
            "---\n"
            "\n# H\n"
        )
        result = run_cli(
            "file-meta", "set",
            "--file", str(path),
            "--completed-on", "2026-05-20",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        new_text = path.read_text(encoding="utf-8")
        self.assertIn("tags: [planning, infra]", new_text)
        self.assertIn("Ticket: KEEP-1", new_text)
        self.assertIn("Completed on: 2026-05-20", new_text)
        # Managed fields serialize in canonical order ahead of foreign ones.
        self.assertLess(new_text.index("Ticket:"), new_text.index("tags:"))

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


class TestFileMetaUpdate(IsolatedHomeTestCase):
    def _write_plan(self, content: str) -> Path:
        path = self.cwd / "plan.md"
        path.write_text(content, encoding="utf-8")
        return path

    def test_file_meta_update_sets_single_field(self) -> None:
        """`update --field Status=todo` writes the field back atomically."""
        plan = self._write_plan(
            "---\nAgent: claude\nStatus: backlog\n---\n\n# Body\n"
        )
        result = run_cli(
            "file-meta", "update", "--file", str(plan), "--field", "Status=todo",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        text = plan.read_text()
        self.assertIn("Status: todo", text)
        self.assertNotIn("Status: backlog", text)
        self.assertIn("Agent: claude", text)  # untouched
        self.assertIn("# Body", text)  # body preserved

    def test_file_meta_update_multiple_fields(self) -> None:
        """Multiple --field flags apply in order."""
        plan = self._write_plan(
            "---\nAgent: claude\nStatus: backlog\n---\n\n# Body\n"
        )
        result = run_cli(
            "file-meta", "update", "--file", str(plan),
            "--field", "Agent=codex",
            "--field", "Status=in-progress",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        text = plan.read_text()
        self.assertIn("Agent: codex", text)
        self.assertIn("Status: in-progress", text)

    def test_file_meta_update_rejects_unknown_key(self) -> None:
        """Whitelist enforced — Foo is not in _FRONTMATTER_FIELDS."""
        plan = self._write_plan("---\nAgent: claude\nStatus: backlog\n---\n\n# Body\n")
        result = run_cli(
            "file-meta", "update", "--file", str(plan), "--field", "Foo=bar",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown frontmatter field", result.stderr)

    def test_file_meta_update_rejects_malformed_field(self) -> None:
        """--field must be key=value; bare 'Status' is a usage error."""
        plan = self._write_plan("---\nAgent: claude\nStatus: backlog\n---\n\n# Body\n")
        result = run_cli(
            "file-meta", "update", "--file", str(plan), "--field", "Status",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be key=value", result.stderr)

    def test_file_meta_update_rejects_file_without_frontmatter(self) -> None:
        """Spec: 'Reject the call if the file has no frontmatter (force user to re-save first).'"""
        plan = self._write_plan("# Just a body, no frontmatter\n")
        result = run_cli(
            "file-meta", "update", "--file", str(plan), "--field", "Status=todo",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("no frontmatter", result.stderr)

    def test_file_meta_update_value_with_equals(self) -> None:
        """Value containing '=' is preserved (split on first = only)."""
        plan = self._write_plan("---\nAgent: claude\nStatus: backlog\n---\n\n# Body\n")
        # Use Ticket since it's freeform; demonstrates split-on-first-=
        result = run_cli(
            "file-meta", "update", "--file", str(plan),
            "--field", "Ticket=ENG-123=draft",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Ticket: ENG-123=draft", plan.read_text())

    def test_file_meta_update_sets_valid_kind(self) -> None:
        """Kind is whitelisted; a valid value writes back (normalized lowercase)."""
        plan = self._write_plan("---\nAgent: claude\nStatus: backlog\n---\n\n# Body\n")
        result = run_cli(
            "file-meta", "update", "--file", str(plan), "--field", "Kind=Exec-Plan",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Kind: exec-plan", plan.read_text())

    def test_file_meta_update_rejects_invalid_kind(self) -> None:
        """An out-of-enum Kind is rejected before any write."""
        plan = self._write_plan("---\nAgent: claude\nStatus: backlog\n---\n\n# Body\n")
        result = run_cli(
            "file-meta", "update", "--file", str(plan), "--field", "Kind=blueprint",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid Kind", result.stderr)
        # File untouched — no Kind line leaked in.
        self.assertNotIn("Kind:", plan.read_text())


class TestTicketSystemConfig(IsolatedHomeTestCase):
    def test_list_no_config(self) -> None:
        result = run_cli(
            "ticket-system-config", "list",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), [])

    def test_save_then_get_redacts_secrets_by_default(self) -> None:
        # Linear: apiKey masked.
        linear_payload = (
            '{"apiKey": "k", "defaults": {"teamId": "t"}, '
            '"cache": {"teams": [{"id": "t", "name": "Eng"}]}}'
        )
        result = run_cli(
            "ticket-system-config", "save", "--name", "linear",
            stdin=linear_payload, home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(
            "ticket-system-config", "get", "--name", "linear",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiKey"], "***redacted***")
        self.assertEqual(data["defaults"]["teamId"], "t")

        # Jira: apiToken masked.
        jira_payload = (
            '{"site": "x.atlassian.net", "email": "p@x.com", "apiToken": "j", '
            '"defaults": {"projectKey": "HERDS"}}'
        )
        result = run_cli(
            "ticket-system-config", "save", "--name", "jira",
            stdin=jira_payload, home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(
            "ticket-system-config", "get", "--name", "jira",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiToken"], "***redacted***")
        self.assertEqual(data["defaults"]["projectKey"], "HERDS")

    def test_get_show_secrets_reveals_credentials(self) -> None:
        # Linear: apiKey visible with --show-secrets.
        run_cli(
            "ticket-system-config", "save", "--name", "linear",
            stdin='{"apiKey": "k", "defaults": {"teamId": "t"}}',
            home=self.home, cwd=self.cwd,
        )
        result = run_cli(
            "ticket-system-config", "get", "--name", "linear", "--show-secrets",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiKey"], "k")

        # Jira: apiToken visible with --show-secrets.
        run_cli(
            "ticket-system-config", "save", "--name", "jira",
            stdin='{"site": "x.atlassian.net", "email": "p@x.com", "apiToken": "j"}',
            home=self.home, cwd=self.cwd,
        )
        result = run_cli(
            "ticket-system-config", "get", "--name", "jira", "--show-secrets",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiToken"], "j")

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


class TestTicketApiArgValidation(IsolatedHomeTestCase):
    """Verify cmd_ticket_api rejects calls with missing required flags
    before any network call is attempted."""

    def test_linear_viewer_without_api_key_exits_2(self) -> None:
        r = run_cli(
            "ticket-api", "viewer", "--name", "linear",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("--api-key", r.stderr)

    def test_jira_viewer_without_site_exits_2(self) -> None:
        r = run_cli(
            "ticket-api", "viewer", "--name", "jira",
            "--email", "p@x.com", "--api-key", "tok",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("--site", r.stderr)

    def test_jira_components_without_project_key_exits_2(self) -> None:
        r = run_cli(
            "ticket-api", "components", "--name", "jira",
            "--site", "x.atlassian.net",
            "--email", "p@x.com", "--api-key", "tok",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("--project-key", r.stderr)

    def test_jira_invalid_site_exits_2(self) -> None:
        r = run_cli(
            "ticket-api", "viewer", "--name", "jira",
            "--site", "https://x.atlassian.net",  # scheme not allowed
            "--email", "p@x.com", "--api-key", "tok",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("bare hostname", r.stderr)


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


class TestTicketSystemConfigRefreshLinear(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.plans_root = self.home / "plans"
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()
        # Tests in this class patch Path.home directly because they
        # call into module-level functions, not subprocess.
        self._home_patch = patch.object(self.cli.Path, "home", return_value=self.home)
        self._home_patch.start()
        self._cwd_patch = patch("os.getcwd", return_value=str(self.cwd))
        self._cwd_patch.start()

    def tearDown(self) -> None:
        self._home_patch.stop()
        self._cwd_patch.stop()
        self._tmp.cleanup()

    def _mock_response(self, body: dict):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.status = 200
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_refresh_writes_all_kinds_into_cache(self) -> None:
        # Seed existing config with credentials and defaults.
        self.cli.save_config("workdir", {"linear": {
            "apiKey": "k",
            "defaults": {"teamId": "t1"},
            "cache": {"refreshedAt": "2020-01-01T00:00:00Z"},
        }})
        teams = {"data": {"teams": {
            "nodes": [{"id": "t1", "name": "Engineering"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        projects = {"data": {"projects": {
            "nodes": [{"id": "p1", "name": "Backend",
                       "teams": {"nodes": [{"id": "t1"}]}}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        labels = {"data": {"issueLabels": {
            "nodes": [{"id": "l1", "name": "plan", "team": None}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        users = {"data": {"users": {
            "nodes": [{"id": "u1", "name": "Paul", "email": "p@x.com"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                self._mock_response(teams),
                self._mock_response(projects),
                self._mock_response(labels),
                self._mock_response(users),
            ],
        ):
            self.cli.refresh_linear_cache(api_key="k")
        config = self.cli.load_config("workdir")
        cache = config["linear"]["cache"]
        self.assertEqual(len(cache["teams"]), 1)
        self.assertEqual(cache["teams"][0]["name"], "Engineering")
        self.assertEqual(len(cache["projects"]), 1)
        self.assertEqual(len(cache["labels"]), 1)
        self.assertEqual(len(cache["users"]), 1)
        # refreshedAt updated to a recent ISO 8601 timestamp.
        self.assertNotEqual(cache["refreshedAt"], "2020-01-01T00:00:00Z")
        self.assertRegex(cache["refreshedAt"], r"\d{4}-\d{2}-\d{2}T")

    def test_refresh_warns_when_defaults_id_missing_from_cache(self) -> None:
        self.cli.save_config("workdir", {"linear": {
            "apiKey": "k",
            "defaults": {"teamId": "t-deleted", "teamName": "Gone"},
        }})
        teams = {"data": {"teams": {
            "nodes": [{"id": "t1", "name": "Engineering"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        empty_projects = {"data": {"projects": {
            "nodes": [], "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        labels_empty = {"data": {"issueLabels": {
            "nodes": [], "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        users_empty = {"data": {"users": {
            "nodes": [], "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                self._mock_response(teams),
                self._mock_response(empty_projects),
                self._mock_response(labels_empty),
                self._mock_response(users_empty),
            ],
        ):
            warnings = self.cli.refresh_linear_cache(api_key="k")
        self.assertTrue(any("t-deleted" in w for w in warnings))


class TestPushLinearCreate(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.plans_root = self.home / "plans"
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.cwd, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin",
             "https://github.com/herds-social/herds.git"],
            cwd=self.cwd, check=True,
        )
        self._home_patch = patch.object(self.cli.Path, "home", return_value=self.home)
        self._home_patch.start()
        self._cwd_patch = patch("os.getcwd", return_value=str(self.cwd))
        self._cwd_patch.start()

    def tearDown(self) -> None:
        self._home_patch.stop()
        self._cwd_patch.stop()
        self._tmp.cleanup()

    def _seed_config(self):
        self.cli.save_config("herds", {"linear": {
            "apiKey": "lin_test",
            "defaults": {
                "teamId": "t1", "teamName": "Engineering",
                "projectId": "p1", "projectName": "Backend",
                "assigneeId": "u1", "assigneeName": "Paul",
                "labelIds": ["l1"], "labelNames": ["plan"],
            },
            "cache": {"refreshedAt": "2026-05-20T00:00:00Z"},
        }})

    def _seed_plan(self, frontmatter: str = "", h1: str = "# Multi-Event Design"):
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        path = repo_dir / "2026-05-20-multi-event-design.md"
        path.write_text(f"{frontmatter}{h1}\n\n## Context\n\nBody text.\n", encoding="utf-8")
        return path

    def _mock_create_response(self):
        body = {"data": {"issueCreate": {
            "success": True,
            "issue": {
                "id": "uuid-1",
                "identifier": "ENG-123",
                "url": "https://linear.app/herds/issue/ENG-123/multi-event-design",
                "title": "Multi-Event Design",
            },
        }}}
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_create_sends_expected_payload(self) -> None:
        self._seed_config()
        path = self._seed_plan()
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_create_response(),
        ) as mock_open:
            result = self.cli.push_subcommand(name="linear", file_path=str(path), force_new=False)
        self.assertEqual(result["action"], "create")
        self.assertEqual(result["id"], "ENG-123")
        # Inspect the GraphQL request payload.
        call = mock_open.call_args
        req = call[0][0]
        sent = json.loads(req.data.decode("utf-8"))
        self.assertIn("issueCreate", sent["query"])
        variables_input = sent["variables"]["input"]
        self.assertEqual(variables_input["title"], "Multi-Event Design")
        self.assertEqual(variables_input["teamId"], "t1")
        self.assertEqual(variables_input["projectId"], "p1")
        self.assertEqual(variables_input["assigneeId"], "u1")
        self.assertEqual(variables_input["labelIds"], ["l1"])
        # Description must start with "Repo: ..." line.
        self.assertTrue(variables_input["description"].startswith("Repo: herds-social/herds\n"))
        # And contain the plan body.
        self.assertIn("## Context", variables_input["description"])

    def test_create_strips_existing_frontmatter_from_description(self) -> None:
        self._seed_config()
        path = self._seed_plan(frontmatter="---\nTicket: \nTicket System: \n---\n\n")
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_create_response(),
        ) as mock_open:
            self.cli.push_subcommand(name="linear", file_path=str(path), force_new=False)
        sent = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        # The "---" lines must not appear in the description.
        self.assertNotIn("---", sent["variables"]["input"]["description"])

    def test_create_aborts_if_description_exceeds_limit(self) -> None:
        self._seed_config()
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        big_body = "x" * 70_000
        path = repo_dir / "2026-05-20-big.md"
        path.write_text(f"# Big\n\n{big_body}\n", encoding="utf-8")
        with self.assertRaises(self.cli.PlanKeeperCliError) as ctx:
            self.cli.push_subcommand(name="linear", file_path=str(path), force_new=False)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("65000", str(ctx.exception))


class TestPushLinearUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.plans_root = self.home / "plans"
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.cwd, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin",
             "https://github.com/herds-social/herds.git"],
            cwd=self.cwd, check=True,
        )
        self._home_patch = patch.object(self.cli.Path, "home", return_value=self.home)
        self._home_patch.start()
        self._cwd_patch = patch("os.getcwd", return_value=str(self.cwd))
        self._cwd_patch.start()
        self.cli.save_config("herds", {"linear": {
            "apiKey": "lin_test",
            "defaults": {"teamId": "t1"},
            "cache": {"refreshedAt": "now"},
        }})

    def tearDown(self) -> None:
        self._home_patch.stop()
        self._cwd_patch.stop()
        self._tmp.cleanup()

    def _mock_update_response(self):
        body = {"data": {"issueUpdate": {
            "success": True,
            "issue": {
                "id": "uuid-1",
                "identifier": "ENG-123",
                "url": "https://linear.app/herds/issue/ENG-123/foo",
                "title": "Updated Title",
            },
        }}}
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_update_omits_team_project_assignee_labels(self) -> None:
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        path = repo_dir / "plan.md"
        path.write_text(
            "---\nTicket: ENG-123\nTicket System: linear\n---\n\n"
            "# Updated Title\n\n## Body\n",
            encoding="utf-8",
        )
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_update_response(),
        ) as mock_open:
            result = self.cli.push_subcommand(name="linear", file_path=str(path), force_new=False)
        self.assertEqual(result["action"], "update")
        self.assertEqual(result["id"], "ENG-123")
        sent = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        self.assertIn("issueUpdate", sent["query"])
        input_dict = sent["variables"]["input"]
        self.assertEqual(set(input_dict.keys()), {"title", "description"})  # nothing else
        self.assertEqual(sent["variables"]["id"], "ENG-123")

    def test_update_uses_force_new_when_set(self) -> None:
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        path = repo_dir / "plan.md"
        path.write_text(
            "---\nTicket: OLD-1\nTicket System: linear\n---\n\n# T\n",
            encoding="utf-8",
        )
        # With force_new=True, this should call create, not update.
        body = {"data": {"issueCreate": {
            "success": True, "issue": {
                "id": "u2", "identifier": "ENG-200",
                "url": "https://x", "title": "T",
            },
        }}}
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=m) as mock_open:
            result = self.cli.push_subcommand(name="linear", file_path=str(path), force_new=True)
        self.assertEqual(result["action"], "create")
        sent = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        self.assertIn("issueCreate", sent["query"])


class TestTicketApiJiraViewer(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()

    def _mock_response(self, body: dict):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_viewer_calls_myself_and_returns_identity(self) -> None:
        response = {
            "accountId": "5e8f", "emailAddress": "p@x.com", "displayName": "Paul",
        }
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_response(response),
        ) as mock_open:
            result = self.cli.jira_viewer(
                site="herds.atlassian.net", email="p@x.com", api_token="tok",
            )
        self.assertEqual(result, response)
        req = mock_open.call_args[0][0]
        self.assertEqual(req.full_url, "https://herds.atlassian.net/rest/api/3/myself")
        # Basic auth header present.
        self.assertTrue(req.get_header("Authorization").startswith("Basic "))
        encoded = req.get_header("Authorization")[len("Basic "):]
        decoded = base64.b64decode(encoded).decode("utf-8")
        self.assertEqual(decoded, "p@x.com:tok")


class TestTicketApiJiraLists(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self.site, self.email, self.token = "herds.atlassian.net", "p@x.com", "tok"

    def _mock_response(self, body):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_projects_paginates(self) -> None:
        page1 = {
            "values": [{"key": "HERDS", "id": "1", "name": "Herds"}],
            "isLast": False,
            "startAt": 0,
            "maxResults": 1,
            "total": 2,
        }
        page2 = {
            "values": [{"key": "INT", "id": "2", "name": "Internal"}],
            "isLast": True,
            "startAt": 1,
            "maxResults": 1,
            "total": 2,
        }
        with patch(
            "urllib.request.urlopen",
            side_effect=[self._mock_response(page1), self._mock_response(page2)],
        ) as mock_open:
            result = self.cli.jira_projects(self.site, self.email, self.token)
        self.assertEqual(len(result), 2)
        self.assertEqual(mock_open.call_count, 2)
        # Second call should have startAt=50 (pagination uses page size 50 in helper)
        url2 = mock_open.call_args_list[1][0][0].full_url
        self.assertIn("startAt=50", url2)

    def test_components_per_project(self) -> None:
        response = [{"id": "10001", "name": "Backend"}]
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_response(response),
        ) as mock_open:
            result = self.cli.jira_components(self.site, self.email, self.token, "HERDS")
        self.assertEqual(result, [{"id": "10001", "name": "Backend", "projectKey": "HERDS"}])
        self.assertIn("/project/HERDS/components", mock_open.call_args[0][0].full_url)

    def test_users_per_project(self) -> None:
        response = [
            {"accountId": "5e8f", "displayName": "Paul", "emailAddress": "p@x.com"},
        ]
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_response(response),
        ) as mock_open:
            result = self.cli.jira_users(self.site, self.email, self.token, "HERDS")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["accountId"], "5e8f")
        self.assertIn(
            "/user/assignable/multiProjectSearch",
            mock_open.call_args[0][0].full_url,
        )
        self.assertIn("projectKeys=HERDS", mock_open.call_args[0][0].full_url)

    def test_issuetypes_per_project(self) -> None:
        response = [{"id": "10001", "name": "Task"}]
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_response(response),
        ) as mock_open:
            result = self.cli.jira_issuetypes(self.site, self.email, self.token, "1")
        self.assertEqual(result, [{"id": "10001", "name": "Task", "projectId": "1"}])
        self.assertIn("projectId=1", mock_open.call_args[0][0].full_url)


class TestTicketSystemConfigRefreshJira(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()
        self._home_patch = patch.object(self.cli.Path, "home", return_value=self.home)
        self._home_patch.start()
        self._cwd_patch = patch("os.getcwd", return_value=str(self.cwd))
        self._cwd_patch.start()

    def tearDown(self) -> None:
        self._home_patch.stop()
        self._cwd_patch.stop()
        self._tmp.cleanup()

    def _mock_response(self, body):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_refresh_populates_jira_cache(self) -> None:
        self.cli.save_config("workdir", {"jira": {
            "site": "herds.atlassian.net",
            "email": "p@x.com",
            "apiToken": "tok",
            "defaults": {"projectKey": "HERDS"},
        }})
        # The refresh fetches: projects, then for each project: components, users, issuetypes.
        # Assume one project to keep the test tractable.
        projects = {
            "values": [{"key": "HERDS", "id": "1", "name": "Herds"}],
            "isLast": True, "startAt": 0, "maxResults": 1, "total": 1,
        }
        components = [{"id": "10001", "name": "Backend"}]
        users = []  # empty for simplicity
        issuetypes = [{"id": "20001", "name": "Task"}]
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                self._mock_response(projects),
                self._mock_response(components),
                self._mock_response(users),
                self._mock_response(issuetypes),
            ],
        ):
            self.cli.refresh_jira_cache(site="herds.atlassian.net", email="p@x.com", api_token="tok")
        config = self.cli.load_config("workdir")
        cache = config["jira"]["cache"]
        self.assertEqual(len(cache["projects"]), 1)
        self.assertEqual(cache["components"][0]["projectKey"], "HERDS")
        self.assertEqual(cache["issueTypes"][0]["name"], "Task")
        self.assertRegex(cache["refreshedAt"], r"\d{4}-\d{2}-\d{2}T")


class TestPushJira(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.plans_root = self.home / "plans"
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.cwd, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin",
             "https://github.com/herds-social/herds.git"],
            cwd=self.cwd, check=True,
        )
        self._home_patch = patch.object(self.cli.Path, "home", return_value=self.home)
        self._home_patch.start()
        self._cwd_patch = patch("os.getcwd", return_value=str(self.cwd))
        self._cwd_patch.start()
        self.cli.save_config("herds", {"jira": {
            "site": "herds.atlassian.net",
            "email": "p@x.com",
            "apiToken": "tok",
            "defaults": {
                "projectKey": "HERDS",
                "componentIds": ["10001"], "componentNames": ["Backend"],
                "assigneeAccountId": "5e8f", "assigneeName": "Paul",
                "issueType": "Task",
                "labels": ["plan"],
            },
        }})

    def tearDown(self) -> None:
        self._home_patch.stop()
        self._cwd_patch.stop()
        self._tmp.cleanup()

    def _mock_create_response(self):
        body = {"key": "HERDS-100", "id": "9999"}
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def _mock_update_response(self):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=b"")  # 204 No Content has empty body
        return m

    def test_create_wraps_body_in_adf_paragraph(self) -> None:
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        path = repo_dir / "plan.md"
        path.write_text("# Title\n\n## Body\n\nWords.\n", encoding="utf-8")
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_create_response(),
        ) as mock_open:
            result = self.cli.push_subcommand(name="jira", file_path=str(path), force_new=False)
        self.assertEqual(result["action"], "create")
        self.assertEqual(result["id"], "HERDS-100")
        self.assertEqual(result["url"], "https://herds.atlassian.net/browse/HERDS-100")
        sent = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        # ADF is a JSON object with type=doc, content=[paragraph], text=our composed desc.
        adf = sent["fields"]["description"]
        self.assertEqual(adf["type"], "doc")
        self.assertEqual(adf["content"][0]["type"], "paragraph")
        adf_text = adf["content"][0]["content"][0]["text"]
        self.assertTrue(adf_text.startswith("Repo: herds-social/herds\n"))
        self.assertIn("## Body", adf_text)
        # Project + components + assignee + issue type + labels all sent.
        self.assertEqual(sent["fields"]["project"]["key"], "HERDS")
        self.assertEqual(sent["fields"]["components"], [{"id": "10001"}])
        self.assertEqual(sent["fields"]["assignee"]["accountId"], "5e8f")
        self.assertEqual(sent["fields"]["issuetype"]["name"], "Task")
        self.assertEqual(sent["fields"]["labels"], ["plan"])

    def test_update_omits_components_assignee_labels(self) -> None:
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        path = repo_dir / "plan.md"
        path.write_text(
            "---\nTicket: HERDS-100\nTicket System: jira\n---\n\n# T\n",
            encoding="utf-8",
        )
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_update_response(),
        ) as mock_open:
            result = self.cli.push_subcommand(name="jira", file_path=str(path), force_new=False)
        self.assertEqual(result["action"], "update")
        self.assertEqual(result["id"], "HERDS-100")
        # Request was a PUT to /rest/api/3/issue/HERDS-100
        req = mock_open.call_args[0][0]
        self.assertEqual(req.method, "PUT")
        self.assertIn("/rest/api/3/issue/HERDS-100", req.full_url)
        sent = json.loads(req.data.decode("utf-8"))
        # Only summary + description, nothing else.
        self.assertEqual(set(sent["fields"].keys()), {"summary", "description"})


class TestGroundcrewFetch(IsolatedHomeTestCase):
    """Tests for the groundcrew-fetch subcommand."""

    def test_groundcrew_fetch_emits_array_with_translated_status(self):
        """Each active plan becomes one JSON issue with correct status mapping."""
        with tempfile.TemporaryDirectory() as home:
            plans = Path(home) / "plans"
            for repo, name, status in [
                ("groundcrew", "2026-01-01-a.md", "todo"),
                ("groundcrew", "2026-01-02-b.md", "backlog"),
                ("herds", "2026-01-03-c.md", "in-progress"),
            ]:
                d = plans / repo
                d.mkdir(parents=True, exist_ok=True)
                (d / name).write_text(
                    f"---\nAgent: claude\nStatus: {status}\n---\n# {name}\nDesc.\n"
                )
            # done/ subdir — must NOT appear in fetch output
            (plans / "groundcrew" / "done").mkdir()
            (plans / "groundcrew" / "done" / "2025-12-31-old.md").write_text(
                "---\nAgent: claude\nStatus: done\n---\n# Done\n"
            )

            result = run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            issues = json.loads(result.stdout)
            self.assertEqual(len(issues), 3)  # done/ excluded

            # Ids are synthesized (plan-<digits>), not the filename stem, so
            # key the lookup on each plan's stem recovered from sourceRef.path.
            by_stem = {Path(i["sourceRef"]["path"]).stem: i for i in issues}
            self.assertEqual(by_stem["2026-01-01-a"]["status"], "todo")
            self.assertEqual(by_stem["2026-01-02-b"]["status"], "other")  # backlog → other
            self.assertEqual(by_stem["2026-01-03-c"]["status"], "in-progress")
            self.assertEqual(by_stem["2026-01-01-a"]["repository"], "groundcrew")
            self.assertEqual(by_stem["2026-01-03-c"]["repository"], "herds")
            self.assertEqual(by_stem["2026-01-01-a"]["model"], "claude")
            for issue in issues:
                self.assertRegex(issue["id"], r"^plan-\d+$")

    def test_groundcrew_fetch_uses_h1_as_title(self):
        """Title comes from the first H1 in the body, not the filename."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "groundcrew"
            d.mkdir(parents=True)
            (d / "2026-01-01-x.md").write_text(
                "---\nAgent: claude\nStatus: todo\n---\n# The Real Title\nbody\n"
            )
            result = run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0)
            issues = json.loads(result.stdout)
            self.assertEqual(issues[0]["title"], "The Real Title")

    def test_groundcrew_fetch_sets_source_ref(self):
        """sourceRef.path is the absolute path to the plan file."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            plan = d / "2026-01-01-x.md"
            plan.write_text("---\nAgent: claude\nStatus: todo\n---\n# T\n")
            result = run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd)
            issues = json.loads(result.stdout)
            self.assertEqual(issues[0]["sourceRef"]["path"], str(plan.resolve()))

    def test_groundcrew_fetch_stamps_id_into_frontmatter(self):
        """fetch mirrors the synthesized id into the Ticket / Ticket System
        pair (Ticket System: groundcrew) so a human can see the mapping."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "herds"
            d.mkdir(parents=True)
            plan = d / "2026-04-30-typed-models.md"
            plan.write_text("---\nAgent: claude\nStatus: todo\n---\n# Typed\n")
            issues = json.loads(
                run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd).stdout
            )
            text = plan.read_text()
            self.assertIn(f"Ticket: {issues[0]['id']}", text)
            self.assertIn("Ticket System: groundcrew", text)

    def test_groundcrew_fetch_stamp_is_idempotent(self):
        """Once stamped, repeated fetches don't rewrite the file."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            plan = d / "2026-01-01-x.md"
            plan.write_text("---\nAgent: claude\nStatus: todo\n---\n# T\n")
            run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd)
            after_first = plan.read_text()
            run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(plan.read_text(), after_first)

    def test_groundcrew_fetch_stamp_preserves_foreign_fields(self):
        """Stamping the id keeps foreign frontmatter (Obsidian tags etc.)."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            plan = d / "2026-01-01-x.md"
            plan.write_text(
                "---\ntags: [infra]\nAgent: claude\nStatus: todo\n---\n# T\n"
            )
            issues = json.loads(
                run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd).stdout
            )
            text = plan.read_text()
            self.assertIn("tags: [infra]", text)
            self.assertIn(f"Ticket: {issues[0]['id']}", text)

    def test_groundcrew_fetch_heals_stale_stamp(self):
        """A stale groundcrew Ticket is corrected to the canonical (hash) id —
        the frontmatter is a mirror, never the source of truth."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            plan = d / "2026-01-01-x.md"
            plan.write_text(
                "---\nTicket: plan-999999\nTicket System: groundcrew\n"
                "Agent: claude\nStatus: todo\n---\n# T\n"
            )
            issues = json.loads(
                run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd).stdout
            )
            self.assertNotEqual(issues[0]["id"], "plan-999999")
            self.assertIn(f"Ticket: {issues[0]['id']}", plan.read_text())
            self.assertNotIn("plan-999999", plan.read_text())

    def test_groundcrew_fetch_does_not_clobber_external_ticket(self):
        """A plan already filed in Linear/Jira keeps its tracker reference;
        groundcrew dispatches via the recomputed id without touching it."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            plan = d / "2026-01-01-x.md"
            plan.write_text(
                "---\nTicket: ENG-1\nTicket System: linear\n"
                "Agent: claude\nStatus: todo\n---\n# T\n"
            )
            issues = json.loads(
                run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd).stdout
            )
            text = plan.read_text()
            self.assertIn("Ticket: ENG-1", text)
            self.assertIn("Ticket System: linear", text)
            self.assertRegex(issues[0]["id"], r"^plan-\d+$")
            # The recomputed id still resolves the plan.
            r = run_cli("groundcrew-resolve-one", issues[0]["id"],
                        home=Path(home), cwd=self.cwd)
            self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_groundcrew_fetch_skips_files_without_frontmatter(self):
        """A bare .md (no frontmatter) is skipped, not crashed on."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            (d / "good.md").write_text("---\nAgent: claude\nStatus: todo\n---\n# G\n")
            (d / "bare.md").write_text("# Just a body\n")
            result = run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0)
            issues = json.loads(result.stdout)
            stems = {Path(i["sourceRef"]["path"]).stem for i in issues}
            self.assertIn("good", stems)
            self.assertNotIn("bare", stems)

    def test_groundcrew_fetch_includes_plan_with_foreign_frontmatter(self):
        """Regression: a plan with extra frontmatter (e.g. Obsidian tags) must
        not silently vanish from the queue — it parses and is dispatchable."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "herds"
            d.mkdir(parents=True)
            (d / "2026-01-01-tagged.md").write_text(
                "---\ntags: [infra]\nAgent: claude\nStatus: todo\n---\n# Tagged\n"
            )
            result = run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            issues = json.loads(result.stdout)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["status"], "todo")
            self.assertRegex(issues[0]["id"], r"^plan-\d+$")

    def test_groundcrew_fetch_empty_when_no_plans(self):
        """`[]` (not error) when ~/plans/ is empty or missing."""
        with tempfile.TemporaryDirectory() as home:
            result = run_cli("groundcrew-fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout), [])


class TestGroundcrewId(IsolatedHomeTestCase):
    """Synthesized groundcrew ticket id (stateless deterministic hash)."""

    def setUp(self) -> None:
        super().setUp()
        self.cli = _import_cli_module()

    def test_id_matches_groundcrew_ticket_shape(self):
        # groundcrew enforces TICKET_RE = /^[a-z][\\da-z]*-\\d+$/.
        self.assertRegex(
            self.cli.groundcrew_id("herds", "2026-04-30-foo"),
            r"^[a-z][\da-z]*-\d+$",
        )

    def test_id_is_stable_across_calls(self):
        self.assertEqual(
            self.cli.groundcrew_id("herds", "2026-04-30-foo"),
            self.cli.groundcrew_id("herds", "2026-04-30-foo"),
        )

    def test_id_differs_by_repo(self):
        # Same stem in two repos must not collide: groundcrew uses the bare
        # id as a git branch and run-state filename, with no repo qualifier.
        self.assertNotEqual(
            self.cli.groundcrew_id("r1", "2026-01-01-x"),
            self.cli.groundcrew_id("r2", "2026-01-01-x"),
        )

    def test_id_differs_by_stem(self):
        self.assertNotEqual(
            self.cli.groundcrew_id("r", "2026-01-01-x"),
            self.cli.groundcrew_id("r", "2026-01-02-y"),
        )

    def test_collision_guard_raises_with_both_paths(self):
        issues = [
            {"id": "plan-1", "sourceRef": {"path": "/a.md"}},
            {"id": "plan-1", "sourceRef": {"path": "/b.md"}},
        ]
        with self.assertRaises(self.cli.PlanKeeperCliError) as ctx:
            self.cli._assert_no_groundcrew_id_collisions(issues)
        self.assertIn("/a.md", str(ctx.exception))
        self.assertIn("/b.md", str(ctx.exception))

    def test_collision_guard_passes_distinct_ids(self):
        issues = [
            {"id": "plan-1", "sourceRef": {"path": "/a.md"}},
            {"id": "plan-2", "sourceRef": {"path": "/b.md"}},
        ]
        self.cli._assert_no_groundcrew_id_collisions(issues)  # no raise


class TestGroundcrewResolveOne(IsolatedHomeTestCase):
    """Tests for the groundcrew-resolve-one subcommand."""

    def setUp(self) -> None:
        super().setUp()
        # A real caller passes resolve-one an id it got from a prior fetch.
        # Tests reproduce that by computing the same id via the module fn.
        self.cli = _import_cli_module()

    def test_groundcrew_resolve_one_finds_active_plan(self):
        d = self.home / "plans" / "r"
        d.mkdir(parents=True)
        plan = d / "2026-01-01-x.md"
        plan.write_text("---\nAgent: claude\nStatus: todo\n---\n# Title\n")
        ticket = self.cli.groundcrew_id("r", "2026-01-01-x")
        result = run_cli("groundcrew-resolve-one", ticket,
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        issue = json.loads(result.stdout)
        self.assertEqual(issue["id"], ticket)
        self.assertEqual(issue["status"], "todo")
        self.assertEqual(issue["title"], "Title")
        self.assertEqual(issue["sourceRef"]["path"], str(plan.resolve()))

    def test_groundcrew_resolve_one_finds_done_plan(self):
        d = self.home / "plans" / "r" / "done"
        d.mkdir(parents=True)
        (d / "2025-12-31-old.md").write_text(
            "---\nAgent: claude\nStatus: done\n---\n# Old\n"
        )
        # Archived plan's repo is the grandparent dir ("r"), so its id is
        # keyed on ("r", stem) — same as when it was active.
        ticket = self.cli.groundcrew_id("r", "2025-12-31-old")
        result = run_cli("groundcrew-resolve-one", ticket,
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0)
        issue = json.loads(result.stdout)
        self.assertEqual(issue["status"], "done")

    def test_groundcrew_resolve_one_missing_returns_exit_3(self):
        """Spec: 'prints nothing for "not found", or exits 3.' We pick exit 3."""
        (self.home / "plans" / "r").mkdir(parents=True)
        result = run_cli("groundcrew-resolve-one", "does-not-exist",
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")  # nothing on stdout

    def test_groundcrew_resolve_one_rejects_path_separator(self):
        """ID can't contain '/' — defends against ../../etc/passwd-style inputs."""
        result = run_cli("groundcrew-resolve-one", "../escape",
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid id", result.stderr)

    def test_groundcrew_resolve_one_done_plan_has_correct_repository(self):
        """Regression: archived plans must report their repo name, not 'done'."""
        d = self.home / "plans" / "myrepo" / "done"
        d.mkdir(parents=True)
        (d / "2025-12-31-old.md").write_text(
            "---\nAgent: claude\nStatus: done\n---\n# Old\n"
        )
        ticket = self.cli.groundcrew_id("myrepo", "2025-12-31-old")
        result = run_cli("groundcrew-resolve-one", ticket,
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        issue = json.loads(result.stdout)
        self.assertEqual(issue["repository"], "myrepo")

    def test_groundcrew_resolve_one_deferred_plan_has_correct_repository(self):
        """Regression: paused plans must report their repo name, not 'deferred'."""
        d = self.home / "plans" / "myrepo" / "deferred"
        d.mkdir(parents=True)
        (d / "2025-06-15-paused.md").write_text(
            "---\nAgent: claude\nStatus: backlog\n---\n# Paused\n"
        )
        ticket = self.cli.groundcrew_id("myrepo", "2025-06-15-paused")
        result = run_cli("groundcrew-resolve-one", ticket,
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        issue = json.loads(result.stdout)
        self.assertEqual(issue["repository"], "myrepo")

    def test_groundcrew_resolve_one_round_trips_fetched_id(self):
        """The id fetch emits resolves back to the exact same plan file."""
        d = self.home / "plans" / "herds"
        d.mkdir(parents=True)
        plan = d / "2026-04-30-notification-service-typed-models.md"
        plan.write_text("---\nAgent: claude\nStatus: todo\n---\n# Typed models\n")

        issues = json.loads(
            run_cli("groundcrew-fetch", home=self.home, cwd=self.cwd).stdout
        )
        ticket = issues[0]["id"]
        result = run_cli("groundcrew-resolve-one", ticket,
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        issue = json.loads(result.stdout)
        self.assertEqual(issue["id"], ticket)
        self.assertEqual(issue["sourceRef"]["path"], str(plan.resolve()))


class TestGroundcrewMarkInProgress(IsolatedHomeTestCase):
    """Tests for the groundcrew-mark-in-progress subcommand."""

    def test_groundcrew_mark_in_progress_flips_status(self):
        d = self.home / "plans" / "r"
        d.mkdir(parents=True)
        plan = d / "2026-01-01-x.md"
        plan.write_text(
            "---\nAgent: claude\nStatus: todo\n---\n# Title\n"
        )
        result = run_cli(
            "groundcrew-mark-in-progress",
            home=self.home, cwd=self.cwd,
            stdin=json.dumps({"path": str(plan)}),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Status: in-progress", plan.read_text())
        self.assertNotIn("Status: todo", plan.read_text())

    def test_groundcrew_mark_in_progress_rejects_missing_path(self):
        """Path inside PLAN_ROOT but file doesn't exist → exit 3 (not-found)."""
        (self.home / "plans" / "r").mkdir(parents=True)
        missing = self.home / "plans" / "r" / "nonexistent.md"
        result = run_cli(
            "groundcrew-mark-in-progress",
            home=self.home, cwd=self.cwd,
            stdin=json.dumps({"path": str(missing)}),
        )
        self.assertEqual(result.returncode, 3)

    def test_groundcrew_mark_in_progress_rejects_path_outside_plan_root(self):
        """Path outside PLAN_ROOT → exit 2 (validation), even if file exists."""
        outside = self.home / "outside.md"
        outside.write_text("---\nStatus: todo\n---\n# Outside\n")
        result = run_cli(
            "groundcrew-mark-in-progress",
            home=self.home, cwd=self.cwd,
            stdin=json.dumps({"path": str(outside)}),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("outside PLAN_ROOT", result.stderr)
        # File must NOT have been mutated.
        self.assertIn("Status: todo", outside.read_text())

    def test_groundcrew_mark_in_progress_rejects_non_absolute_path(self):
        """Relative path → exit 2 (validation) — never resolved against cwd."""
        result = run_cli(
            "groundcrew-mark-in-progress",
            home=self.home, cwd=self.cwd,
            stdin=json.dumps({"path": "relative/file.md"}),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be absolute", result.stderr)

    def test_groundcrew_mark_in_progress_rejects_non_md_suffix(self):
        """Path inside PLAN_ROOT but not .md → exit 2."""
        d = self.home / "plans" / "r"
        d.mkdir(parents=True)
        not_md = d / "config.json"
        not_md.write_text("{}")
        result = run_cli(
            "groundcrew-mark-in-progress",
            home=self.home, cwd=self.cwd,
            stdin=json.dumps({"path": str(not_md)}),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(".md plan file", result.stderr)

    def test_groundcrew_mark_in_progress_rejects_non_string_path(self):
        """path JSON field of wrong type → exit 2."""
        result = run_cli(
            "groundcrew-mark-in-progress",
            home=self.home, cwd=self.cwd,
            stdin=json.dumps({"path": 42}),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("non-empty string", result.stderr)

    def test_groundcrew_mark_in_progress_rejects_bad_json(self):
        result = run_cli(
            "groundcrew-mark-in-progress",
            home=self.home, cwd=self.cwd,
            stdin="not json",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("not valid JSON", result.stderr)

    def test_groundcrew_mark_in_progress_rejects_missing_path_key(self):
        result = run_cli(
            "groundcrew-mark-in-progress",
            home=self.home, cwd=self.cwd,
            stdin=json.dumps({"other": "value"}),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("'path' field required", result.stderr)


class TestQueue(IsolatedHomeTestCase):
    """Cross-repo `queue list` / `queue set` for the plan-crew skill."""

    def _make_plan(
        self, repo: str, name: str, status: str = "", agent: str = ""
    ) -> Path:
        """Create ~/<home>/plans/<repo>/<name> with optional Status/Agent."""
        d = self.plans_root / repo
        d.mkdir(parents=True, exist_ok=True)
        fm = ["---"]
        if agent:
            fm.append(f"Agent: {agent}")
        if status:
            fm.append(f"Status: {status}")
        fm.append("---")
        p = d / name
        p.write_text("\n".join(fm) + f"\n\n# {name}\n", encoding="utf-8")
        return p

    def test_queue_list_empty_when_no_plans(self) -> None:
        r = run_cli("queue", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [])

    def test_queue_list_reports_status_and_agent_across_repos(self) -> None:
        self._make_plan("alpha", "2026-05-01-a.md", status="todo", agent="codex")
        self._make_plan("beta", "2026-05-02-b.md", status="backlog")
        r = run_cli("queue", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)
        by_file = {row["file"]: row for row in rows}
        self.assertEqual(
            by_file["2026-05-01-a.md"],
            {"repo": "alpha", "file": "2026-05-01-a.md", "status": "todo", "agent": "codex"},
        )
        self.assertEqual(
            by_file["2026-05-02-b.md"],
            {"repo": "beta", "file": "2026-05-02-b.md", "status": "backlog", "agent": ""},
        )

    def test_queue_list_skips_done_and_deferred_and_no_frontmatter(self) -> None:
        self._make_plan("alpha", "2026-05-01-active.md", status="backlog")
        # archived/paused subdirs must be ignored
        done = self.plans_root / "alpha" / "done"
        done.mkdir(parents=True, exist_ok=True)
        (done / "2026-04-01-old.md").write_text(
            "---\nStatus: done\n---\n\n# old\n", encoding="utf-8"
        )
        deferred = self.plans_root / "alpha" / "deferred"
        deferred.mkdir(parents=True, exist_ok=True)
        (deferred / "2026-04-02-paused.md").write_text(
            "---\nStatus: backlog\n---\n\n# paused\n", encoding="utf-8"
        )
        # a non-plan .md with no frontmatter must be skipped
        (self.plans_root / "alpha" / "README.md").write_text(
            "# not a plan\n", encoding="utf-8"
        )
        r = run_cli("queue", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        files = sorted(row["file"] for row in json.loads(r.stdout))
        self.assertEqual(files, ["2026-05-01-active.md"])

    def test_queue_list_surfaces_in_progress_and_in_review(self) -> None:
        self._make_plan("alpha", "2026-05-01-a.md", status="in-progress", agent="claude")
        self._make_plan("alpha", "2026-05-02-b.md", status="in-review")
        r = run_cli("queue", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        by_file = {row["file"]: row["status"] for row in json.loads(r.stdout)}
        self.assertEqual(by_file["2026-05-01-a.md"], "in-progress")
        self.assertEqual(by_file["2026-05-02-b.md"], "in-review")

    def test_queue_list_empty_status_plan(self) -> None:
        self._make_plan("alpha", "2026-05-01-a.md", agent="codex")  # no Status line
        r = run_cli("queue", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "")
        self.assertEqual(rows[0]["agent"], "codex")

    def test_queue_set_promotes_backlog_to_todo(self) -> None:
        p = self._make_plan("alpha", "2026-05-01-a.md", status="backlog", agent="codex")
        r = run_cli(
            "queue", "set", "--status", "todo",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertIn("Status: todo", text)
        self.assertNotIn("Status: backlog", text)
        self.assertIn("Agent: codex", text)  # existing Agent untouched

    def test_queue_set_promote_stamps_groundcrew_ticket(self) -> None:
        # Promoting a plan claims the groundcrew Ticket pair so the id is
        # visible the moment it's queued.
        p = self._make_plan("alpha", "2026-05-01-a.md", status="backlog")
        r = run_cli(
            "queue", "set", "--status", "todo",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertRegex(text, r"Ticket: plan-\d+")
        self.assertIn("Ticket System: groundcrew", text)

    def test_queue_set_promote_does_not_clobber_external_ticket(self) -> None:
        # A plan already filed in Linear keeps its tracker reference on promote.
        d = self.plans_root / "alpha"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "2026-05-01-a.md"
        p.write_text(
            "---\nTicket: ENG-1\nTicket System: linear\nStatus: backlog\n---\n\n# a\n",
            encoding="utf-8",
        )
        r = run_cli(
            "queue", "set", "--status", "todo",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertIn("Ticket: ENG-1", text)
        self.assertIn("Ticket System: linear", text)
        self.assertNotIn("groundcrew", text)

    def test_queue_set_dequeue_does_not_stamp_groundcrew(self) -> None:
        p = self._make_plan("alpha", "2026-05-01-a.md", status="todo")
        r = run_cli(
            "queue", "set", "--status", "backlog",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("groundcrew", p.read_text())

    def test_queue_set_promote_fills_missing_agent_with_default(self) -> None:
        p = self._make_plan("alpha", "2026-05-01-a.md", status="backlog")  # no Agent
        r = run_cli(
            "queue", "set", "--status", "todo", "--default-agent", "claude",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertIn("Status: todo", text)
        self.assertIn("Agent: claude", text)

    def test_queue_set_promote_keeps_existing_agent_over_default(self) -> None:
        p = self._make_plan("alpha", "2026-05-01-a.md", status="backlog", agent="codex")
        r = run_cli(
            "queue", "set", "--status", "todo", "--default-agent", "claude",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertIn("Agent: codex", text)
        self.assertNotIn("Agent: claude", text)

    def test_queue_set_dequeues_todo_to_backlog_without_touching_agent(self) -> None:
        p = self._make_plan("alpha", "2026-05-01-a.md", status="todo")  # no Agent
        r = run_cli(
            "queue", "set", "--status", "backlog", "--default-agent", "claude",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertIn("Status: backlog", text)
        self.assertNotIn("Agent:", text)  # dequeue never writes a default Agent

    def test_queue_set_promotes_multiple_plans_in_one_call(self) -> None:
        p1 = self._make_plan("alpha", "2026-05-01-a.md", status="backlog")
        p2 = self._make_plan("beta", "2026-05-02-b.md", status="backlog")
        r = run_cli(
            "queue", "set", "--status", "todo", "--default-agent", "claude",
            stdin=f"{p1}\n{p2}\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Status: todo", p1.read_text())
        self.assertIn("Status: todo", p2.read_text())

    def test_queue_set_rejects_path_outside_plan_root(self) -> None:
        outside = self.home / "evil.md"
        outside.write_text("---\nStatus: backlog\n---\n\n# evil\n", encoding="utf-8")
        r = run_cli(
            "queue", "set", "--status", "todo",
            stdin=str(outside) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("outside PLAN_ROOT", r.stderr)
        self.assertIn("Status: backlog", outside.read_text())  # untouched

    def test_queue_set_rejects_plan_without_frontmatter(self) -> None:
        d = self.plans_root / "alpha"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "2026-05-01-a.md"
        p.write_text("# no frontmatter\n", encoding="utf-8")
        r = run_cli(
            "queue", "set", "--status", "todo",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("no frontmatter", r.stderr)

    def test_queue_set_errors_on_empty_stdin(self) -> None:
        r = run_cli(
            "queue", "set", "--status", "todo",
            stdin="\n  \n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("no plan paths", r.stderr)

    def test_queue_set_is_all_or_nothing_on_bad_path(self) -> None:
        good = self._make_plan("alpha", "2026-05-01-a.md", status="backlog")
        bad = self.plans_root / "alpha" / "missing.md"  # does not exist
        r = run_cli(
            "queue", "set", "--status", "todo",
            stdin=f"{good}\n{bad}\n", home=self.home, cwd=self.cwd,
        )
        self.assertNotEqual(r.returncode, 0)
        # good plan must be untouched because validation fails before any write
        self.assertIn("Status: backlog", good.read_text())


class TestCreatedStamp(IsolatedHomeTestCase):
    """`save` records a `Created:` ISO-8601 stamp, fill-if-absent."""

    def test_save_injects_created_iso(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "stamp me",
            stdin="# Body\n", home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(r.stdout.strip()).read_text()
        # e.g. "Created: 2026-06-02T14:30:00Z"
        self.assertRegex(text, r"Created: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

    def test_created_is_fill_if_absent(self) -> None:
        # A hand-written Created in the body must win over the save-time stamp.
        r = run_cli(
            "save", "--override", "scratch", "--topic", "preset",
            stdin="---\nCreated: 2020-01-01T00:00:00Z\n---\n\n# Body\n",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(r.stdout.strip()).read_text()
        self.assertEqual(text.count("Created:"), 1)
        self.assertIn("Created: 2020-01-01T00:00:00Z", text)

    def test_non_md_save_has_no_created(self) -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", "data", "--extension", "json",
            stdin='{"a": 1}\n', home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Created:", Path(r.stdout.strip()).read_text())


class TestListIntraDayOrder(IsolatedHomeTestCase):
    """list orders by `Created` (newest-first) with intra-day precision,
    falling back to the filename date when Created is absent."""

    def _write(self, name: str, created: str | None) -> None:
        d = self.plans_root / "scratch"
        d.mkdir(parents=True, exist_ok=True)
        fm = "Status: todo\n"
        if created is not None:
            fm += f"Created: {created}\n"
        (d / name).write_text(f"---\n{fm}---\n\n# {name}\n", encoding="utf-8")

    def test_created_overrides_filename_alphabetical_within_day(self) -> None:
        # Same day. Alphabetically apple < zebra, so the old filename-desc sort
        # put zebra first. But apple was Created later → it must now lead.
        self._write("2026-06-02-apple.md", "2026-06-02T15:00:00Z")
        self._write("2026-06-02-zebra.md", "2026-06-02T09:00:00Z")
        r = run_cli("list", "--override", "scratch", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout.strip().split("\n"),
            ["2026-06-02-apple.md", "2026-06-02-zebra.md"],
        )

    def test_malformed_created_falls_back_to_filename_date(self) -> None:
        # A half-valid `Created` (matches YYYY-MM-DD but has trailing junk) must
        # NOT be trusted as the sort key — it falls back to the filename date.
        # If it were trusted, "2026-06-02 junk" (space < 'T') would sort below
        # the well-formed midnight stamp, flipping the name-tiebreak order.
        self._write("2026-06-02-zzz.md", "2026-06-02 junk")
        self._write("2026-06-02-aaa.md", "2026-06-02T00:00:00Z")
        r = run_cli("list", "--override", "scratch", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        # Both resolve to the same midnight key → tiebreak on filename desc.
        self.assertEqual(
            r.stdout.strip().split("\n"),
            ["2026-06-02-zzz.md", "2026-06-02-aaa.md"],
        )

    def test_cross_day_still_orders_by_date(self) -> None:
        self._write("2026-06-01-old.md", "2026-06-01T23:00:00Z")
        self._write("2026-06-02-new.md", "2026-06-02T01:00:00Z")
        r = run_cli("list", "--override", "scratch", home=self.home)
        self.assertEqual(
            r.stdout.strip().split("\n"),
            ["2026-06-02-new.md", "2026-06-01-old.md"],
        )

    def test_missing_created_falls_back_to_filename_date(self) -> None:
        # No-Created plan dated 06-03 must sort above a Created plan dated 06-01,
        # because the fallback uses the filename's day.
        self._write("2026-06-03-nostamp.md", None)
        self._write("2026-06-01-stamped.md", "2026-06-01T12:00:00Z")
        r = run_cli("list", "--override", "scratch", home=self.home)
        self.assertEqual(
            r.stdout.strip().split("\n"),
            ["2026-06-03-nostamp.md", "2026-06-01-stamped.md"],
        )

    def test_status_filtered_listing_respects_created_order(self) -> None:
        # The --status path stable-sorts by tier over list_plans' order, so the
        # Created ordering must survive inside a status group.
        self._write("2026-06-02-apple.md", "2026-06-02T15:00:00Z")
        self._write("2026-06-02-zebra.md", "2026-06-02T09:00:00Z")
        r = run_cli(
            "list", "--override", "scratch", "--status", "todo", home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout.strip().split("\n"),
            ["todo\t2026-06-02-apple.md", "todo\t2026-06-02-zebra.md"],
        )

    def test_order_survives_status_mutation(self) -> None:
        # A status flip rewrites the file (new inode/birthtime) but Created is
        # untouched, so the relative order must not change.
        self._write("2026-06-02-apple.md", "2026-06-02T15:00:00Z")
        self._write("2026-06-02-zebra.md", "2026-06-02T09:00:00Z")
        d = self.plans_root / "scratch"
        run_cli(
            "file-meta", "update", "--file", str(d / "2026-06-02-zebra.md"),
            "--field", "Status=in-progress", home=self.home,
        )
        r = run_cli("list", "--override", "scratch", home=self.home)
        self.assertEqual(
            r.stdout.strip().split("\n"),
            ["2026-06-02-apple.md", "2026-06-02-zebra.md"],
        )


class TestBackfillCreated(IsolatedHomeTestCase):
    """`backfill-created` stamps `Created` (from file birthtime) on plans
    missing it, idempotently, skipping files it can't or shouldn't touch."""

    def _write(self, name: str, body: str) -> Path:
        d = self.plans_root / "scratch"
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_stamps_plan_missing_created(self) -> None:
        path = self._write("2026-06-01-a.md", "---\nStatus: todo\n---\n\n# A\n")
        r = run_cli("backfill-created", "--override", "scratch", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("backfilled Created on 1 plan(s)", r.stdout)
        self.assertRegex(
            path.read_text(), r"Created: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
        )

    def test_idempotent_skips_already_stamped(self) -> None:
        self._write(
            "2026-06-01-a.md",
            "---\nStatus: todo\nCreated: 2026-06-01T08:00:00Z\n---\n\n# A\n",
        )
        r = run_cli("backfill-created", "--override", "scratch", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("backfilled Created on 0 plan(s)", r.stdout)
        # Existing value preserved.
        self.assertIn(
            "Created: 2026-06-01T08:00:00Z",
            (self.plans_root / "scratch" / "2026-06-01-a.md").read_text(),
        )

    def test_skips_non_md_and_no_frontmatter(self) -> None:
        self._write("2026-06-01-data.json", '{"a": 1}\n')
        bare = self._write("2026-06-01-bare.md", "# No frontmatter\n")
        r = run_cli("backfill-created", "--override", "scratch", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("backfilled Created on 0 plan(s)", r.stdout)
        self.assertNotIn("Created:", bare.read_text())

    def test_covers_done_subdir(self) -> None:
        d = self.plans_root / "scratch" / "done"
        d.mkdir(parents=True, exist_ok=True)
        path = d / "2026-06-01-archived.md"
        path.write_text("---\nStatus: done\n---\n\n# Archived\n", encoding="utf-8")
        r = run_cli("backfill-created", "--override", "scratch", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Created:", path.read_text())


class TestBackfillCreatedBestEffort(unittest.TestCase):
    """A stat/write failure on one file must not abort the whole run.

    In-process (patches write_atomic) because the subprocess harness can't
    inject a filesystem error.
    """

    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "plans"
        (self.root / "scratch").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str) -> Path:
        path = self.root / "scratch" / name
        path.write_text("---\nStatus: todo\n---\n\n# x\n", encoding="utf-8")
        return path

    def test_write_failure_on_one_file_does_not_abort(self) -> None:
        bad = self._write("2026-06-01-bad.md")
        good = self._write("2026-06-02-good.md")
        real_write = self.cli.write_atomic

        def flaky_write(path: Path, content: str) -> None:
            if path == bad:
                raise OSError("disk gone")
            real_write(path, content)

        args = MagicMock()
        args.override = "scratch"
        with patch.object(self.cli, "PLAN_ROOT", self.root), \
             patch.object(self.cli, "write_atomic", side_effect=flaky_write):
            rc = self.cli.cmd_backfill_created(args)
        self.assertEqual(rc, 0)
        # The healthy file is still stamped even though the other file errored.
        self.assertIn("Created:", good.read_text())
        self.assertNotIn("Created:", bad.read_text())


class TestResolveTicket(IsolatedHomeTestCase):
    def _save_with_ticket(self, repo: str, topic: str, ticket: str) -> Path:
        r = run_cli("save", "--override", repo, "--topic", topic,
                    stdin="# Body\ntext\n", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        path = Path(r.stdout.strip())
        u = run_cli("file-meta", "update", "--file", str(path),
                    "--field", f"Ticket={ticket}", home=self.home)
        self.assertEqual(u.returncode, 0, u.stderr)
        return path

    def test_archive_by_groundcrew_ticket(self) -> None:
        src = self._save_with_ticket("scratch", "p1", "plan-195296912509085")
        r = run_cli("archive", "--ticket", "plan-195296912509085", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        target = self.plans_root / "scratch" / "done" / src.name
        self.assertEqual(r.stdout.strip(), str(target))
        self.assertTrue(target.exists())
        self.assertFalse(src.exists())

    def test_archive_by_linear_ticket(self) -> None:
        src = self._save_with_ticket("scratch", "p2", "ENG-42")
        r = run_cli("archive", "--ticket", "ENG-42", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.plans_root / "scratch" / "done" / src.name).exists())

    def test_archive_ticket_not_found_exits_3(self) -> None:
        r = run_cli("archive", "--ticket", "plan-000", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("no active plan", r.stderr)

    def test_archive_ticket_multi_match_exits_2(self) -> None:
        self._save_with_ticket("scratch", "a", "DUP-1")
        self._save_with_ticket("other", "b", "DUP-1")
        r = run_cli("archive", "--ticket", "DUP-1", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("matches 2 plans", r.stderr)

    def test_archive_both_file_and_ticket_exits_2(self) -> None:
        r = run_cli("archive", "--override", "scratch",
                    "--file", "x.md", "--ticket", "plan-1", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_archive_neither_file_nor_ticket_exits_2(self) -> None:
        r = run_cli("archive", "--override", "scratch", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_file_meta_update_by_ticket(self) -> None:
        src = self._save_with_ticket("scratch", "u", "plan-77")
        r = run_cli("file-meta", "update", "--ticket", "plan-77",
                    "--field", "Status=todo", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        meta = run_cli("file-meta", "get", "--file", str(src), home=self.home)
        self.assertIn('"Status": "todo"', meta.stdout)

    def test_file_meta_update_ticket_not_found_exits_3(self) -> None:
        r = run_cli("file-meta", "update", "--ticket", "plan-nope",
                    "--field", "Status=todo", home=self.home)
        self.assertEqual(r.returncode, 3)

    def test_file_meta_update_both_file_and_ticket_exits_2(self) -> None:
        r = run_cli("file-meta", "update", "--file", "x.md",
                    "--ticket", "plan-1", "--field", "Status=todo", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_file_meta_update_neither_file_nor_ticket_exits_2(self) -> None:
        r = run_cli("file-meta", "update", "--field", "Status=todo", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_push_ticket_not_found_exits_3(self) -> None:
        r = run_cli("push", "--name", "linear", "--ticket", "plan-absent",
                    home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("no active plan", r.stderr)

    def test_push_both_file_and_ticket_exits_2(self) -> None:
        r = run_cli("push", "--name", "linear", "--file", "x.md",
                    "--ticket", "plan-1", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_push_neither_file_nor_ticket_exits_2(self) -> None:
        r = run_cli("push", "--name", "linear", home=self.home)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
