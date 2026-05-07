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

    # ---- validate ------------------------------------------------------

    def test_validate_good_file_exits_zero(self):
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 0, result.stderr)
        # fixture has 3 tasks: 1 pending, 1 in-progress, 1 complete; bucket
        # order is fixed (pending, in-progress, complete, failed) and zero
        # buckets are omitted.
        self.assertEqual(
            result.stdout,
            "valid: 3 tasks (1 pending, 1 in-progress, 1 complete)\n",
        )
        self.assertEqual(result.stderr, "")

    def test_validate_missing_file_exits_one(self):
        result = subprocess.run(
            [sys.executable, str(CLI), "--file", str(self.tmp_dir / "nope.json"), "validate"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file", result.stderr)

    def test_validate_malformed_json_exits_thirteen(self):
        self.task_path.write_text('{"tasks": [},', encoding="utf-8")
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 13, result.stderr)
        self.assertIn("not valid JSON", result.stderr)

    def test_validate_missing_verify_steps_exits_twelve(self):
        data = fixture_data()
        del data["verifySteps"]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 12)
        self.assertIn("verifySteps", result.stderr)

    def test_validate_empty_verify_steps_exits_twelve(self):
        data = fixture_data()
        data["verifySteps"] = []
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 12)
        self.assertIn("at least one step", result.stderr)

    def test_validate_old_test_command_field_rejected_with_migration_hint(self):
        # Old (pre-v4) shape: a single testCommand string. Validator must reject
        # it AND tell the user how to migrate, not just say "verifySteps missing".
        data = fixture_data()
        del data["verifySteps"]
        data["testCommand"] = "echo test"
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 12)
        self.assertIn("testCommand", result.stderr)
        self.assertIn("verifySteps", result.stderr)
        self.assertIn("migrate", result.stderr)

    def test_validate_step_missing_name_exits_twelve(self):
        data = fixture_data()
        data["verifySteps"] = [{"command": "echo hi"}]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 12)
        self.assertIn("name", result.stderr)

    def test_validate_step_missing_command_exits_twelve(self):
        data = fixture_data()
        data["verifySteps"] = [{"name": "tests"}]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 12)
        self.assertIn("command", result.stderr)

    def test_validate_step_empty_name_exits_twelve(self):
        data = fixture_data()
        data["verifySteps"] = [{"name": "", "command": "echo hi"}]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 12)
        self.assertIn("non-empty", result.stderr)

    def test_validate_duplicate_task_ids_exits_twelve(self):
        data = fixture_data()
        data["tasks"][1]["id"] = 1
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 12)
        self.assertIn("duplicate", result.stderr)

    def test_validate_invalid_status_exits_twelve(self):
        data = fixture_data()
        data["tasks"][0]["status"] = "skipped"
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 12)
        self.assertIn("status", result.stderr)

    def test_validate_zero_tasks_omits_parens(self):
        data = fixture_data()
        data["tasks"] = []
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("validate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "valid: 0 tasks\n")

    # ---- list ----------------------------------------------------------

    def test_list_no_filter_returns_all(self):
        result = self.run_cli("list")
        self.assertEqual(result.returncode, 0)
        tasks = json.loads(result.stdout)
        self.assertEqual([t["id"] for t in tasks], [1, 2, 3])

    def test_list_remaining_returns_pending_and_in_progress(self):
        result = self.run_cli("list", "--remaining")
        self.assertEqual(result.returncode, 0)
        tasks = json.loads(result.stdout)
        self.assertEqual({t["id"] for t in tasks}, {1, 2})

    def test_list_status_filters_exactly(self):
        result = self.run_cli("list", "--status", "complete")
        self.assertEqual(result.returncode, 0)
        tasks = json.loads(result.stdout)
        self.assertEqual([t["id"] for t in tasks], [3])

    def test_list_status_and_remaining_are_mutually_exclusive(self):
        result = self.run_cli("list", "--remaining", "--status", "pending")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not allowed with", result.stderr)

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

    # ---- status --------------------------------------------------------

    def test_status_returns_counts_plan_and_verify_steps(self):
        # Fixture: 1 pending, 1 in-progress, 1 complete, 0 failed → total 3
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["in-progress"], 1)
        self.assertEqual(summary["complete"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["plan"], "docs/exec-plans/active/test.md")
        self.assertEqual(
            summary["verifySteps"],
            [{"name": "tests", "command": "echo test"}],
        )

    def test_status_emits_full_verify_steps_array(self):
        # Multi-step plan: status output must show every step verbatim, not
        # collapse to "the first one" or "N steps". The agent reads this to
        # know exactly which commands it will run.
        data = fixture_data()
        data["verifySteps"] = [
            {"name": "typecheck", "command": "npx tsc --noEmit"},
            {"name": "tests", "command": "npm test"},
        ]
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(len(summary["verifySteps"]), 2)
        self.assertEqual(summary["verifySteps"][0]["name"], "typecheck")
        self.assertEqual(summary["verifySteps"][1]["command"], "npm test")

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

    def test_validate_non_utf8_file_exits_thirteen(self):
        self.task_path.write_bytes(b"\xff\xfe\xfd not utf-8 here")
        result = self.run_cli("validate")
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


if __name__ == "__main__":
    unittest.main()
