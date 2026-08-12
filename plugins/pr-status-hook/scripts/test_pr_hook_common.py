#!/usr/bin/env python3
"""Tests for pr_hook_common.py.

This module holds what the two hooks must not disagree on, so a bug here breaks
both at once. The `OPEN` filter gets the most attention. It is the rule that
used to live twice, and getting it wrong reports a merged PR as live.

Stdlib-only, so no pytest is needed. Unittest discovery works too.

    python3 plugins/pr-status-hook/scripts/test_pr_hook_common.py
"""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Dict, Optional, Tuple

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


class TestOpenPrUrl(unittest.TestCase):
    def test_returns_the_url_gh_reports(self):
        self.assertEqual(common.open_pr_url(runner({PR_VIEW: PR_URL})), PR_URL)

    def test_no_pr_returns_none(self):
        self.assertIsNone(common.open_pr_url(runner({})))

    def test_non_url_output_is_rejected(self):
        self.assertIsNone(common.open_pr_url(runner({PR_VIEW: "no pull requests found"})))

    def test_the_query_filters_on_open_state(self):
        """A closed PR must never be reported as live.

        `gh pr view` reports the branch's most recent PR whatever its state.
        So the state filter has to live in the query itself.
        """
        seen = []

        def run(argv):
            seen.append(list(argv))
            return None

        common.open_pr_url(run)
        self.assertIn("url,state", seen[0])
        self.assertIn('select(.state == "OPEN") | .url', seen[0])


class TestMakeRunner(unittest.TestCase):
    """The runner is the only place these hooks touch the outside world."""

    def test_returns_trimmed_stdout(self):
        run = common.make_runner("", common.GIT_TIMEOUT_SECONDS)
        self.assertEqual(run(["echo", "  hello  "]), "hello")

    def test_a_failing_command_is_none(self):
        run = common.make_runner("", common.GIT_TIMEOUT_SECONDS)
        self.assertIsNone(run(["false"]))

    def test_a_missing_binary_is_none_not_an_exception(self):
        run = common.make_runner("", common.GIT_TIMEOUT_SECONDS)
        self.assertIsNone(run(["definitely-not-a-real-binary-9f3a"]))

    def test_empty_output_is_none(self):
        run = common.make_runner("", common.GIT_TIMEOUT_SECONDS)
        self.assertIsNone(run(["true"]))

    def test_it_runs_in_the_directory_it_was_given(self):
        run = common.make_runner("/", common.GIT_TIMEOUT_SECONDS)
        self.assertEqual(run(["pwd"]), "/")


class TestEmit(unittest.TestCase):
    """One banner, two harnesses, two channels."""

    def capture(self, event_name, cursor_event):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            common.emit("PR: x", event_name, cursor_event)
        return out.getvalue(), err.getvalue()

    def test_claude_gets_json_on_stdout(self):
        out, err = self.capture("Stop", common.CURSOR_STOP_EVENT)
        self.assertEqual(json.loads(out)["systemMessage"], "PR: x")
        self.assertEqual(err, "")

    def test_cursor_gets_plain_text_on_stderr(self):
        out, err = self.capture(common.CURSOR_STOP_EVENT, common.CURSOR_STOP_EVENT)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "PR: x")

    def test_the_json_stays_out_of_the_transcript(self):
        """Without suppressOutput the hook's own JSON prints after every call."""
        out, _ = self.capture("PostToolUse", common.CURSOR_POST_TOOL_EVENT)
        self.assertTrue(json.loads(out)["suppressOutput"])

    def test_each_hook_matches_only_its_own_cursor_event(self):
        """A Stop payload must not take the PostToolUse hook's Cursor branch."""
        out, err = self.capture(common.CURSOR_STOP_EVENT, common.CURSOR_POST_TOOL_EVENT)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out)["systemMessage"], "PR: x")


if __name__ == "__main__":
    unittest.main()
