#!/usr/bin/env python3
"""Repo derivation, slugify, and name/extension validation (naming.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import subprocess
import unittest

from support import (  # noqa: F401 — also inserts scripts/ onto sys.path
    IsolatedHomeTestCase,
    run_cli,
)

from plan_keeper.naming import plan_filename, plan_group_key  # noqa: E402


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


class TestPlanFilename(unittest.TestCase):
    def test_md_with_kind_gets_double_hyphen_suffix(self) -> None:
        self.assertEqual(
            plan_filename("2026-06-04", "noun-first-provider-commands", "md", "exec-plan"),
            "2026-06-04-noun-first-provider-commands--exec-plan.md",
        )

    def test_md_without_kind_is_unchanged(self) -> None:
        self.assertEqual(
            plan_filename("2026-06-04", "my-topic", "md", None),
            "2026-06-04-my-topic.md",
        )

    def test_non_md_never_gets_kind_suffix(self) -> None:
        # Defensive: the caller already rejects --kind for non-md, but the
        # helper is the single source of truth, so it must not append either.
        self.assertEqual(
            plan_filename("2026-06-04", "tasks", "json", "exec-plan"),
            "2026-06-04-tasks.json",
        )

    def test_topic_ending_in_kind_word_does_not_collapse(self) -> None:
        # slug already ends in "design"; the -- boundary keeps it unambiguous.
        self.assertEqual(
            plan_filename("2026-06-04", "auth-design", "md", "design"),
            "2026-06-04-auth-design--design.md",
        )


class TestPlanGroupKey(unittest.TestCase):
    def test_recovers_slug_stripping_date_and_kind(self) -> None:
        self.assertEqual(
            plan_group_key("2026-06-04-noun-first-provider-commands--exec-plan.md"),
            "noun-first-provider-commands",
        )

    def test_recovers_slug_when_no_kind_suffix(self) -> None:
        self.assertEqual(
            plan_group_key("2026-06-03-noun-first-provider-commands.md"),
            "noun-first-provider-commands",
        )

    def test_round_trips_topic_ending_in_kind_word(self) -> None:
        self.assertEqual(plan_group_key("2026-06-04-auth-design--design.md"), "auth-design")

    def test_collision_suffixed_file_groups_with_original(self) -> None:
        # A same-kind/same-day/same-topic re-save lands at `…--<kind>-N.md`
        # (find_unused_suffix appends -N to the whole stem); the `-N` must be
        # stripped so the copy groups with its original, not as a new project.
        self.assertEqual(plan_group_key("2026-06-04-dup--spec-2.md"), "dup")
        self.assertEqual(plan_group_key("2026-06-04-dup--spec-10.md"), "dup")

    def test_topic_ending_in_kind_word_with_numeric_tail_is_kept(self) -> None:
        # `auth-spec-2` is a legitimate slug (topic "auth spec 2"); with a real
        # Kind suffix it round-trips, and the numeric strip must not eat into it.
        self.assertEqual(
            plan_group_key("2026-06-04-auth-spec-2--design.md"), "auth-spec-2"
        )

    def test_trailing_segment_not_a_valid_kind_is_kept(self) -> None:
        # "--foo" is not a Kind, so it stays part of the slug (cannot happen
        # via slugify, which collapses --, but the recovery must be safe).
        self.assertEqual(plan_group_key("2026-06-04-a--foo.md"), "a--foo")

    def test_no_date_prefix_falls_back_to_stem(self) -> None:
        self.assertEqual(plan_group_key("README.md"), "README")

    def test_no_date_prefix_with_kind_suffix_is_not_stripped(self) -> None:
        # The --<kind> recovery applies only to dated plan filenames (the only
        # shape plan_filename produces). A no-date name falls back to its whole
        # stem, so a hand-named `README--spec.md` is NOT mistaken for a `spec`
        # stage of project `README`.
        self.assertEqual(plan_group_key("README--spec.md"), "README--spec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
