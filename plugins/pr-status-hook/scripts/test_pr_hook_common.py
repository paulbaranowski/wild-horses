#!/usr/bin/env python3
"""Tests for pr_hook_common.py.

This module holds what the two hooks must not disagree on, so a bug here breaks
both at once. The `gh` query and `pr_link` get the most attention. Those are the
rules that used to live twice, once in `jq` and once in Python.

Stdlib-only, so no pytest is needed. Unittest discovery works too.

    python3 plugins/pr-status-hook/scripts/test_pr_hook_common.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Dict, Tuple

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import pr_hook_common as common  # noqa: E402

PR_URL = "https://github.com/acme/widgets/pull/42"
BRANCH = ("git", "rev-parse", "--abbrev-ref", "HEAD")
PR_VIEW = ("gh", "pr", "view")


def runner(mapping: Dict[Tuple[str, ...], str]):
    """Build a CommandRunner backed by a dict of argv-prefix -> stdout."""

    def run(argv):
        for prefix, result in mapping.items():
            if list(argv)[: len(prefix)] == list(prefix):
                return result
        return None

    return run


def parse(raw) -> common.HookInput:
    """Parse a payload the tests know is well-formed, and narrow away the None."""
    result = common.parse_hook_input(raw)
    assert result is not None, f"expected a parsable payload, got {raw!r}"
    return result


_SCRATCH_STATE = tempfile.TemporaryDirectory()


def setUpModule():
    """Keep the hooks' state directory out of the real home while testing.

    `state_root()` honours XDG_STATE_HOME. Redirecting it here means a test run
    never leaves cache or marker files behind for a real session to read.
    """
    os.environ["XDG_STATE_HOME"] = _SCRATCH_STATE.name


def tearDownModule():
    _SCRATCH_STATE.cleanup()


class TestParseHookInput(unittest.TestCase):
    def test_reads_the_fields_the_hooks_need(self):
        parsed = parse(
            json.dumps(
                {
                    "session_id": "abc",
                    "hook_event_name": "PostToolUse",
                    "cwd": "/repo",
                    "tool_input": {"command": "gh pr view"},
                }
            )
        )
        self.assertEqual(parsed.command, "gh pr view")
        self.assertEqual(parsed.session_id, "abc")
        self.assertEqual(parsed.event_name, "PostToolUse")
        self.assertEqual(parsed.cwd, "/repo")

    def test_malformed_json_is_unusable(self):
        self.assertIsNone(common.parse_hook_input("not json{"))

    def test_non_object_payload_is_unusable(self):
        self.assertIsNone(common.parse_hook_input('["a", "b"]'))

    def test_missing_fields_fall_back(self):
        parsed = parse("{}")
        self.assertEqual(parsed.command, "")
        self.assertEqual(parsed.session_id, "nosession")
        self.assertEqual(parsed.event_name, "")

    def test_tool_input_without_command_is_empty(self):
        parsed = parse(json.dumps({"tool_input": {"description": "x"}}))
        self.assertEqual(parsed.command, "")


class TestAnnounceableBranch(unittest.TestCase):
    def test_default_branches_are_skipped(self):
        for branch in ("main", "master"):
            self.assertIsNone(common.announceable_branch(runner({BRANCH: branch})), branch)

    def test_detached_head_is_skipped(self):
        self.assertIsNone(common.announceable_branch(runner({BRANCH: "HEAD"})))

    def test_outside_a_work_tree_there_is_no_branch(self):
        self.assertIsNone(common.announceable_branch(runner({})))

    def test_feature_branch_is_announceable(self):
        run = runner({BRANCH: "emdash/show-pr-link"})
        self.assertEqual(common.announceable_branch(run), "emdash/show-pr-link")


class TestFindPullRequest(unittest.TestCase):
    def test_returns_url_and_state(self):
        pull = common.find_pull_request(runner({PR_VIEW: f"OPEN\t{PR_URL}"}))
        self.assertEqual(pull, common.PullRequest(url=PR_URL, state="OPEN"))
        assert pull is not None
        self.assertTrue(pull.is_open)

    def test_a_merged_pr_is_still_returned(self):
        """The state is reported, not filtered. A merged PR keeps its link."""
        pull = common.find_pull_request(runner({PR_VIEW: f"MERGED\t{PR_URL}"}))
        assert pull is not None
        self.assertEqual(pull.url, PR_URL)
        self.assertFalse(pull.is_open)

    def test_no_pr_returns_none(self):
        self.assertIsNone(common.find_pull_request(runner({})))

    def test_output_without_a_separator_is_rejected(self):
        self.assertIsNone(common.find_pull_request(runner({PR_VIEW: "no pull requests found"})))

    def test_non_url_output_is_rejected(self):
        self.assertIsNone(common.find_pull_request(runner({PR_VIEW: "OPEN\tnot-a-url"})))

    def test_the_query_asks_for_both_fields(self):
        seen = []

        def run(argv):
            seen.append(list(argv))
            return None

        common.find_pull_request(run)
        self.assertIn("url,state", seen[0])


class TestMakeRunner(unittest.TestCase):
    """The runner is the only place these hooks touch the outside world."""

    def test_returns_trimmed_stdout(self):
        run = common.make_runner("", common.GIT_TIMEOUT_SECONDS, common.new_deadline())
        self.assertEqual(run(["echo", "  hello  "]), "hello")

    def test_a_failing_command_is_none(self):
        run = common.make_runner("", common.GIT_TIMEOUT_SECONDS, common.new_deadline())
        self.assertIsNone(run(["false"]))

    def test_a_missing_binary_is_none_not_an_exception(self):
        run = common.make_runner("", common.GIT_TIMEOUT_SECONDS, common.new_deadline())
        self.assertIsNone(run(["definitely-not-a-real-binary-9f3a"]))

    def test_empty_output_is_none(self):
        run = common.make_runner("", common.GIT_TIMEOUT_SECONDS, common.new_deadline())
        self.assertIsNone(run(["true"]))

    def test_it_runs_in_the_directory_it_was_given(self):
        run = common.make_runner("/", common.GIT_TIMEOUT_SECONDS, common.new_deadline())
        self.assertEqual(run(["pwd"]), "/")


class TestSharedDeadline(unittest.TestCase):
    """One run's calls share a budget, so the wrapper never has to kill them.

    Per-call timeouts do not bound a hook. `pr_announce.py` makes three calls,
    so its per-call budget sums to 20 seconds, which is the wrapper exactly.
    """

    def test_the_budget_stays_under_the_wrapper_timeout(self):
        """The wrapper in hooks.json is 20s; leave room to print after."""
        self.assertLess(common.TOTAL_BUDGET_SECONDS, 20.0)

    def test_a_spent_budget_starts_no_further_call(self):
        past = time.monotonic() - 1.0
        run = common.make_runner("", common.GIT_TIMEOUT_SECONDS, past)
        self.assertIsNone(run(["echo", "hello"]))

    def test_a_live_budget_still_runs(self):
        run = common.make_runner("", common.GIT_TIMEOUT_SECONDS, common.new_deadline())
        self.assertEqual(run(["echo", "hello"]), "hello")

    def test_the_deadline_shortens_a_long_per_call_timeout(self):
        """A 10s call with 0.3s of budget left must be capped at the budget."""
        started = time.monotonic()
        run = common.make_runner("", 10.0, time.monotonic() + 0.3)
        self.assertIsNone(run([sys.executable, "-c", "import time; time.sleep(5)"]))
        self.assertLess(time.monotonic() - started, 3.0)


class TestPrLink(unittest.TestCase):
    """One spelling for one link, because two hooks print it.

    The two banners once used `PR <url>` and `PR: <url>`, and later agreed on
    the form while disagreeing about the state label. Both read this function
    now, so neither can drift from the other again.
    """

    def link(self, state: str) -> str:
        return common.pr_link(common.PullRequest(url=PR_URL, state=state))

    def test_an_open_pr_is_the_bare_link(self):
        self.assertEqual(self.link("OPEN"), f"PR: {PR_URL}")

    def test_a_merged_pr_carries_its_state(self):
        """An unlabelled link reads as open, whatever the PR actually is."""
        self.assertEqual(self.link("MERGED"), f"PR: {PR_URL} (merged)")

    def test_a_closed_pr_carries_its_state(self):
        self.assertEqual(self.link("CLOSED"), f"PR: {PR_URL} (closed)")

    def test_the_link_survives_every_state(self):
        """The link is the guaranteed part. The label is the extra."""
        for state in ("OPEN", "MERGED", "CLOSED", "SOMETHING_NEW"):
            self.assertIn(PR_URL, self.link(state), state)


class TestEmit(unittest.TestCase):
    """One banner, three harnesses, two channels."""

    def capture(self, runtime):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            common.emit("PR: x", runtime)
        return out.getvalue(), err.getvalue()

    def test_claude_gets_json_on_stdout(self):
        out, err = self.capture(common.RUNTIME_CLAUDE)
        self.assertEqual(json.loads(out)["systemMessage"], "PR: x")
        self.assertEqual(err, "")

    def test_cursor_gets_plain_text_on_stderr(self):
        out, err = self.capture(common.RUNTIME_CURSOR)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "PR: x")

    def test_grok_gets_plain_text_on_stderr(self):
        """Grok discards a passive hook's stdout, so the banner must use stderr."""
        out, err = self.capture(common.RUNTIME_GROK)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "PR: x")

    def test_the_json_stays_out_of_the_transcript(self):
        """Without suppressOutput the hook's own JSON prints after every call."""
        out, _ = self.capture(common.RUNTIME_CLAUDE)
        self.assertTrue(json.loads(out)["suppressOutput"])


class TestRuntimeDetection(unittest.TestCase):
    """Which harness sent this payload, read off a field it actually sends."""

    def runtime(self, payload):
        parsed = common.parse_hook_input(json.dumps(payload))
        assert parsed is not None
        return parsed

    def test_claude_pascal_case_event(self):
        got = self.runtime({"hook_event_name": "Stop"})
        self.assertEqual(got.runtime, common.RUNTIME_CLAUDE)

    def test_cursor_camel_case_event(self):
        got = self.runtime({"hook_event_name": common.CURSOR_POST_TOOL_EVENT})
        self.assertEqual(got.runtime, common.RUNTIME_CURSOR)

    def test_grok_camel_case_key(self):
        got = self.runtime({"hookEventName": common.GROK_POST_TOOL_EVENT})
        self.assertEqual(got.runtime, common.RUNTIME_GROK)

    def test_grok_command_and_session_read_from_camel_case(self):
        got = self.runtime({
            "hookEventName": "pre_tool_use",
            "sessionId": "s-1",
            "toolInput": {"command": "git status"},
        })
        self.assertEqual(got.command, "git status")
        self.assertEqual(got.session_id, "s-1")

    def test_claude_fields_still_read_from_snake_case(self):
        got = self.runtime({
            "hook_event_name": "PostToolUse",
            "session_id": "s-2",
            "tool_input": {"command": "git status"},
        })
        self.assertEqual(got.command, "git status")
        self.assertEqual(got.session_id, "s-2")

class TestStateRootIsPrivate(unittest.TestCase):
    """The cache and marker hold data the hook prints to the user.

    A world-writable `/tmp` lets any local user pre-create the path. They can
    poison the URL the banner shows, or point it at a file the hook's own user
    can write. Neither needs to guess much: the key is derived from a branch
    name and a commit sha.
    """

    def test_the_root_is_not_world_writable(self):
        root = common.state_root()
        assert root is not None
        self.assertEqual(root.stat().st_mode & 0o077, 0, f"{root} is group/other accessible")

    def test_the_root_is_owned_by_this_user(self):
        root = common.state_root()
        assert root is not None
        self.assertEqual(root.stat().st_uid, os.getuid())

    def test_a_write_does_not_follow_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "victim"
            link = Path(tmp) / "entry"
            link.symlink_to(target)
            common.write_pr_cache(link, common.PullRequest(url=PR_URL, state="OPEN"))
            self.assertFalse(target.exists(), "write followed the symlink to its target")

    def test_a_read_does_not_follow_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            planted = Path(tmp) / "planted"
            planted.write_text("OPEN\thttps://evil.example/pull/1")
            link = Path(tmp) / "entry"
            link.symlink_to(planted)
            self.assertIsNone(common.read_pr_cache(link, now=time.time()))

class TestStateRootFailsQuietly(unittest.TestCase):
    """An unwritable state directory costs a cache, never a banner.

    Both hooks call `state_root()` before they can print anything. If it
    raises, the harness gets a traceback after every turn end. The link this
    plugin exists to show then never appears.
    """

    def unwritable_root(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        blocked = Path(scratch.name) / "blocked"
        blocked.mkdir(mode=0o500)
        self.addCleanup(blocked.chmod, 0o700)
        return blocked / "state"

    def test_an_unwritable_root_returns_none(self):
        prior = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(self.unwritable_root())
        self.addCleanup(os.environ.__setitem__, "XDG_STATE_HOME", prior or "")
        self.assertIsNone(common.state_root())

    def test_a_writable_root_is_returned(self):
        self.assertIsNotNone(common.state_root())


if __name__ == "__main__":
    unittest.main()
