#!/usr/bin/env python3
"""Tests for pr_churn_cli.py.

Stdlib-only - no pytest needed. Run from anywhere:

    python3 plugins/wild-pr/scripts/test_pr_churn_cli.py

fixtures/churn_pr.json is a synthetic GraphQL pullRequest node: six commits,
three posted reviews (one empty), five threads (one all-sentinel, one anchored
to a force-pushed commit), and three conversation comments (one blank).
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
CLI = HERE / "pr_churn_cli.py"

sys.path.insert(0, str(HERE))
import pr_churn_cli as cli  # noqa: E402


def oid(n):
    return f"a{n:039d}"


COMMIT_FILES = {
    oid(1): ["app/sync.py"],
    oid(2): ["app/sync.py", "tests/test_sync.py"],
    oid(3): ["app/sync.py", "tests/test_sync.py"],
    oid(4): ["app/sync.py"],
    oid(5): ["app/sync.py", "tests/test_sync.py"],
    oid(6): ["docs/sync.md"],
}


def load_pr():
    return json.loads((FIXTURES / "churn_pr.json").read_text())


def timeline():
    return cli.build_timeline(load_pr(), COMMIT_FILES)


def by_url(tl, suffix):
    return next(f for f in tl["findings"] if f["url"].endswith(suffix))


class TestRounds(unittest.TestCase):
    def test_each_reviewed_commit_opens_one_round_in_commit_order(self):
        rounds = timeline()["rounds"]
        self.assertEqual([r["reviewed_commit"] for r in rounds],
                         [oid(2), oid(4), oid(5)])
        self.assertEqual([len(r["commits_since_previous"]) for r in rounds], [2, 2, 1])

    def test_conversation_comment_joins_latest_round_without_opening_one(self):
        tl = timeline()
        self.assertEqual(len(tl["rounds"]), 3)  # the notice on commit 6 adds no round
        self.assertEqual(by_url(tl, "#c1")["round"], 3)
        self.assertEqual(by_url(tl, "#c2")["round"], 2)

    def test_force_pushed_commit_falls_back_to_the_commit_at_that_time(self):
        finding = by_url(timeline(), "#t4")
        self.assertEqual(finding["reviewed_commit"], oid(5))
        self.assertEqual(finding["round"], 3)


class TestFindings(unittest.TestCase):
    def test_sentinel_only_thread_blank_comment_and_empty_review_are_dropped(self):
        urls = {f["url"].rsplit("#", 1)[1] for f in timeline()["findings"]}
        self.assertEqual(urls, {"t1", "t2", "t3", "t4", "r1", "r3", "c1", "c2"})

    def test_sentinel_reply_is_not_kept_as_a_reply(self):
        self.assertEqual(by_url(timeline(), "#t1")["replies"], [])

    def test_ids_follow_creation_order(self):
        findings = timeline()["findings"]
        self.assertEqual([f["id"] for f in findings], [f"F{i + 1}" for i in range(len(findings))])
        self.assertEqual(findings, sorted(findings, key=lambda f: f["created_at"]))

    def test_bot_labels_from_each_reviewer_format(self):
        tl = timeline()
        self.assertEqual(by_url(tl, "#t1")["label"], "major")
        self.assertEqual(by_url(tl, "#t2")["label"], "medium")
        self.assertEqual(by_url(tl, "#t3")["label"], "p1")
        self.assertEqual(by_url(tl, "#t4")["label"], "nitpick")

    def test_titles_skip_markup_badges_and_severity_lines(self):
        tl = timeline()
        self.assertEqual(by_url(tl, "#t1")["title"], "Pause and resume can race.")
        self.assertEqual(by_url(tl, "#t2")["title"], "Delete races resume")
        self.assertEqual(by_url(tl, "#t3")["title"], "Revoke reads a stale row")

    def test_truncated_connections_are_reported(self):
        self.assertEqual(timeline()["truncated"], ["comments"])

    def test_truncated_thread_comments_are_reported(self):
        pr = load_pr()
        pr["reviewThreads"]["nodes"][1]["comments"]["pageInfo"] = {"hasNextPage": True}
        self.assertEqual(cli.build_timeline(pr, COMMIT_FILES)["truncated"],
                         ["comments", "threadComments"])


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.metrics = cli.compute_metrics(timeline())

    def test_findings_per_round_counts_threads_only(self):
        self.assertEqual(self.metrics["findings_per_round"], [1, 2, 1])
        self.assertEqual([r["review_bodies"] for r in self.metrics["per_round"]], [1, 1, 0])

    def test_fix_commits_start_after_first_review(self):
        commits = self.metrics["commits"]
        self.assertEqual(commits["after_first_review"], 4)
        self.assertEqual(commits["after_last_review"], 1)
        self.assertEqual(commits["by_type"], {"fix": 3, "docs": 1})

    def test_hot_files_need_three_fix_commits_or_three_findings(self):
        self.assertEqual(self.metrics["hot_files"],
                         [{"path": "app/sync.py", "fix_commits": 3, "findings": 3}])

    def test_unresolved_threads(self):
        self.assertEqual(self.metrics["findings"]["unresolved"], 3)

    def test_pr_with_no_reviews_has_no_rounds_or_fix_commits(self):
        pr = load_pr()
        for name in ("reviews", "reviewThreads", "comments"):
            pr[name]["nodes"] = []
        metrics = cli.compute_metrics(cli.build_timeline(pr, COMMIT_FILES))
        self.assertEqual(metrics["rounds"], 0)
        self.assertEqual(metrics["commits"]["after_first_review"], 0)
        self.assertEqual(metrics["commits"]["after_last_review"], 0)
        self.assertEqual(metrics["hot_files"], [])


class TestHelpers(unittest.TestCase):
    def test_commit_type(self):
        self.assertEqual(cli.commit_type("fix(sync): x"), "fix")
        self.assertEqual(cli.commit_type("feat!: x"), "feat")
        self.assertIsNone(cli.commit_type("Merge branch main"))

    def test_resolve_target_from_url_needs_no_gh(self):
        with mock.patch.object(cli, "gh_json", side_effect=AssertionError("no gh call")):
            self.assertEqual(cli.resolve_target("https://github.com/acme/app/pull/7"),
                             ("acme", "app", 7))

    def test_resolve_target_from_number_uses_current_repo(self):
        repo = {"owner": {"login": "acme"}, "name": "app"}
        with mock.patch.object(cli, "gh_json", return_value=repo):
            self.assertEqual(cli.resolve_target("#12"), ("acme", "app", 12))


class TestCollect(unittest.TestCase):
    def test_collect_writes_timeline_and_prints_metrics(self):
        pr = load_pr()

        def fake_gh(args):
            if args[:2] == ["api", "graphql"]:
                return {"data": {"repository": {"pullRequest": pr}}}
            if args[0] == "api" and "/commits/" in args[1]:
                return {"files": [{"filename": f} for f in COMMIT_FILES[args[1].rsplit("/", 1)[1]]]}
            raise AssertionError(f"unexpected gh call: {args}")

        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(cli, "gh_json", side_effect=fake_gh), \
                mock.patch.object(cli.shutil, "which", return_value="/usr/bin/gh"), \
                mock.patch("sys.stdout") as stdout:
            rc = cli.main(["collect", "https://github.com/acme/app/pull/7", "--out", out])
            printed = "".join(call.args[0] for call in stdout.write.call_args_list)
            saved = json.loads((Path(out) / "timeline.json").read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(printed)["findings_per_round"], [1, 2, 1])
        self.assertEqual(len(saved["findings"]), 8)


class TestCollectErrors(unittest.TestCase):
    def test_non_json_gh_output_becomes_collect_error(self):
        proc = subprocess.CompletedProcess([], 0, stdout="<html>", stderr="")
        with mock.patch.object(cli.subprocess, "run", return_value=proc):
            with self.assertRaisesRegex(cli.CollectError, "non-JSON"):
                cli.gh_json(["api", "graphql"])

    def test_unwritable_out_dir_prints_json_error(self):
        pr = load_pr()

        def fake_gh(args):
            if args[:2] == ["api", "graphql"]:
                return {"data": {"repository": {"pullRequest": pr}}}
            return {"files": []}

        with tempfile.NamedTemporaryFile() as blocker, \
                mock.patch.object(cli, "gh_json", side_effect=fake_gh), \
                mock.patch.object(cli.shutil, "which", return_value="/usr/bin/gh"), \
                mock.patch("sys.stdout") as stdout:
            # A regular file where the output directory should go.
            rc = cli.main(["collect", "https://github.com/acme/app/pull/7",
                           "--out", str(Path(blocker.name) / "run")])
            printed = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertEqual(rc, 1)
        self.assertIn("error", json.loads(printed))


class TestCliSurface(unittest.TestCase):
    def run_cli(self, args):
        return subprocess.run([sys.executable, str(CLI), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_no_subcommand_exits_2(self):
        self.assertEqual(self.run_cli([]).returncode, 2)

    def test_missing_out_exits_2(self):
        self.assertEqual(self.run_cli(["collect", "7"]).returncode, 2)

    def test_bad_pr_error_on_stdout(self):
        with tempfile.TemporaryDirectory() as out:
            proc = self.run_cli(["collect", "not-a-pr", "--out", out])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Invalid PR argument", json.loads(proc.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
