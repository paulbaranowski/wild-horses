#!/usr/bin/env python3
"""Listing, sort order, and ticket resolution (storage.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import unittest
from pathlib import Path

from support import (
    IsolatedHomeTestCase,
    run_cli,
)


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
