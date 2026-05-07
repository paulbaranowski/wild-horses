#!/usr/bin/env python3
"""Smoke tests for task_list_cli.py.

Stdlib-only — no pytest needed. Run from anywhere:

    python3 plugins/harness/skills/task-list-runner/test_task_list_cli.py

Or via unittest discovery:

    python3 -m unittest discover -s plugins/harness/skills/task-list-runner -p 'test_task_list_cli.py'

Tests invoke the CLI as a subprocess so exit codes, argparse behaviour,
and stdout/stderr separation are exercised exactly as a dispatched
agent would see them.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).parent / "task_list_cli.py"


def fixture_data() -> dict:
    return {
        "plan": "docs/exec-plans/active/test.md",
        "verifySteps": [
            {"name": "tests", "command": "echo test"},
        ],
        "scope": ["src/foo.py"],
        "tasks": [
            {
                "id": 1,
                "title": "First task",
                "what": "Do thing one",
                "resolves": ["src/foo.py:10"],
                "effort": "low",
                "createsNewCode": True,
                "status": "pending",
                "acceptanceCriteria": ["it works"],
                "log": None,
            },
            {
                "id": 2,
                "title": 'Task with "quotes" and unicode 日本語',
                "what": "tricky\nfield",
                "resolves": [],
                "effort": "medium",
                "createsNewCode": False,
                "status": "in-progress",
                "acceptanceCriteria": [],
                "log": None,
            },
            {
                "id": 3,
                "title": "Already done",
                "what": "x",
                "resolves": [],
                "effort": "low",
                "createsNewCode": False,
                "status": "complete",
                "acceptanceCriteria": [],
                "log": "all good",
            },
        ],
    }


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.task_path = self.tmp_dir / "tasks.json"
        self.task_path.write_text(json.dumps(fixture_data(), indent=2), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), "--file", str(self.task_path), *args],
            capture_output=True,
            text=True,
        )

    def read_task_file(self) -> dict:
        return json.loads(self.task_path.read_text(encoding="utf-8"))

    # ---- load_and_validate (exercised via `status`) -------------------
    # `validate` was removed in v5.0.0; `load_and_validate` runs as a
    # precondition for every subcommand, so we route these through
    # `status` (cheapest read) to confirm the schema-check contract.

    def test_status_missing_file_exits_one(self):
        result = subprocess.run(
            [sys.executable, str(CLI), "--file", str(self.tmp_dir / "nope.json"), "status"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file", result.stderr)

    def test_status_malformed_json_exits_thirteen(self):
        self.task_path.write_text('{"tasks": [},', encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 13, result.stderr)
        self.assertIn("not valid JSON", result.stderr)

    def test_status_missing_verify_steps_exits_twelve(self):
        data = fixture_data()
        del data["verifySteps"]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12)
        self.assertIn("verifySteps", result.stderr)

    def test_status_empty_verify_steps_exits_twelve(self):
        data = fixture_data()
        data["verifySteps"] = []
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12)
        self.assertIn("at least one step", result.stderr)

    def test_status_old_test_command_field_rejected_with_migration_hint(self):
        # Old (pre-v4) shape: a single testCommand string. Validator must reject
        # it AND tell the user how to migrate, not just say "verifySteps missing".
        data = fixture_data()
        del data["verifySteps"]
        data["testCommand"] = "echo test"
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12)
        self.assertIn("testCommand", result.stderr)
        self.assertIn("verifySteps", result.stderr)
        self.assertIn("migrate", result.stderr)

    def test_status_step_missing_name_exits_twelve(self):
        data = fixture_data()
        data["verifySteps"] = [{"command": "echo hi"}]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12)
        self.assertIn("name", result.stderr)

    def test_status_step_missing_command_exits_twelve(self):
        data = fixture_data()
        data["verifySteps"] = [{"name": "tests"}]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12)
        self.assertIn("command", result.stderr)

    def test_status_step_empty_name_exits_twelve(self):
        data = fixture_data()
        data["verifySteps"] = [{"name": "", "command": "echo hi"}]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12)
        self.assertIn("non-empty", result.stderr)

    def test_status_duplicate_task_ids_exits_twelve(self):
        data = fixture_data()
        data["tasks"][1]["id"] = 1
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12)
        self.assertIn("duplicate", result.stderr)

    def test_status_invalid_status_exits_twelve(self):
        data = fixture_data()
        data["tasks"][0]["status"] = "skipped"
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12)
        self.assertIn("status", result.stderr)

    # ---- list ----------------------------------------------------------

    def test_list_no_filter_returns_all(self):
        result = self.run_cli("list")
        self.assertEqual(result.returncode, 0)
        tasks = json.loads(result.stdout)
        self.assertEqual([t["id"] for t in tasks], [1, 2, 3])

    def test_list_status_filters_exactly(self):
        result = self.run_cli("list", "--status", "complete")
        self.assertEqual(result.returncode, 0)
        tasks = json.loads(result.stdout)
        self.assertEqual([t["id"] for t in tasks], [3])

    # ---- get -----------------------------------------------------------

    def test_get_existing_id_prints_object(self):
        result = self.run_cli("get", "--id", "2")
        self.assertEqual(result.returncode, 0)
        task = json.loads(result.stdout)
        self.assertEqual(task["id"], 2)
        self.assertIn("日本語", task["title"])

    def test_get_missing_id_exits_ten_with_empty_stdout(self):
        result = self.run_cli("get", "--id", "999")
        self.assertEqual(result.returncode, 10)
        self.assertEqual(result.stdout, "", "stdout must be empty so jq cannot silently succeed")
        self.assertIn("not found", result.stderr)

    # ---- verify (executor) --------------------------------------------

    def test_verify_runs_passing_steps_and_exits_zero(self):
        data = fixture_data()
        data["verifySteps"] = [
            {"name": "first", "command": "echo first-out; echo first-err 1>&2; true"},
            {"name": "second", "command": "true"},
        ]
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("verify", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "verify[1/2] first exit=0 log=/tmp/verify-1-step1-first.log", result.stdout
        )
        self.assertIn(
            "verify[2/2] second exit=0 log=/tmp/verify-1-step2-second.log", result.stdout
        )
        self.assertIn("verify: all 2 steps passed", result.stdout)
        log1 = Path("/tmp/verify-1-step1-first.log").read_text(encoding="utf-8")
        # Both stdout and stderr must land in the same log (stderr=STDOUT)
        self.assertIn("first-out", log1)
        self.assertIn("first-err", log1)

    def test_verify_stops_on_first_failure_with_correct_exit_code(self):
        data = fixture_data()
        data["verifySteps"] = [
            {"name": "ok", "command": "true"},
            {"name": "boom", "command": "echo about-to-fail; exit 7"},
            {"name": "skipped", "command": "echo SHOULD-NOT-RUN"},
        ]
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # Pre-clean any matching log files from previous test runs so the
        # "step 3 never ran" assertion can't false-pass on a leftover.
        for stale in Path("/tmp").glob("verify-1-step*.log"):
            stale.unlink()
        result = self.run_cli("verify", "--id", "1")
        self.assertEqual(result.returncode, 7, "exit code must be the failing step's code")
        self.assertIn(
            "verify[2/3] boom exit=7 log=/tmp/verify-1-step2-boom.log", result.stdout
        )
        self.assertNotIn("all 3 steps passed", result.stdout)
        log2 = Path("/tmp/verify-1-step2-boom.log").read_text(encoding="utf-8")
        self.assertIn("about-to-fail", log2)
        self.assertFalse(
            Path("/tmp/verify-1-step3-skipped.log").exists(),
            "step 3 must be skipped after step 2 fails",
        )

    def test_verify_missing_id_exits_ten(self):
        result = self.run_cli("verify", "--id", "999")
        self.assertEqual(result.returncode, 10)
        self.assertEqual(result.stdout, "", "stdout must be empty so jq cannot silently succeed")
        self.assertIn("not found", result.stderr)

    def test_verify_slugifies_special_chars_in_step_name(self):
        # Step names with spaces, slashes, unicode etc. get reduced to
        # ASCII-alnum + hyphens for safe use in log paths. The agent's
        # "the failing log is at /tmp/verify-<id>-stepN-<slug>.log"
        # mental model relies on this being predictable.
        data = fixture_data()
        data["verifySteps"] = [{"name": "Tests / Type-check 日本語", "command": "true"}]
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("verify", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("log=/tmp/verify-1-step1-tests-type-check.log", result.stdout)

    # ---- status --------------------------------------------------------

    def test_status_returns_counts_and_plan(self):
        # Fixture: 1 pending, 1 in-progress, 1 complete, 0 failed → total 3
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["in_progress"], 1)
        self.assertEqual(summary["complete"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["plan"], "docs/exec-plans/active/test.md")
        # `verifySteps` is intentionally NOT in the status payload — agents
        # use `verify` to access it, so embedding it in `status` would just
        # tempt them to bypass that subcommand.
        self.assertNotIn("verifySteps", summary)

    def test_status_remaining_is_precomputed_integer(self):
        # `status.remaining` is the halt-gate's one number — pending +
        # in_progress, computed by the CLI so the agent reads it without
        # any addition. The full task array lives behind the `remaining`
        # subcommand so a 30–50-task file doesn't pay an O(N) payload on
        # every loop iteration.
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["remaining"], 2)
        self.assertEqual(summary["remaining"], summary["pending"] + summary["in_progress"])

    def test_status_no_remaining_array_field_leaks_through(self):
        # Guard against accidental reintroduction of the heavy array
        # under the `remaining` key (or a renamed variant). `remaining`
        # must be the integer; the array shape only appears under the
        # separate `remaining` subcommand.
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertIsInstance(summary["remaining"], int)

    def test_remaining_returns_pending_and_in_progress_in_source_order(self):
        # The new subcommand replaces what `status.remaining` used to
        # carry. Same compact projection (id/title/effort/status), same
        # filter (pending + in-progress), same source order.
        result = self.run_cli("remaining")
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = json.loads(result.stdout)
        self.assertEqual(
            entries,
            [
                {"id": 1, "title": "First task", "effort": "low", "status": "pending"},
                {
                    "id": 2,
                    "title": 'Task with "quotes" and unicode 日本語',
                    "effort": "medium",
                    "status": "in-progress",
                },
            ],
        )

    def test_remaining_entries_are_compact_only(self):
        # Each entry must expose ONLY the four display fields — no full-task
        # leakage (no `what`, `resolves`, `acceptanceCriteria`, `log`, etc.).
        result = self.run_cli("remaining")
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = json.loads(result.stdout)
        self.assertGreater(len(entries), 0, "fixture has remaining tasks")
        for entry in entries:
            self.assertEqual(
                set(entry.keys()),
                {"id", "title", "effort", "status"},
                f"remaining entry must be compact; got keys {sorted(entry.keys())}",
            )

    def test_remaining_empty_when_all_complete(self):
        data = fixture_data()
        for t in data["tasks"]:
            t["status"] = "complete"
            t["log"] = "done"
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("remaining")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_status_remaining_count_matches_remaining_command_length(self):
        # The count and the list are derived from the same constant
        # (NON_TERMINAL_STATUSES) — this test pins them together so a
        # future change to one definition can't silently make them
        # disagree.
        status_result = self.run_cli("status")
        remaining_result = self.run_cli("remaining")
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        self.assertEqual(remaining_result.returncode, 0, remaining_result.stderr)
        summary = json.loads(status_result.stdout)
        entries = json.loads(remaining_result.stdout)
        self.assertEqual(summary["remaining"], len(entries))

    def test_status_with_no_tasks(self):
        data = fixture_data()
        data["tasks"] = []
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["pending"], 0)
        self.assertEqual(summary["complete"], 0)

    def test_status_with_missing_plan_field_returns_null(self):
        data = fixture_data()
        del data["plan"]
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertIsNone(summary["plan"])

    # ---- next ----------------------------------------------------------

    def test_next_returns_in_progress_unchanged_when_present(self):
        # Fixture has task 2 already in-progress — resume preference must
        # return it as-is, NOT flip task 1 (which is pending and earlier).
        result = self.run_cli("next")
        self.assertEqual(result.returncode, 0, result.stderr)
        task = json.loads(result.stdout)
        self.assertEqual(task["id"], 2)
        self.assertEqual(task["status"], "in-progress")
        # Task 1 must still be pending — no claim happened
        self.assertEqual(self.read_task_file()["tasks"][0]["status"], "pending")

    def test_next_claims_pending_when_no_in_progress(self):
        # Mutate fixture: drop task 2 to pending so only pending tasks remain
        data = fixture_data()
        data["tasks"][1]["status"] = "pending"
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("next")
        self.assertEqual(result.returncode, 0, result.stderr)
        task = json.loads(result.stdout)
        self.assertEqual(task["id"], 1)
        self.assertEqual(task["status"], "in-progress")
        # Persisted to disk
        self.assertEqual(self.read_task_file()["tasks"][0]["status"], "in-progress")
        # Task 2 untouched
        self.assertEqual(self.read_task_file()["tasks"][1]["status"], "pending")

    def test_next_no_remaining_tasks_exits_fourteen(self):
        # Mark all tasks complete
        data = fixture_data()
        for t in data["tasks"]:
            t["status"] = "complete"
            t["log"] = "done"
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("next")
        self.assertEqual(result.returncode, 14)
        self.assertEqual(result.stdout, "", "stdout must be empty so jq cannot silently succeed")
        self.assertIn("no remaining tasks", result.stderr)

    def test_next_skips_failed_tasks(self):
        data = fixture_data()
        data["tasks"][0]["status"] = "failed"
        data["tasks"][0]["log"] = "broke"
        data["tasks"][1]["status"] = "complete"
        data["tasks"][1]["log"] = "done"
        # Add a fresh pending task at id 4
        data["tasks"].append(
            {
                "id": 4,
                "title": "fresh",
                "what": "x",
                "resolves": [],
                "effort": "low",
                "createsNewCode": False,
                "status": "pending",
                "acceptanceCriteria": [],
                "log": None,
            }
        )
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("next")
        self.assertEqual(result.returncode, 0, result.stderr)
        task = json.loads(result.stdout)
        self.assertEqual(task["id"], 4)
        self.assertEqual(task["status"], "in-progress")

    def test_next_finish_round_trip(self):
        # Resume task 2 via next, finish it, next picks up task 1
        log = self._write_log("done")
        first = self.run_cli("next")
        self.assertEqual(json.loads(first.stdout)["id"], 2)
        finish = self.run_cli(
            "finish", "--id", "2", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(finish.returncode, 0, finish.stderr)
        second = self.run_cli("next")
        self.assertEqual(second.returncode, 0)
        second_task = json.loads(second.stdout)
        self.assertEqual(second_task["id"], 1)
        self.assertEqual(second_task["status"], "in-progress")

    # ---- start ---------------------------------------------------------

    def test_start_pending_flips_to_in_progress(self):
        result = self.run_cli("start", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.read_task_file()["tasks"][0]["status"], "in-progress")

    def test_start_already_in_progress_exits_eleven(self):
        result = self.run_cli("start", "--id", "2")
        self.assertEqual(result.returncode, 11)
        self.assertIn('current status is "in-progress"', result.stderr)

    def test_start_already_complete_exits_eleven(self):
        result = self.run_cli("start", "--id", "3")
        self.assertEqual(result.returncode, 11)
        self.assertIn('current status is "complete"', result.stderr)

    def test_start_missing_id_exits_ten(self):
        result = self.run_cli("start", "--id", "999")
        self.assertEqual(result.returncode, 10)

    # ---- finish --------------------------------------------------------

    def _write_log(self, content: str) -> Path:
        log = self.tmp_dir / "log.txt"
        log.write_text(content, encoding="utf-8")
        return log

    def test_finish_in_progress_to_complete(self):
        log = self._write_log("done\nthings\n")
        result = self.run_cli(
            "finish", "--id", "2", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        task = self.read_task_file()["tasks"][1]
        self.assertEqual(task["status"], "complete")
        self.assertEqual(task["log"], "done\nthings")  # one trailing \n stripped

    def test_finish_preserves_quotes_and_unicode(self):
        payload = 'embedded "quotes", unicode 漢字, and {"json": "looking"}'
        log = self._write_log(payload)
        result = self.run_cli(
            "finish", "--id", "2", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.read_task_file()["tasks"][1]["log"], payload)

    def test_finish_strips_only_one_trailing_newline(self):
        log = self._write_log("line\n\n")
        result = self.run_cli(
            "finish", "--id", "2", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.read_task_file()["tasks"][1]["log"], "line\n")

    def test_finish_pending_task_exits_eleven(self):
        log = self._write_log("x")
        result = self.run_cli(
            "finish", "--id", "1", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 11)
        self.assertIn('current status is "pending"', result.stderr)

    def test_finish_with_failed_status(self):
        log = self._write_log("broke")
        result = self.run_cli(
            "finish", "--id", "2", "--status", "failed", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.read_task_file()["tasks"][1]["status"], "failed")

    def test_finish_rejects_non_terminal_status(self):
        log = self._write_log("x")
        result = self.run_cli(
            "finish", "--id", "2", "--status", "in-progress", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_finish_non_utf8_log_file_exits_thirteen(self):
        # Non-UTF-8 bytes (e.g. latin-1 0xff) must map to a controlled
        # CLI error, not a Python UnicodeDecodeError traceback.
        log = self.tmp_dir / "log.bin"
        log.write_bytes(b"\xff\xfe\xfd not utf-8 here")
        result = self.run_cli(
            "finish", "--id", "2", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 13)
        self.assertIn("not valid UTF-8", result.stderr)

    def test_status_non_utf8_file_exits_thirteen(self):
        self.task_path.write_bytes(b"\xff\xfe\xfd not utf-8 here")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 13)
        self.assertIn("not valid UTF-8", result.stderr)

    def test_finish_missing_log_file_exits_one(self):
        result = self.run_cli(
            "finish",
            "--id",
            "2",
            "--status",
            "complete",
            "--log-file",
            str(self.tmp_dir / "absent.txt"),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file", result.stderr)

    # ---- atomicity -----------------------------------------------------

    def test_successful_write_leaves_no_tmp_file(self):
        self.run_cli("start", "--id", "1")
        leftovers = list(self.tmp_dir.glob("*.tmp"))
        self.assertEqual(
            leftovers,
            [],
            f".tmp file(s) must be cleaned up after successful os.replace; found {leftovers}",
        )

    def test_pretty_printed_indent_two(self):
        self.run_cli("start", "--id", "1")
        contents = self.task_path.read_text(encoding="utf-8")
        # tasks array should be indented with 2 spaces
        self.assertIn('\n  "tasks": [', contents)

    # ---- help-on-error -------------------------------------------------
    # Bad/missing args should print the full help (including subcommand
    # names) to stderr, not just `usage:` + a one-line error. Helps both
    # humans and dispatched agents discover the available verbs when
    # they mistype.

    SUBCOMMAND_NAMES = (
        "start",
        "finish",
        "get",
        "next",
        "status",
        "remaining",
        "verify",
        "list",
    )

    def test_no_args_at_all_prints_full_help_to_stderr(self):
        result = subprocess.run(
            [sys.executable, str(CLI)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        for verb in self.SUBCOMMAND_NAMES:
            self.assertIn(verb, result.stderr, f"help output should mention `{verb}`")

    def test_no_subcommand_prints_full_help_to_stderr(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        for verb in self.SUBCOMMAND_NAMES:
            self.assertIn(verb, result.stderr, f"help output should mention `{verb}`")

    def test_unknown_subcommand_prints_full_help_to_stderr(self):
        result = self.run_cli("bogus")
        self.assertEqual(result.returncode, 2)
        for verb in self.SUBCOMMAND_NAMES:
            self.assertIn(verb, result.stderr, f"help output should mention `{verb}`")
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
