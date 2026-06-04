#!/usr/bin/env python3
"""Listing, sort order, and ticket resolution (storage.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import subprocess
import unittest
from pathlib import Path

from support import (  # noqa: F401 — also inserts scripts/ onto sys.path
    IsolatedHomeTestCase,
    run_cli,
)

from plan_keeper import storage  # noqa: E402 — after support's path insert
from plan_keeper.errors import PlanKeeperCliError  # noqa: E402


class TestStateSubdir(unittest.TestCase):
    """state_subdir maps a lifecycle state to its on-disk directory."""

    def test_active_states_resolve_to_repo_root(self) -> None:
        root = Path("/plans/myrepo")
        for state in ("backlog", "todo", "in-progress", "in-review"):
            self.assertEqual(storage.state_subdir(root, state), root)

    def test_done_resolves_to_done_subdir(self) -> None:
        root = Path("/plans/myrepo")
        self.assertEqual(storage.state_subdir(root, "done"), root / "done")

    def test_deferred_resolves_to_deferred_subdir(self) -> None:
        root = Path("/plans/myrepo")
        self.assertEqual(storage.state_subdir(root, "deferred"), root / "deferred")

    def test_unknown_state_raises_code_2(self) -> None:
        with self.assertRaises(PlanKeeperCliError) as ctx:
            storage.state_subdir(Path("/plans/myrepo"), "bogus")
        self.assertEqual(ctx.exception.code, 2)


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
        run_cli("file-meta", "set", "--status", "done",
                "--file", str(self.plans_root / "scratch" / fname), home=self.home)
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
        run_cli("file-meta", "set", "--status", "done",
                "--file", str(self.plans_root / "scratch" / fname), home=self.home)
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

class TestListCrossRepo(IsolatedHomeTestCase):
    """`list` resolves a *scope*, not always a single repo. With no repo
    context (no --override and no git origin) — or with --all-repos — it lists
    every repo under ~/plans/, prefixing each line `repo/filename`. A repo
    context (override or git origin) keeps the old single-repo, bare-filename
    output so skills that parse `list` inside a repo are unaffected.
    """

    def _make_git_repo(self, dirname: str, origin: str) -> Path:
        """A throwaway git checkout whose `origin` derives to a known repo name.

        derive_repo resolves the repo from `git remote get-url origin`, so an
        origin of .../<name>.git makes the cwd look like repo `<name>`.
        """
        d = self.home / dirname
        d.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "remote", "add", "origin", origin], cwd=d, check=True)
        return d

    def test_no_remote_no_override_lists_all_repos_prefixed(self) -> None:
        run_cli("save", "--override", "alpha", "--topic", "a", stdin="x\n", home=self.home)
        run_cli("save", "--override", "beta", "--topic", "b", stdin="x\n", home=self.home)
        # cwd is the non-git workdir, so there is no git origin to resolve.
        r = run_cli("list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().split("\n")
        self.assertTrue(any(l.startswith("alpha/") and l.endswith(".md") for l in lines))
        self.assertTrue(any(l.startswith("beta/") and l.endswith(".md") for l in lines))

    def test_all_repos_flag_overrides_git_context(self) -> None:
        run_cli("save", "--override", "alpha", "--topic", "a", stdin="x\n", home=self.home)
        run_cli("save", "--override", "beta", "--topic", "b", stdin="x\n", home=self.home)
        # Inside a git checkout that resolves to `alpha`, --all-repos still
        # spans every repo rather than narrowing to alpha.
        git_repo = self._make_git_repo("alpha-checkout", "https://github.com/me/alpha.git")
        r = run_cli("list", "--all-repos", home=self.home, cwd=git_repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("alpha/", r.stdout)
        self.assertIn("beta/", r.stdout)

    def test_git_context_no_flag_is_single_repo_bare_filenames(self) -> None:
        run_cli("save", "--override", "myrepo", "--topic", "only one", stdin="x\n", home=self.home)
        run_cli("save", "--override", "other", "--topic", "elsewhere", stdin="x\n", home=self.home)
        git_repo = self._make_git_repo("myrepo-checkout", "https://github.com/me/myrepo.git")
        r = run_cli("list", home=self.home, cwd=git_repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.strip()
        # Single-repo mode (regression guard): bare filenames, no repo prefix,
        # and the other repo's plans are absent.
        self.assertIn("only-one", out)
        self.assertNotIn("/", out)
        self.assertNotIn("elsewhere", out)

    def test_all_repos_with_override_is_error(self) -> None:
        r = run_cli(
            "list", "--all-repos", "--override", "alpha",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 2)

    def test_cross_repo_respects_state_done(self) -> None:
        res = run_cli("save", "--override", "alpha", "--topic", "to archive", stdin="x\n", home=self.home)
        fname = Path(res.stdout.strip()).name
        run_cli("file-meta", "set", "--status", "done",
                "--file", str(self.plans_root / "alpha" / fname), home=self.home)
        run_cli("save", "--override", "beta", "--topic", "active one", stdin="x\n", home=self.home)
        done = run_cli("list", "--state", "done", home=self.home, cwd=self.cwd)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"alpha/{fname}", done.stdout)
        self.assertNotIn("beta/", done.stdout)

    def test_cross_repo_status_filter_prefixes_and_aggregates(self) -> None:
        a = self.plans_root / "alpha"
        a.mkdir(parents=True)
        (a / "2026-05-28-a.md").write_text("---\nStatus: todo\n---\n\n# a\n", encoding="utf-8")
        (a / "2026-05-29-b.md").write_text("---\nStatus: in-progress\n---\n\n# b\n", encoding="utf-8")
        b = self.plans_root / "beta"
        b.mkdir(parents=True)
        (b / "2026-05-27-c.md").write_text("---\nStatus: todo\n---\n\n# c\n", encoding="utf-8")
        r = run_cli("list", "--status", "todo", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().split("\n")
        self.assertIn("todo\talpha/2026-05-28-a.md", lines)
        self.assertIn("todo\tbeta/2026-05-27-c.md", lines)
        # The hidden in-progress plan is counted, aggregated across repos.
        self.assertIn("1 other active plan(s) hidden", r.stderr)
        self.assertIn("in-progress×1", r.stderr)

    def test_empty_plans_root_no_output(self) -> None:
        # No saves at all: ~/plans/ does not exist. Cross-repo mode must still
        # exit 0 with empty output rather than erroring.
        r = run_cli("list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_whitespace_only_override_is_rejected(self) -> None:
        # cmd_list normalizes + validates --override itself (a path distinct
        # from derive_repo). A whitespace-only override normalizes to "" and
        # must be rejected (exit 2), not silently fall through to cross-repo.
        r = run_cli("list", "--override", "   ", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)

class TestListRepos(IsolatedHomeTestCase):
    def test_counts_per_state(self) -> None:
        # Two repos, varied content
        run_cli("save", "--override", "alpha", "--topic", "a", stdin="x\n", home=self.home)
        run_cli("save", "--override", "alpha", "--topic", "b", stdin="x\n", home=self.home)
        r = run_cli("save", "--override", "beta", "--topic", "c", stdin="x\n", home=self.home)
        run_cli("file-meta", "set", "--status", "done", "--file", r.stdout.strip(), home=self.home)
        out = run_cli("repo", "list", home=self.home)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.strip().split("\n")
        self.assertIn("alpha: active=2", lines)
        self.assertIn("beta: done=1", lines)

    def test_skips_empty_dirs(self) -> None:
        # Create an empty subdir under ~/plans/ — should not appear
        (self.plans_root / "ghost").mkdir(parents=True)
        run_cli("save", "--override", "real", "--topic", "x", stdin="x\n", home=self.home)
        out = run_cli("repo", "list", home=self.home)
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
        out = run_cli("repo", "list", home=self.home)
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
        mutate = run_cli(
            "file-meta", "set", "--file", str(d / "2026-06-02-zebra.md"),
            "--status", "in-progress", home=self.home,
        )
        self.assertEqual(mutate.returncode, 0, mutate.stderr)
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
        u = run_cli("file-meta", "set", "--file", str(path),
                    "--ticket-id", ticket, home=self.home)
        self.assertEqual(u.returncode, 0, u.stderr)
        return path

    def test_set_done_by_groundcrew_ticket(self) -> None:
        src = self._save_with_ticket("scratch", "p1", "plan-195296912509085")
        r = run_cli("file-meta", "set", "--status", "done",
                    "--ticket", "plan-195296912509085", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        target = self.plans_root / "scratch" / "done" / src.name
        self.assertEqual(r.stdout.strip(), str(target))
        self.assertTrue(target.exists())
        self.assertFalse(src.exists())

    def test_set_done_by_linear_ticket(self) -> None:
        src = self._save_with_ticket("scratch", "p2", "ENG-42")
        r = run_cli("file-meta", "set", "--status", "done",
                    "--ticket", "ENG-42", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.plans_root / "scratch" / "done" / src.name).exists())

    def test_set_done_ticket_not_found_exits_3(self) -> None:
        r = run_cli("file-meta", "set", "--status", "done",
                    "--ticket", "plan-000", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("no active plan", r.stderr)

    def test_set_done_ticket_multi_match_exits_2(self) -> None:
        self._save_with_ticket("scratch", "a", "DUP-1")
        self._save_with_ticket("other", "b", "DUP-1")
        r = run_cli("file-meta", "set", "--status", "done",
                    "--ticket", "DUP-1", home=self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn("matches 2 plans", r.stderr)

    def test_set_both_file_and_ticket_exits_2(self) -> None:
        r = run_cli("file-meta", "set", "--status", "done",
                    "--file", "x.md", "--ticket", "plan-1", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_set_neither_file_nor_ticket_exits_2(self) -> None:
        r = run_cli("file-meta", "set", "--status", "done", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_file_meta_set_status_by_ticket(self) -> None:
        src = self._save_with_ticket("scratch", "u", "plan-77")
        r = run_cli("file-meta", "set", "--ticket", "plan-77",
                    "--status", "todo", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        meta = run_cli("file-meta", "get", "--file", str(src), home=self.home)
        self.assertIn('"Status": "todo"', meta.stdout)

    def test_file_meta_set_both_file_and_ticket_exits_2(self) -> None:
        r = run_cli("file-meta", "set", "--file", "x.md",
                    "--ticket", "plan-1", "--status", "todo", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_file_meta_get_by_ticket(self) -> None:
        self._save_with_ticket("scratch", "g", "plan-88")
        r = run_cli("file-meta", "get", "--ticket", "plan-88", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"Ticket": "plan-88"', r.stdout)

    def test_file_meta_get_ticket_not_found_exits_3(self) -> None:
        r = run_cli("file-meta", "get", "--ticket", "plan-nope", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("no active plan", r.stderr)

    def test_file_meta_get_both_file_and_ticket_exits_2(self) -> None:
        r = run_cli("file-meta", "get", "--file", "x.md",
                    "--ticket", "plan-1", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_file_meta_get_neither_file_nor_ticket_exits_2(self) -> None:
        r = run_cli("file-meta", "get", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_file_meta_strip_by_ticket(self) -> None:
        self._save_with_ticket("scratch", "s", "plan-99")
        r = run_cli("file-meta", "strip", "--ticket", "plan-99", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "# Body\ntext\n")

    def test_file_meta_strip_ticket_not_found_exits_3(self) -> None:
        r = run_cli("file-meta", "strip", "--ticket", "plan-nope", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("no active plan", r.stderr)

    def test_file_meta_strip_neither_file_nor_ticket_exits_2(self) -> None:
        r = run_cli("file-meta", "strip", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_file_meta_set_by_ticket(self) -> None:
        src = self._save_with_ticket("scratch", "se", "plan-66")
        r = run_cli("file-meta", "set", "--ticket", "plan-66",
                    "--completed-on", "2026-06-02", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        meta = run_cli("file-meta", "get", "--file", str(src), home=self.home)
        self.assertIn('"Completed on": "2026-06-02"', meta.stdout)

    def test_file_meta_set_ticket_not_found_exits_3(self) -> None:
        r = run_cli("file-meta", "set", "--ticket", "plan-nope",
                    "--completed-on", "2026-06-02", home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("no active plan", r.stderr)

    def test_file_meta_set_neither_file_nor_ticket_exits_2(self) -> None:
        r = run_cli("file-meta", "set", "--completed-on", "2026-06-02",
                    home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_push_ticket_not_found_exits_3(self) -> None:
        r = run_cli("linear", "push", "--ticket", "plan-absent",
                    home=self.home)
        self.assertEqual(r.returncode, 3)
        self.assertIn("no active plan", r.stderr)

    def test_push_both_file_and_ticket_exits_2(self) -> None:
        r = run_cli("linear", "push", "--file", "x.md",
                    "--ticket", "plan-1", home=self.home)
        self.assertEqual(r.returncode, 2)

    def test_push_neither_file_nor_ticket_exits_2(self) -> None:
        r = run_cli("linear", "push", home=self.home)
        self.assertEqual(r.returncode, 2)


class TestListGrouped(IsolatedHomeTestCase):
    def _save(self, topic: str, kind: str, body: str = "x\n") -> None:
        r = run_cli(
            "save", "--override", "scratch", "--topic", topic, "--kind", kind,
            stdin=f"# {topic}\n{body}", home=self.home,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_groups_stages_of_one_slug_in_pipeline_order(self) -> None:
        self._save("noun first provider commands", "exec-plan")
        self._save("noun first provider commands", "design")
        r = run_cli("list", "--override", "scratch", "--group", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [ln for ln in r.stdout.split("\n") if ln]
        # Heading line for the project, then design before exec-plan (pipeline order).
        self.assertEqual(lines[0], "noun-first-provider-commands")
        self.assertIn("design", lines[1])
        self.assertIn("exec-plan", lines[2])
        self.assertLess(
            next(i for i, l in enumerate(lines) if "design" in l),
            next(i for i, l in enumerate(lines) if "exec-plan" in l),
        )

    def test_separate_slugs_form_separate_groups(self) -> None:
        self._save("alpha topic", "spec")
        self._save("beta topic", "spec")
        r = run_cli("list", "--override", "scratch", "--group", home=self.home)
        headings = [l for l in r.stdout.split("\n") if l and not l.startswith("  ")]
        self.assertIn("alpha-topic", headings)
        self.assertIn("beta-topic", headings)

    def test_legacy_unclassified_file_still_appears(self) -> None:
        # A bare save (no --kind) groups under its slug with a "-" stage marker.
        run_cli(
            "save", "--override", "scratch", "--topic", "legacy plan",
            stdin="# Legacy\n", home=self.home,
        )
        r = run_cli("list", "--override", "scratch", "--group", home=self.home)
        self.assertIn("legacy-plan", r.stdout)

    def test_group_and_status_are_mutually_exclusive(self) -> None:
        r = run_cli(
            "list", "--override", "scratch", "--group", "--status", "todo",
            home=self.home,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("not allowed with", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
