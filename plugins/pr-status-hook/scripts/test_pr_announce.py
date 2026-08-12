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
PR_VIEW = ("gh", "pr", "view")


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


class TestAnnounce(unittest.TestCase):
    """Gate-by-gate, with `git`/`gh` faked through a patched runner factory."""

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
    ) -> None:
        """Stand in for `git` and `gh`. A None branch means "not in a repo"."""
        mapping: Dict[Tuple[str, ...], str] = {}
        if branch is not None:
            mapping[BRANCH] = branch
        if url is not None:
            mapping[PR_VIEW] = url
        hook.make_runner = lambda cwd, timeout, deadline=None: runner(mapping)

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
        self.assertEqual(hook.announce(parsed, self.root, now=1000.0), PR_URL)

    def test_second_call_within_the_interval_stays_quiet(self):
        self.fake_git_and_gh()
        parsed = parse(payload())
        hook.announce(parsed, self.root, now=1000.0)
        self.assertIsNone(hook.announce(parsed, self.root, now=1010.0))

    def test_call_after_the_interval_announces_again(self):
        self.fake_git_and_gh()
        parsed = parse(payload())
        hook.announce(parsed, self.root, now=1000.0)
        self.assertEqual(hook.announce(parsed, self.root, now=1400.0), PR_URL)

    def test_a_second_branch_is_rate_limited_separately(self):
        self.fake_git_and_gh(branch="feat/a")
        parsed = parse(payload())
        self.assertEqual(hook.announce(parsed, self.root, now=1000.0), PR_URL)
        self.fake_git_and_gh(branch="feat/b")
        self.assertEqual(hook.announce(parsed, self.root, now=1010.0), PR_URL)

    def test_a_second_session_is_rate_limited_separately(self):
        self.fake_git_and_gh()
        first = parse(payload(session="sess-1"))
        second = parse(payload(session="sess-2"))
        self.assertEqual(hook.announce(first, self.root, now=1000.0), PR_URL)
        self.assertEqual(hook.announce(second, self.root, now=1010.0), PR_URL)

    def test_a_pr_appearing_later_still_announces(self):
        """No PR at first, so no marker; the next call must not be rate-limited."""
        self.fake_git_and_gh(url=None)
        parsed = parse(payload())
        self.assertIsNone(hook.announce(parsed, self.root, now=1000.0))
        self.fake_git_and_gh(url=PR_URL)
        self.assertEqual(hook.announce(parsed, self.root, now=1005.0), PR_URL)


class TestMainEmits(unittest.TestCase):
    """`main` picks the channel the banner reaches the user through.

    Every other end-to-end case asserts silence, so without these two the print
    itself carries no test. Swapping the branches would keep the suite green.
    """

    def setUp(self):
        real_announce = hook.announce
        self.addCleanup(lambda: setattr(hook, "announce", real_announce))
        hook.announce = lambda parsed, tmp_root, now: PR_URL

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
