#!/usr/bin/env python3
"""Tests for pr_status.py.

This hook runs at every turn end in every session. A regression here is the
most visible one this plugin can ship.

`characterize.sh` builds thirteen repository states and records what the hook
prints for each into `golden-banners.txt`. `TestRecordedBanners` replays that.
It started as proof that the Python port matched the shell script it replaced,
which it did byte for byte. It is kept because it is the only test here that
runs against real git repositories.

Stdlib-only, so no pytest is needed. Unittest discovery works too.

    python3 plugins/pr-status-hook/scripts/test_pr_status.py
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional, Tuple
from unittest import mock

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


def status(pr_url: Optional[str] = None, ahead: Optional[str] = "0", dirty: int = 0,
           state: str = "OPEN"):
    pull = hook.PullRequest(url=pr_url, state=state) if pr_url else None
    return hook.BranchStatus(pull=pull, ahead=ahead, dirty=dirty)


PORCELAIN_V2 = """\
# branch.oid abc123
# branch.head feat/x
# branch.upstream origin/feat/x
# branch.ab +2 -0
1 .M N... 100644 100644 100644 aaa bbb a.txt
? untracked.txt
"""


_SCRATCH_STATE = tempfile.TemporaryDirectory()


def setUpModule():
    """Keep the hooks' state directory out of the real home while testing.

    `state_root()` honours XDG_STATE_HOME. Redirecting it here means a test run
    never leaves cache or marker files behind for a real session to read.
    """
    os.environ["XDG_STATE_HOME"] = _SCRATCH_STATE.name


def tearDownModule():
    _SCRATCH_STATE.cleanup()


class TestParseWorkTree(unittest.TestCase):
    """One `git status --porcelain=v2 --branch` replaced three separate calls."""

    def test_reads_every_field_from_one_output(self):
        tree = hook.parse_work_tree(PORCELAIN_V2)
        self.assertEqual(tree.branch, "feat/x")
        self.assertEqual(tree.upstream, "origin/feat/x")
        self.assertEqual(tree.ahead, "2")
        self.assertEqual(tree.dirty, 2)
        self.assertEqual(tree.head_sha, "abc123")

    def test_a_detached_head_has_no_branch(self):
        tree = hook.parse_work_tree("# branch.oid abc\n# branch.head (detached)\n")
        self.assertIsNone(tree.branch)

    def test_no_upstream_leaves_ahead_unknown(self):
        """`git` omits both header lines, and that absence is the signal."""
        tree = hook.parse_work_tree("# branch.oid abc\n# branch.head feat/x\n")
        self.assertIsNone(tree.upstream)
        self.assertIsNone(tree.ahead)

    def test_level_with_upstream_is_zero_not_none(self):
        tree = hook.parse_work_tree(
            "# branch.head feat/x\n# branch.upstream origin/feat/x\n# branch.ab +0 -0\n"
        )
        self.assertEqual(tree.ahead, "0")

    def test_a_clean_tree_counts_no_files(self):
        tree = hook.parse_work_tree("# branch.head feat/x\n")
        self.assertEqual(tree.dirty, 0)

    def test_header_lines_are_never_counted_as_files(self):
        tree = hook.parse_work_tree(PORCELAIN_V2)
        self.assertEqual(tree.dirty, 2)

    def test_no_output_at_all_is_survivable(self):
        tree = hook.parse_work_tree(None)
        self.assertIsNone(tree.branch)
        self.assertEqual(tree.dirty, 0)


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
            f"PR: {PR_URL} · ✓ all commits pushed",
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
            f"PR: {PR_URL} · ⚠ 2 commit(s) NOT pushed · ✎ 3 file(s) uncommitted",
        )

    def test_the_link_is_the_full_url(self):
        self.assertIn(PR_URL, hook.build_banner("feat/x", status(pr_url=PR_URL)))

    def test_a_merged_pr_still_shows_its_link(self):
        """The link is most wanted at the moment the PR merges, not least."""
        banner = hook.build_banner("feat/x", status(pr_url=PR_URL, state="MERGED"))
        self.assertIn(PR_URL, banner)

    def test_a_non_open_pr_is_labelled(self):
        """An unlabelled link reads as open, and every other fact is labelled."""
        self.assertIn("(merged)", hook.build_banner("feat/x", status(pr_url=PR_URL, state="MERGED")))
        self.assertIn("(closed)", hook.build_banner("feat/x", status(pr_url=PR_URL, state="CLOSED")))

    def test_an_open_pr_is_not_labelled(self):
        """The common case must look exactly as it did before labelling."""
        self.assertNotIn("(", hook.build_banner("feat/x", status(pr_url=PR_URL)).split(" · ")[0])


class TestMainHonoursTheParserContract(unittest.TestCase):
    """An unusable payload means silence, the same as in pr_announce.py."""

    def setUp(self):
        real = hook.make_runner
        self.addCleanup(lambda: setattr(hook, "make_runner", real))
        # Everything a banner needs, so only the payload gate can stop it.
        hook.make_runner = lambda cwd, timeout, deadline=None: runner(
            {
                ("git", "status", "--porcelain=v2", "--branch"): (
                    "# branch.oid abc123\n"
                    "# branch.head feat/x\n"
                    "# branch.upstream origin/feat/x\n"
                    "# branch.ab +0 -0\n"
                ),
                ("gh", "pr", "view"): f"OPEN\t{PR_URL}",
            }
        )

    def run_main(self, payload: str):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(payload)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(hook.main(), 0)
        return out.getvalue(), err.getvalue()

    def test_malformed_json_prints_nothing(self):
        out, err = self.run_main("}{ not json")
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_a_valid_payload_still_prints(self):
        """The guard must not silence the ordinary path."""
        out, _ = self.run_main('{"hook_event_name": "Stop", "cwd": ""}')
        self.assertIn(PR_URL, out)


class TestRecordedBanners(unittest.TestCase):
    """The banner this hook prints, across thirteen repository states.

    Every other test in this file feeds a fake runner a canned string. This
    one builds real git repositories and runs the real hook against them. It is
    the only end-to-end coverage here.

    To change a banner on purpose, run `characterize.sh` and commit the new
    `golden-banners.txt` alongside the code change. The diff on that file is
    then the reviewable statement of what users will see differently.
    """

    def test_matches_the_recorded_banners(self):
        """Compared as bytes.

        Text mode decodes and normalizes line endings, so it cannot enforce the
        byte-for-byte claim this fixture makes. `read_bytes` and a binary
        capture can.
        """
        recorded = (HERE / "golden-banners.txt").read_bytes()
        done = subprocess.run(
            ["bash", str(HERE / "characterize.sh"), "python3", str(HERE / "pr_status.py")],
            capture_output=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertEqual(done.stdout, recorded)


if __name__ == "__main__":
    unittest.main()
