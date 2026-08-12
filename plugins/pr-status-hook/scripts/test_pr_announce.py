#!/usr/bin/env python3
"""Tests for pr_announce.py.

Every gate in `announce` gets a test. Each gate is a way the user can silently
lose the PR link. The end-to-end cases drive the real script over a pipe, which
is how the harness calls it.

Stdlib-only, so no pytest is needed. Unittest discovery works too.

    python3 plugins/pr-status-hook/scripts/test_pr_announce.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional, Tuple
from unittest import mock

HERE = Path(__file__).parent
SCRIPT = HERE / "pr_announce.py"

sys.path.insert(0, str(HERE))
import pr_announce as hook  # noqa: E402
import pr_hook_common as common  # noqa: E402

PR_URL = "https://github.com/acme/widgets/pull/42"


def payload(command="gh pr create --title x", session="sess-1", event="PostToolUse", cwd="/repo"):
    return json.dumps(
        {
            "session_id": session,
            "hook_event_name": event,
            "cwd": cwd,
            "tool_input": {"command": command},
        }
    )


def parse(raw) -> common.HookInput:
    """Parse a payload the tests know is well-formed, and narrow away the None."""
    result = common.parse_hook_input(raw)
    assert result is not None, f"expected a parsable payload, got {raw!r}"
    return result


def runner(mapping):
    """Build a CommandRunner backed by a dict of argv-prefix -> stdout."""

    def run(argv):
        for prefix, result in mapping.items():
            if list(argv)[: len(prefix)] == list(prefix):
                return result
        return None

    return run


BRANCH = ("git", "rev-parse", "--abbrev-ref", "HEAD")
# The four-element BRANCH prefix cannot match this three-element argv, and this
# one cannot match PR_VIEW, so the mapping is order-independent.
HEAD_SHA = ("git", "rev-parse", "HEAD")
PR_VIEW = ("gh", "pr", "view")

CWD = "/repo"
COMMIT = "abc123"


_SCRATCH_STATE = tempfile.TemporaryDirectory()


def setUpModule():
    """Keep the hooks' state directory out of the real home while testing.

    `state_root()` honours XDG_STATE_HOME. Redirecting it here means a test run
    never leaves cache or marker files behind for a real session to read.
    """
    os.environ["XDG_STATE_HOME"] = _SCRATCH_STATE.name


def tearDownModule():
    _SCRATCH_STATE.cleanup()


class TestIsTriggerCommand(unittest.TestCase):
    def test_matches_the_three_triggers(self):
        for command in ("gh pr create --draft", "gh pr view --json url", "gh pr checks --watch"):
            self.assertTrue(hook.is_trigger_command(command), command)

    def test_matches_when_embedded_in_a_substitution(self):
        self.assertTrue(hook.is_trigger_command("BASE=$(gh pr view --json baseRefName)"))

    def test_ignores_other_gh_subcommands(self):
        self.assertFalse(hook.is_trigger_command("gh pr list --state open"))
        self.assertFalse(hook.is_trigger_command("gh issue view 3"))

    def test_ignores_unrelated_commands(self):
        self.assertFalse(hook.is_trigger_command("npm test"))
        self.assertFalse(hook.is_trigger_command(""))


class TestMarkerPath(unittest.TestCase):
    def test_slashes_in_the_branch_do_not_nest_directories(self):
        path = hook.marker_path(Path("/tmp"), "sess-1", "emdash/show-pr-link")
        self.assertEqual(path.parent, Path("/tmp") / hook.MARKER_DIR_NAME)
        self.assertNotIn("/", path.name)

    def test_session_and_branch_both_appear(self):
        path = hook.marker_path(Path("/tmp"), "sess-1", "feat")
        self.assertTrue(path.name.startswith("sess-1--feat-"), path.name)

    def test_different_branches_get_different_markers(self):
        first = hook.marker_path(Path("/tmp"), "s", "feat/a")
        second = hook.marker_path(Path("/tmp"), "s", "feat/b")
        self.assertNotEqual(first, second)

    def test_branches_that_sanitize_alike_still_differ(self):
        """`feat/a` and `feat_a` are both valid, and both sanitize to `feat_a`.

        Sharing a marker would let one branch mute the other's banner for five
        minutes. Worktrees make two branches in one session ordinary.
        """
        slashed = hook.marker_path(Path("/tmp"), "s", "feat/a")
        underscored = hook.marker_path(Path("/tmp"), "s", "feat_a")
        self.assertNotEqual(slashed, underscored)

    def test_sessions_that_sanitize_alike_still_differ(self):
        first = hook.marker_path(Path("/tmp"), "a/b", "feat")
        second = hook.marker_path(Path("/tmp"), "a_b", "feat")
        self.assertNotEqual(first, second)

    def test_the_branch_stays_readable_in_the_name(self):
        """Whoever debugs a stuck marker should recognize it on sight."""
        path = hook.marker_path(Path("/tmp"), "sess-1", "emdash/show-pr-link")
        self.assertIn("emdash_show-pr-link", path.name)


class TestShouldAnnounce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.marker = Path(self.tmp.name) / "marker"

    def test_absent_marker_is_due(self):
        self.assertTrue(hook.should_announce(self.marker, now=1000.0, interval=300.0))

    def test_fresh_marker_is_not_due(self):
        hook.touch_marker(self.marker, now=1000.0)
        self.assertFalse(hook.should_announce(self.marker, now=1100.0, interval=300.0))

    def test_stale_marker_is_due_again(self):
        hook.touch_marker(self.marker, now=1000.0)
        self.assertTrue(hook.should_announce(self.marker, now=1400.0, interval=300.0))

    def test_the_interval_boundary_counts_as_due(self):
        hook.touch_marker(self.marker, now=1000.0)
        self.assertTrue(hook.should_announce(self.marker, now=1300.0, interval=300.0))

    def test_an_unwritable_marker_does_not_raise(self):
        """A marker we cannot write costs a duplicate banner, never the banner."""
        unwritable = Path(self.tmp.name) / "ro" / "marker"
        unwritable.parent.mkdir()
        unwritable.parent.chmod(0o500)
        self.addCleanup(unwritable.parent.chmod, 0o700)
        hook.touch_marker(unwritable, now=1000.0)
        self.assertTrue(hook.should_announce(unwritable, now=1000.0, interval=300.0))

    def test_a_planted_symlink_cannot_redirect_the_stamp(self):
        """The PR cache refuses this, and two writes in one directory agree."""
        victim = Path(self.tmp.name) / "victim"
        self.marker.symlink_to(victim)
        hook.touch_marker(self.marker, now=1000.0)
        self.assertFalse(victim.exists())

    def test_a_planted_symlink_cannot_decide_the_rate_limit(self):
        """`lstat` reads the link itself, so its target's mtime cannot mute us.

        The link is stale and its target is fresh. Following the link would
        answer "not due" and cost the banner. Reading the link answers "due".
        """
        target = Path(self.tmp.name) / "target"
        target.touch()
        os.utime(target, (1090.0, 1090.0))
        self.marker.symlink_to(target)
        os.utime(self.marker, (500.0, 500.0), follow_symlinks=False)
        self.assertTrue(hook.should_announce(self.marker, now=1100.0, interval=300.0))

    def test_the_marker_and_the_directory_it_creates_are_private(self):
        nested = Path(self.tmp.name) / "made-here" / "marker"
        hook.touch_marker(nested, now=1000.0)
        self.assertEqual(nested.stat().st_mode & 0o777, 0o600)
        self.assertEqual(nested.parent.stat().st_mode & 0o777, 0o700)


class AnnounceFixture(unittest.TestCase):
    """The patched runner factory and state root both announce suites share.

    It carries no `test_` method of its own, on purpose. A suite that inherited
    one would run it again under its own name. The count would then claim
    coverage that is not there.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.real_make_runner = hook.make_runner

        def restore():
            hook.make_runner = self.real_make_runner

        self.addCleanup(restore)

    def fake_git_and_gh(
        self,
        branch: Optional[str] = "feat/x",
        url: Optional[str] = PR_URL,
        state: str = "OPEN",
    ) -> None:
        """Stand in for `git` and `gh`. A None branch means "not in a repo"."""
        mapping: Dict[Tuple[str, ...], str] = {}
        if branch is not None:
            mapping[BRANCH] = branch
            mapping[HEAD_SHA] = COMMIT
        if url is not None:
            mapping[PR_VIEW] = f"{state}\t{url}"
        hook.make_runner = lambda cwd, timeout, deadline=None: runner(mapping)

    def announced_url(self, parsed, now: float) -> Optional[str]:
        """Run `announce` and reduce its answer to the URL, or None if quiet."""
        pull = hook.announce(parsed, self.root, now=now)
        return None if pull is None else pull.url

    def cached(self, branch: str = "feat/x") -> Optional[common.PullRequest]:
        """Read back what `announce` left for `pr_status.py`, through its key."""
        path = common.pr_cache_path(self.root, CWD, branch, COMMIT)
        return common.read_pr_cache(path, now=0.0)


class TestAnnounce(AnnounceFixture, unittest.TestCase):
    """Gate-by-gate, with `git`/`gh` faked through a patched runner factory."""

    def test_non_trigger_command_announces_nothing(self):
        self.fake_git_and_gh()
        parsed = parse(payload(command="npm test"))
        self.assertIsNone(hook.announce(parsed, self.root, now=1000.0))

    def test_outside_a_repo_announces_nothing(self):
        self.fake_git_and_gh(branch=None)
        parsed = parse(payload())
        self.assertIsNone(hook.announce(parsed, self.root, now=1000.0))

    def test_default_branch_announces_nothing(self):
        self.fake_git_and_gh(branch="main")
        parsed = parse(payload())
        self.assertIsNone(hook.announce(parsed, self.root, now=1000.0))

    def test_no_pr_announces_nothing_and_writes_no_marker(self):
        self.fake_git_and_gh(url=None)
        parsed = parse(payload())
        self.assertIsNone(hook.announce(parsed, self.root, now=1000.0))
        marker = hook.marker_path(self.root, "sess-1", "feat/x")
        self.assertFalse(marker.exists())

    def test_first_call_announces(self):
        self.fake_git_and_gh()
        parsed = parse(payload())
        self.assertEqual(self.announced_url(parsed, now=1000.0), PR_URL)

    def test_second_call_within_the_interval_stays_quiet(self):
        self.fake_git_and_gh()
        parsed = parse(payload())
        hook.announce(parsed, self.root, now=1000.0)
        self.assertIsNone(hook.announce(parsed, self.root, now=1010.0))

    def test_call_after_the_interval_announces_again(self):
        self.fake_git_and_gh()
        parsed = parse(payload())
        hook.announce(parsed, self.root, now=1000.0)
        self.assertEqual(self.announced_url(parsed, now=1400.0), PR_URL)

    def test_a_second_branch_is_rate_limited_separately(self):
        self.fake_git_and_gh(branch="feat/a")
        parsed = parse(payload())
        self.assertEqual(self.announced_url(parsed, now=1000.0), PR_URL)
        self.fake_git_and_gh(branch="feat/b")
        self.assertEqual(self.announced_url(parsed, now=1010.0), PR_URL)

    def test_a_second_session_is_rate_limited_separately(self):
        self.fake_git_and_gh()
        first = parse(payload(session="sess-1"))
        second = parse(payload(session="sess-2"))
        self.assertEqual(self.announced_url(first, now=1000.0), PR_URL)
        self.assertEqual(self.announced_url(second, now=1010.0), PR_URL)

    def test_a_pr_appearing_later_still_announces(self):
        """No PR at first, so no marker; the next call must not be rate-limited."""
        self.fake_git_and_gh(url=None)
        parsed = parse(payload())
        self.assertIsNone(hook.announce(parsed, self.root, now=1000.0))
        self.fake_git_and_gh(url=PR_URL)
        self.assertEqual(self.announced_url(parsed, now=1005.0), PR_URL)

    def test_a_non_open_pr_still_announces(self):
        """A PR that merges mid-session must not make its own link vanish."""
        self.fake_git_and_gh(state="MERGED")
        parsed = parse(payload())
        self.assertEqual(self.announced_url(parsed, now=1000.0), PR_URL)


class TestCacheHandoff(AnnounceFixture, unittest.TestCase):
    """What `announce` leaves behind for `pr_status.py` to read.

    `pr_announce.py` writes this cache and never reads it. So the write has to
    be checked from the other side, through the key `pr_status.find_pr` builds.
    Without that, the two hooks could key the entry differently and every test
    here would still pass. The turn-end banner would quietly ask `gh` again.
    """

    def test_announcing_records_the_pr_for_the_turn_end_hook(self):
        self.fake_git_and_gh()
        self.assertEqual(self.announced_url(parse(payload()), now=1000.0), PR_URL)
        entry = self.cached()
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.url, PR_URL)
        self.assertEqual(entry.state, "OPEN")

    def test_the_recorded_state_is_the_one_gh_reported(self):
        """`pr_status.py` labels from this field, so a wrong one mislabels."""
        self.fake_git_and_gh(state="MERGED")
        self.assertEqual(self.announced_url(parse(payload()), now=1000.0), PR_URL)
        entry = self.cached()
        assert entry is not None
        self.assertEqual(entry.state, "MERGED")
        self.assertFalse(entry.is_open)

    def test_no_pr_records_nothing(self):
        self.fake_git_and_gh(url=None)
        self.assertIsNone(hook.announce(parse(payload()), self.root, now=1000.0))
        self.assertIsNone(self.cached())

    def test_a_second_branch_gets_its_own_entry(self):
        """The key carries the branch, so one branch cannot answer for another."""
        self.fake_git_and_gh(branch="feat/a")
        self.assertEqual(self.announced_url(parse(payload()), now=1000.0), PR_URL)
        self.assertIsNotNone(self.cached("feat/a"))
        self.assertIsNone(self.cached("feat/b"))


class TestMainEmits(unittest.TestCase):
    """`main` picks the channel the banner reaches the user through.

    Every other end-to-end case asserts silence, so without these two the print
    itself carries no test. Swapping the branches would keep the suite green.
    """

    def setUp(self):
        real_announce = hook.announce
        self.addCleanup(lambda: setattr(hook, "announce", real_announce))
        self.state = "OPEN"
        hook.announce = lambda parsed, tmp_root, now: common.PullRequest(
            url=PR_URL, state=self.state
        )

    def emit(self, event):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(payload(event=event))):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(hook.main(), 0)
        return out.getvalue(), err.getvalue()

    def test_claude_gets_the_json_banner(self):
        out, err = self.emit("PostToolUse")
        self.assertEqual(json.loads(out)["systemMessage"], f"PR: {PR_URL}")
        self.assertEqual(err, "")

    def test_cursor_gets_the_stderr_banner(self):
        out, err = self.emit(common.CURSOR_POST_TOOL_EVENT)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), f"PR: {PR_URL}")

    def test_a_merged_pr_is_labelled_on_the_way_out(self):
        """The turn-end banner labels state, so this one must not omit it."""
        self.state = "MERGED"
        out, _ = self.emit("PostToolUse")
        self.assertEqual(
            json.loads(out)["systemMessage"], f"PR: {PR_URL} (merged)"
        )


class TestEndToEnd(unittest.TestCase):
    """Drive the real script over a pipe, the way the harness invokes it."""

    def run_hook(self, stdin, cwd=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=cwd,
        )

    def test_non_trigger_command_prints_nothing(self):
        done = self.run_hook(payload(command="ls -la"))
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout, "")
        self.assertEqual(done.stderr, "")

    def test_malformed_stdin_exits_quietly(self):
        done = self.run_hook("}{not json")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout, "")

    def test_empty_stdin_exits_quietly(self):
        done = self.run_hook("")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout, "")

    def test_outside_a_git_repo_prints_nothing(self):
        with tempfile.TemporaryDirectory() as outside:
            done = self.run_hook(payload(cwd=outside), cwd=outside)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout, "")


if __name__ == "__main__":
    unittest.main()
