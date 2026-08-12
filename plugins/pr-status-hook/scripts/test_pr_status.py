#!/usr/bin/env python3
"""Tests for pr_status.py.

This hook runs at every turn end in every session. A regression here is the
most visible one this plugin can ship, and `pr-status.sh` had no tests at all.
So the port was guarded a second way. `characterize.sh` drives the hook across
thirteen repository states. `golden-pr-status.txt` is what the original shell
script printed for each. `TestGoldenParity` re-runs that comparison.

Stdlib-only, so no pytest is needed. Unittest discovery works too.

    python3 plugins/pr-status-hook/scripts/test_pr_status.py
"""
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Dict, Optional, Tuple

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import pr_status as hook  # noqa: E402

PR_URL = "https://github.com/acme/widgets/pull/42"
PORCELAIN = ("git", "status", "--porcelain")
AHEAD = ("git", "rev-list", "--count", "@{u}..HEAD")


def runner(mapping: Dict[Tuple[str, ...], str]):
    def run(argv):
        for prefix, result in mapping.items():
            if list(argv)[: len(prefix)] == list(prefix):
                return result
        return None

    return run


def status(pr_url: Optional[str] = None, ahead: Optional[str] = "0", dirty: int = 0):
    return hook.BranchStatus(pr_url=pr_url, ahead=ahead, dirty=dirty)


class TestCountDirty(unittest.TestCase):
    def test_a_clean_tree_is_zero(self):
        self.assertEqual(hook.count_dirty(runner({})), 0)

    def test_each_porcelain_line_is_one_file(self):
        run = runner({PORCELAIN: " M a.txt\n?? b.txt\n M c.txt"})
        self.assertEqual(hook.count_dirty(run), 3)

    def test_blank_lines_do_not_count(self):
        run = runner({PORCELAIN: " M a.txt\n\n?? b.txt\n"})
        self.assertEqual(hook.count_dirty(run), 2)


class TestCountAhead(unittest.TestCase):
    def test_no_upstream_is_none(self):
        """`git rev-list` fails rather than answering zero, and that is the signal."""
        self.assertIsNone(hook.count_ahead(runner({})))

    def test_level_with_upstream_is_zero(self):
        self.assertEqual(hook.count_ahead(runner({AHEAD: "0"})), "0")

    def test_unpushed_commits_are_counted(self):
        self.assertEqual(hook.count_ahead(runner({AHEAD: "3"})), "3")


class TestIsQuiet(unittest.TestCase):
    def test_nothing_to_say_stays_silent(self):
        self.assertTrue(hook.is_quiet(status()))

    def test_an_open_pr_is_worth_saying(self):
        self.assertFalse(hook.is_quiet(status(pr_url=PR_URL)))

    def test_unpushed_commits_are_worth_saying(self):
        self.assertFalse(hook.is_quiet(status(ahead="2")))

    def test_a_dirty_tree_is_worth_saying(self):
        self.assertFalse(hook.is_quiet(status(dirty=1)))

    def test_a_missing_upstream_is_worth_saying(self):
        """None means never pushed, which must not be mistaken for level."""
        self.assertFalse(hook.is_quiet(status(ahead=None)))


class TestBuildBanner(unittest.TestCase):
    """The exact text, because users have been reading it for months."""

    def test_pr_and_all_pushed(self):
        self.assertEqual(
            hook.build_banner("feat/x", status(pr_url=PR_URL)),
            f"PR {PR_URL} · ✓ all commits pushed",
        )

    def test_no_pr_names_the_branch(self):
        self.assertEqual(
            hook.build_banner("feat/x", status(dirty=1)),
            "No PR for branch 'feat/x' · ✓ all commits pushed · ✎ 1 file(s) uncommitted",
        )

    def test_no_upstream(self):
        self.assertEqual(
            hook.build_banner("feat/x", status(ahead=None)),
            "No PR for branch 'feat/x' · ⚠ branch has no upstream (never pushed)",
        )

    def test_unpushed_and_dirty(self):
        self.assertEqual(
            hook.build_banner("feat/x", status(pr_url=PR_URL, ahead="2", dirty=3)),
            f"PR {PR_URL} · ⚠ 2 commit(s) NOT pushed · ✎ 3 file(s) uncommitted",
        )

    def test_the_link_is_the_full_url(self):
        self.assertIn(PR_URL, hook.build_banner("feat/x", status(pr_url=PR_URL)))


class TestGoldenParity(unittest.TestCase):
    """The port must print what the shell script printed, for every state."""

    def test_matches_the_recorded_shell_output(self):
        golden = HERE / "golden-pr-status.txt"
        done = subprocess.run(
            ["bash", str(HERE / "characterize.sh"), "python3", str(HERE / "pr_status.py")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, golden.read_text())


if __name__ == "__main__":
    unittest.main()
