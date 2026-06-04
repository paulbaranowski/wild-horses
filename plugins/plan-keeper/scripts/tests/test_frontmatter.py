#!/usr/bin/env python3
"""Frontmatter parse/serialize/inject + Created stamping (frontmatter.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import unittest
from pathlib import Path

from support import (
    IsolatedHomeTestCase,
    run_cli,
)


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

    def test_directory_exits_3_not_traceback(self) -> None:
        # A directory passes exists() but must not reach read_text() (which
        # would raise IsADirectoryError → unhandled traceback / exit 1).
        d = self.cwd / "adir.md"
        d.mkdir()
        result = run_cli(
            "file-meta", "get", "--file", str(d), home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("not a file", result.stderr)

class TestFileMetaSet(IsolatedHomeTestCase):
    def _write_plan(self, content: str) -> Path:
        path = self.cwd / "plan.md"
        path.write_text(content, encoding="utf-8")
        return path

    def _managed(self, *extra: str) -> Path:
        """A plan that already has frontmatter (set rejects bare files)."""
        return self._write_plan(
            "---\nAgent: claude\nStatus: backlog\n" + "".join(extra) + "---\n\n# Body\n"
        )

    def test_sets_ticket_id_and_system(self) -> None:
        path = self._managed()
        result = run_cli(
            "file-meta", "set", "--file", str(path),
            "--ticket-id", "ENG-123", "--ticket-system", "linear",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = path.read_text(encoding="utf-8")
        self.assertIn("Ticket: ENG-123", text)
        self.assertIn("Ticket System: linear", text)

    def test_rejects_bare_file(self) -> None:
        path = self._write_plan("# Heading\n\nBody.\n")
        original = path.read_text(encoding="utf-8")
        result = run_cli(
            "file-meta", "set", "--file", str(path), "--ticket-id", "ENG-123",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("no frontmatter", result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_updates_existing_ticket_in_place(self) -> None:
        path = self._managed("Ticket: OLD-1\n", "Ticket System: jira\n")
        result = run_cli(
            "file-meta", "set", "--file", str(path),
            "--ticket-id", "ENG-99", "--ticket-system", "linear",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = path.read_text(encoding="utf-8")
        self.assertIn("Ticket: ENG-99", text)
        self.assertIn("Ticket System: linear", text)
        self.assertNotIn("OLD-1", text)
        self.assertNotIn("Ticket System: jira", text)

    def test_preserves_unmodified_fields(self) -> None:
        path = self._managed("Ticket: KEEP-1\n", "Completed on: 2026-05-19\n")
        # Only setting --completed-on; Ticket should stay.
        result = run_cli(
            "file-meta", "set", "--file", str(path), "--completed-on", "2026-05-20",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = path.read_text(encoding="utf-8")
        self.assertIn("Ticket: KEEP-1", text)
        self.assertIn("Completed on: 2026-05-20", text)

    def test_preserves_foreign_field_through_write(self) -> None:
        # A rewrite must not drop foreign frontmatter.
        path = self._write_plan(
            "---\ntags: [planning, infra]\nTicket: KEEP-1\n---\n\n# H\n"
        )
        result = run_cli(
            "file-meta", "set", "--file", str(path), "--completed-on", "2026-05-20",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        text = path.read_text(encoding="utf-8")
        self.assertIn("tags: [planning, infra]", text)
        self.assertIn("Ticket: KEEP-1", text)
        self.assertIn("Completed on: 2026-05-20", text)
        # Managed fields serialize in canonical order ahead of foreign ones.
        self.assertLess(text.index("Ticket:"), text.index("tags:"))

    def test_omits_empty_fields(self) -> None:
        path = self._managed()
        result = run_cli(
            "file-meta", "set", "--file", str(path), "--ticket-id", "ENG-1",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # Completed on was never set, so the line should be absent.
        self.assertNotIn("Completed on:", path.read_text(encoding="utf-8"))

    def test_requires_at_least_one_value_flag(self) -> None:
        # A locator with no value flag is a usage error (exit 2), file untouched.
        path = self._managed()
        original = path.read_text(encoding="utf-8")
        result = run_cli(
            "file-meta", "set", "--file", str(path),
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_sets_agent(self) -> None:
        path = self._managed()
        result = run_cli(
            "file-meta", "set", "--file", str(path), "--agent", "codex",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Agent: codex", path.read_text(encoding="utf-8"))

    def test_sets_status(self) -> None:
        path = self._managed()
        result = run_cli(
            "file-meta", "set", "--file", str(path), "--status", "in-progress",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Status: in-progress", path.read_text(encoding="utf-8"))

    def test_sets_multiple_fields_at_once(self) -> None:
        path = self._managed()
        result = run_cli(
            "file-meta", "set", "--file", str(path),
            "--agent", "codex", "--status", "in-progress",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = path.read_text(encoding="utf-8")
        self.assertIn("Agent: codex", text)
        self.assertIn("Status: in-progress", text)

    def test_kind_normalized_lowercase(self) -> None:
        path = self._managed()
        result = run_cli(
            "file-meta", "set", "--file", str(path), "--kind", "Exec-Plan",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Kind: exec-plan", path.read_text(encoding="utf-8"))

    def test_rejects_invalid_kind_before_write(self) -> None:
        path = self._managed()
        result = run_cli(
            "file-meta", "set", "--file", str(path), "--kind", "blueprint",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid Kind", result.stderr)
        self.assertNotIn("Kind:", path.read_text(encoding="utf-8"))

    def test_rejects_invalid_completed_on(self) -> None:
        path = self._managed()
        result = run_cli(
            "file-meta", "set", "--file", str(path), "--completed-on", "notadate",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Completed on:", path.read_text(encoding="utf-8"))

    def test_rejects_invalid_ticket_system(self) -> None:
        path = self._managed()
        result = run_cli(
            "file-meta", "set", "--file", str(path), "--ticket-system", "github",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)

    def test_directory_exits_3_not_traceback(self) -> None:
        d = self.cwd / "adir.md"
        d.mkdir()
        result = run_cli(
            "file-meta", "set", "--file", str(d), "--status", "todo",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("not a file", result.stderr)

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

    def test_directory_exits_3_not_traceback(self) -> None:
        d = self.cwd / "adir.md"
        d.mkdir()
        result = run_cli(
            "file-meta", "strip", "--file", str(d), home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("not a file", result.stderr)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
