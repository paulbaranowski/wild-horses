#!/usr/bin/env python3
"""CLI subcommand wiring: save, file-meta set (status lifecycle), backfill, ticket-api arg validation (cli.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from support import (
    CLI,
    IsolatedHomeTestCase,
    _import_cli_module,
    run_cli,
    storage,
)


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

    def test_save_from_path_md_injects_full_block(self) -> None:
        """A `.md` --from-path move with NO frontmatter gets the full managed
        block (Agent/Status/Created), with Created sourced from the source
        file's birthtime — NOT the move time."""
        source = self.cwd / "src.md"
        source.write_text("# Plain body\nNo frontmatter here.\n")
        old = 1_600_000_000.0  # 2020-09-13T12:26:40Z — distinct from "now"
        os.utime(source, (old, old))
        # Assert the exact ISO literal the controlled epoch yields, computed
        # independently of the CLI's _iso_from_stat — so a regression in that
        # helper's format/timezone can't pass by deriving expected from itself.
        # Deterministic cross-platform: Linux has no birthtime (uses mtime=old);
        # macOS clamps birthtime down to the older mtime, so it also reports old.
        expected_created = datetime.fromtimestamp(old, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = run_cli(
            "save", "--override", "testrepo",
            "--from-path", str(source),
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        text = Path(r.stdout.strip()).read_text()
        self.assertTrue(text.startswith("---\n"), f"expected frontmatter, got: {text[:120]!r}")
        self.assertIn("Agent: claude", text)
        self.assertIn("Status: backlog", text)
        self.assertIn(f"Created: {expected_created}", text)
        self.assertIn("# Plain body", text)

    def test_save_from_path_md_partial_frontmatter_fills_only_created(self) -> None:
        """A `.md` move with a partial block (Agent present, no Created) gets
        only the missing fields filled; existing Agent/Status are untouched."""
        source = self.cwd / "src.md"
        source.write_text("---\nAgent: codex\nStatus: todo\n---\n\n# Body\n")
        old = 1_600_000_000.0
        os.utime(source, (old, old))
        # Exact literal, computed independently of _iso_from_stat (see the
        # no-frontmatter test above for why this is deterministic cross-platform).
        expected_created = datetime.fromtimestamp(old, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = run_cli(
            "save", "--override", "testrepo",
            "--from-path", str(source),
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        text = Path(r.stdout.strip()).read_text()
        self.assertIn("Agent: codex", text)
        self.assertNotIn("Agent: claude", text)
        self.assertIn("Status: todo", text)
        self.assertIn(f"Created: {expected_created}", text)

    def test_save_from_path_md_existing_created_not_overwritten(self) -> None:
        """A `.md` move whose body already carries a valid Created keeps it —
        the move never re-stamps an existing value."""
        source = self.cwd / "src.md"
        source.write_text(
            "---\nAgent: codex\nStatus: todo\nCreated: 2020-01-01T00:00:00Z\n---\n\n# Body\n"
        )
        r = run_cli(
            "save", "--override", "testrepo",
            "--from-path", str(source),
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        text = Path(r.stdout.strip()).read_text()
        self.assertIn("Created: 2020-01-01T00:00:00Z", text)
        self.assertEqual(text.count("Created:"), 1)

    def test_save_from_path_md_unlinks_source_on_success(self) -> None:
        """The .md move is a move, not a copy: a successful injection removes
        the source. (The non-.md move guarantees this via shutil.move; the .md
        path rewrites + unlinks, so it needs its own coverage.)"""
        source = self.cwd / "src.md"
        source.write_text("# Body\n")
        r = run_cli(
            "save", "--override", "testrepo",
            "--from-path", str(source),
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertTrue(Path(r.stdout.strip()).exists())
        self.assertFalse(source.exists(), "source must be unlinked after a successful .md move")

    def test_save_from_path_md_malformed_frontmatter_is_retry_safe(self) -> None:
        """A moved-in .md with malformed frontmatter (no closing '---') must
        fail BEFORE the source is destroyed, leaving nothing in the target dir —
        otherwise a half-completed move would strand an unstamped file and break
        the 'delete only on success' retry contract."""
        source = self.cwd / "broken.md"
        source.write_text("---\nAgent: codex\n\n# No closing fence\n")
        r = run_cli(
            "save", "--override", "testrepo",
            "--from-path", str(source),
            home=self.home,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("malformed frontmatter", r.stderr)
        self.assertTrue(source.exists(), "source must survive a malformed-frontmatter failure")
        self.assertFalse(
            (self.plans_root / "testrepo" / "broken.md").exists(),
            "no file should be stranded in the target dir on failure",
        )

    def test_save_from_path_md_same_path_overwrite_preserves_file(self) -> None:
        """`--from-path` pointing AT an existing target `.md` with
        `--on-collision overwrite` must NOT delete the file: write_atomic
        replaces it in place and the unlink is skipped because source and target
        resolve to the same path. Without the guard, source.unlink() would
        delete the freshly stamped plan."""
        repo_dir = self.plans_root / "testrepo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        target = repo_dir / "2026-06-02-inplace.md"
        target.write_text("# In place\n")
        r = run_cli(
            "save", "--override", "testrepo",
            "--from-path", str(target), "--on-collision", "overwrite",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertTrue(
            target.exists(),
            "the in-place file must survive a same-path overwrite — not be unlinked",
        )
        text = target.read_text()
        self.assertIn("# In place", text)
        self.assertIn("Status: backlog", text)
        self.assertIn("Created:", text)

    def test_save_from_path_json_byte_verbatim(self) -> None:
        """A non-.md (.json) move stays byte-for-byte — no frontmatter, no
        rewrite. This is the regression guard the verbatim guarantee protects."""
        source = self.cwd / "data.json"
        raw = '{"tasks": [1, 2, 3]}\n'
        source.write_text(raw)
        r = run_cli(
            "save", "--override", "testrepo",
            "--from-path", str(source),
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        written = Path(r.stdout.strip())
        self.assertEqual(written.read_bytes(), raw.encode("utf-8"))
        self.assertNotIn("Created", written.read_text())
        self.assertNotIn("Agent", written.read_text())

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

class TestFileMetaSetStatus(IsolatedHomeTestCase):
    """`file-meta set --status` is lifecycle-aware: terminal statuses relocate
    the plan into done/ or deferred/ (done stamps Completed on); active
    statuses rewrite in place."""

    def _save_one(self, topic: str = "lifecycle plan") -> Path:
        r = run_cli(
            "save", "--override", "scratch", "--topic", topic,
            stdin="# Body\nsome text\n",
            home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        return Path(r.stdout.strip())

    def test_done_relocates_and_unlinks(self) -> None:
        source = self._save_one()
        r = run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                    home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        target = self.plans_root / "scratch" / "done" / source.name
        self.assertEqual(r.stdout.strip(), str(target))
        self.assertTrue(target.exists())
        self.assertFalse(source.exists(), "source should be unlinked after relocate")

    def test_done_sets_status_and_stamps_today(self) -> None:
        source = self._save_one()
        run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                home=self.home)
        text = (self.plans_root / "scratch" / "done" / source.name).read_text()
        front = text.split("\n---\n", 1)[0]
        self.assertIn("Status: done", front)
        self.assertIn(f"Completed on: {date.today().isoformat()}", front)

    def test_done_completed_on_override(self) -> None:
        source = self._save_one()
        run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                "--completed-on", "2020-01-15", home=self.home)
        text = (self.plans_root / "scratch" / "done" / source.name).read_text()
        front = text.split("\n---\n", 1)[0]
        self.assertIn("Completed on: 2020-01-15", front)
        # Supplied date must suppress the auto-stamp: exactly one Completed on,
        # and today's date must not leak into that field. Scope the check to the
        # Completed on line — the frontmatter's Created: stamp legitimately
        # carries today's date, so asserting against the whole block is wrong.
        self.assertEqual(front.count("Completed on:"), 1)
        completed_line = next(
            line for line in front.splitlines()
            if line.startswith("Completed on:")
        )
        self.assertNotIn(date.today().isoformat(), completed_line)

    def test_deferred_relocates_without_stamp(self) -> None:
        source = self._save_one()
        r = run_cli("file-meta", "set", "--file", str(source), "--status", "deferred",
                    home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        target = self.plans_root / "scratch" / "deferred" / source.name
        self.assertTrue(target.exists())
        self.assertFalse(source.exists())
        text = target.read_text()
        self.assertIn("Status: deferred", text.split("\n---\n", 1)[0])
        self.assertNotIn("Completed on", text)

    def test_active_status_stays_in_place(self) -> None:
        source = self._save_one()
        r = run_cli("file-meta", "set", "--file", str(source), "--status", "in-progress",
                    home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(source), "active status must not relocate")
        self.assertTrue(source.exists())
        self.assertFalse((self.plans_root / "scratch" / "done" / source.name).exists())
        self.assertIn("Status: in-progress", source.read_text().split("\n---\n", 1)[0])

    def test_multi_field_relocate_is_atomic(self) -> None:
        source = self._save_one()
        run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                "--agent", "codex", home=self.home)
        text = (self.plans_root / "scratch" / "done" / source.name).read_text()
        front = text.split("\n---\n", 1)[0]
        self.assertIn("Status: done", front)
        self.assertIn("Agent: codex", front)

    def test_collision_fail_is_default(self) -> None:
        source = self._save_one("collide me")
        run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                home=self.home)
        # Re-save the same name into the active dir, then relocate again.
        run_cli("save", "--override", "scratch", "--topic", "collide me",
                stdin="x\n", home=self.home)
        r = run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                    home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("existing:", r.stderr)
        self.assertIn("suggestion:", r.stderr)

    def test_collision_suffix(self) -> None:
        source = self._save_one("collide me")
        run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                home=self.home)
        run_cli("save", "--override", "scratch", "--topic", "collide me",
                stdin="x\n", home=self.home)
        r = run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                    "--on-collision", "suffix", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip().endswith("-2.md"))

    def test_collision_overwrite(self) -> None:
        source = self._save_one("collide me")
        run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                home=self.home)
        run_cli("save", "--override", "scratch", "--topic", "collide me",
                stdin="x\n", home=self.home)
        r = run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                    "--on-collision", "overwrite", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        done_dir = self.plans_root / "scratch" / "done"
        self.assertEqual(
            sorted(p.name for p in done_dir.iterdir()), [source.name],
            "overwrite must not create a -2 variant",
        )

    def test_invalid_status_rejected(self) -> None:
        source = self._save_one()
        r = run_cli("file-meta", "set", "--file", str(source), "--status", "bogus",
                    home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid choice", r.stderr)

    def test_active_status_on_terminal_source_refused(self) -> None:
        # Reactivating a done/deferred plan (back to the active dir) is out of
        # scope; the CLI must refuse loudly rather than park an active-status
        # plan in done/.
        source = self._save_one("reactivate me")
        run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                home=self.home)
        done_path = self.plans_root / "scratch" / "done" / source.name
        r = run_cli("file-meta", "set", "--file", str(done_path), "--status", "todo",
                    home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("reactivating", r.stderr)
        # The plan stays put with its terminal status untouched.
        self.assertTrue(done_path.exists())
        self.assertIn("Status: done", done_path.read_text().split("\n---\n", 1)[0])

    def test_non_status_edit_on_terminal_plan_in_place(self) -> None:
        # Editing a non-status field (e.g. Kind) on a done plan is still a
        # legal in-place edit — only an active --status is refused.
        source = self._save_one("edit done plan")
        run_cli("file-meta", "set", "--file", str(source), "--status", "done",
                home=self.home)
        done_path = self.plans_root / "scratch" / "done" / source.name
        r = run_cli("file-meta", "set", "--file", str(done_path), "--kind", "spec",
                    home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(done_path.exists())
        self.assertIn("Kind: spec", done_path.read_text().split("\n---\n", 1)[0])

    def test_archive_subcommand_removed(self) -> None:
        # archive was folded into `file-meta set --status done`; the old
        # subcommand must no longer exist.
        r = run_cli("archive", "--override", "scratch", "--file", "x.md",
                    home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid choice", r.stderr)


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
        with patch.object(storage, "PLAN_ROOT", self.root), \
             patch.object(self.cli, "write_atomic", side_effect=flaky_write):
            rc = self.cli.cmd_backfill_created(args)
        self.assertEqual(rc, 0)
        # The healthy file is still stamped even though the other file errored.
        self.assertIn("Created:", good.read_text())
        self.assertNotIn("Created:", bad.read_text())


class TestVersion(IsolatedHomeTestCase):
    """`--version` reports the single-source-of-truth __version__, kept in
    lockstep with plugin.json. These tests guard the release invariant: the
    Homebrew package version (pyproject reads __version__ dynamically), the
    CLI's --version output, and the plugin manifest must all agree."""

    def test_version_flag_reports_module_version(self) -> None:
        module = _import_cli_module()
        r = run_cli("--version", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(module.__version__, r.stdout)

    def test_version_matches_plugin_manifest(self) -> None:
        module = _import_cli_module()
        manifest = json.loads(
            (CLI.parent.parent / ".claude-plugin" / "plugin.json").read_text()
        )
        self.assertEqual(
            module.__version__,
            manifest["version"],
            "plan_keeper.__version__ must match plugin.json version "
            "(bump both together when releasing)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
