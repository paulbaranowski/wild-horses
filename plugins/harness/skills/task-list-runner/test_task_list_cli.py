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
import hashlib
import json
import os
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
                "agentValidations": ["it works"],
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
                "agentValidations": [],
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
                "agentValidations": [],
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
        # Mirror task_list_cli._staging_path: md5 of the resolved abs path,
        # 12 hex chars. Cleans up any drafted-state staging files this test
        # may have created so /tmp doesn't accumulate cruft across runs and
        # so set-status-failed tests (which intentionally leave the staging
        # file) don't leak into the next test's fixtures.
        digest = hashlib.md5(str(self.task_path.resolve()).encode("utf-8")).hexdigest()[:12]
        for stale in Path("/tmp").glob(f"harness-stage-{digest}-*.json"):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
        self._tmp.cleanup()

    def run_cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        # `cwd` only matters for `publish` (it shells out to git). Keeping
        # it default-None preserves the existing test behavior; publish
        # tests pass `cwd=self.tmp_dir` after `_init_git_repo()`.
        return subprocess.run(
            [sys.executable, str(CLI), "--file", str(self.task_path), *args],
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd is not None else None,
        )

    def run_cli_stdin(self, stdin: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), "--file", str(self.task_path), *args],
            input=stdin,
            capture_output=True,
            text=True,
        )

    def read_task_file(self) -> dict:
        return json.loads(self.task_path.read_text(encoding="utf-8"))

    # ---- helpers for drafted-state and publish tests ------------------

    def _staging_path_for(self, task_id: int) -> Path:
        """Mirror of task_list_cli._staging_path — same hash, same shape."""
        digest = hashlib.md5(str(self.task_path.resolve()).encode("utf-8")).hexdigest()[:12]
        return Path(f"/tmp/harness-stage-{digest}-{task_id}.json")

    def _make_drafted(self, task_id: int, commit_msg: str, log: str) -> Path:
        """Drive a task through `draft` and return its staging path.

        Used by tests that need a task in the `drafted` state without
        re-asserting `draft`'s own behavior. Goes through the real CLI so
        the staging file the test then inspects matches the production
        layout exactly. Caller must guarantee the named task is currently
        in-progress (the fixture's task 2 satisfies this).
        """
        result = self.run_cli_stdin(
            log,
            "draft",
            "--id",
            str(task_id),
            "--commit-msg",
            commit_msg,
            "--log-file",
            "-",
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"_make_drafted: `draft --id {task_id}` failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return self._staging_path_for(task_id)

    def _init_git_repo(self) -> None:
        """Initialize a self-contained git repo in self.tmp_dir.

        Publish runs `git diff --cached` and `git commit` in the CWD, so
        publish tests pass `cwd=self.tmp_dir` to `run_cli`. We need a real
        repo with at least one initial commit (so HEAD exists) and a local
        user.* config (so `git commit` doesn't probe the global one).
        Hooks are disabled via core.hooksPath=/dev/null to keep these
        tests independent of any pre-commit machinery the host may have.
        """
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        for cmd in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test User"],
            ["git", "config", "commit.gpgsign", "false"],
            ["git", "config", "core.hooksPath", "/dev/null"],
        ):
            result = subprocess.run(cmd, cwd=str(self.tmp_dir), env=env, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"_init_git_repo: `{' '.join(cmd)}` failed (exit {result.returncode}): "
                    f"{result.stderr.strip()}"
                )
        # Initial commit so HEAD exists; otherwise `git commit` on an
        # empty repo behaves differently from a normal mid-history commit.
        seed = self.tmp_dir / ".gitkeep"
        seed.write_text("seed\n", encoding="utf-8")
        for cmd in (
            ["git", "add", ".gitkeep"],
            ["git", "commit", "-q", "-m", "seed"],
        ):
            result = subprocess.run(cmd, cwd=str(self.tmp_dir), env=env, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"_init_git_repo: `{' '.join(cmd)}` failed (exit {result.returncode}): "
                    f"{result.stderr.strip()}"
                )

    def _stage_file(self, name: str, content: str) -> Path:
        """Create + `git add` a file in the test's git repo."""
        path = self.tmp_dir / name
        path.write_text(content, encoding="utf-8")
        result = subprocess.run(
            ["git", "add", name],
            cwd=str(self.tmp_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"_stage_file: `git add {name}` failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return path

    def _git_log_subjects(self) -> list[str]:
        """Return commit subjects in the test repo, newest first."""
        result = subprocess.run(
            ["git", "log", "--format=%s"],
            cwd=str(self.tmp_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"_git_log_subjects: `git log` failed: {result.stderr.strip()}"
            )
        return [line for line in result.stdout.splitlines() if line]

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

    # ---- per-task verifySteps shape validation ------------------------
    # The optional per-task `verifySteps` field shares the top-level
    # array's shape rules. The validator threads the offending task id
    # into every error message so a corrupt override is locatable in a
    # 30+ task file without diffing.

    def test_status_per_task_verify_steps_absent_passes(self):
        # The default — no override — is valid; the top-level array governs.
        data = fixture_data()
        for t in data["tasks"]:
            t.pop("verifySteps", None)
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_status_per_task_verify_steps_valid_passes(self):
        data = fixture_data()
        data["tasks"][0]["verifySteps"] = [
            {"name": "linkcheck", "command": "echo ok"}
        ]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_status_per_task_verify_steps_empty_exits_twelve(self):
        # Same rule as top-level — an empty array is rejected. Error
        # message must include the task id so a 30+ task file is searchable.
        data = fixture_data()
        data["tasks"][0]["verifySteps"] = []
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertIn("at least one step", result.stderr)
        self.assertIn("(id=1)", result.stderr)

    def test_status_per_task_verify_steps_not_array_exits_twelve(self):
        data = fixture_data()
        data["tasks"][0]["verifySteps"] = "not-an-array"
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertIn("must be an array", result.stderr)
        self.assertIn("(id=1)", result.stderr)

    def test_status_per_task_verify_step_missing_name_exits_twelve(self):
        data = fixture_data()
        data["tasks"][1]["verifySteps"] = [{"command": "echo hi"}]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertIn("name", result.stderr)
        self.assertIn("non-empty", result.stderr)
        self.assertIn("(id=2)", result.stderr)

    def test_status_per_task_verify_step_missing_command_exits_twelve(self):
        data = fixture_data()
        data["tasks"][1]["verifySteps"] = [{"name": "thing"}]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertIn("command", result.stderr)
        self.assertIn("non-empty", result.stderr)
        self.assertIn("(id=2)", result.stderr)

    def test_status_per_task_verify_step_empty_command_exits_twelve(self):
        data = fixture_data()
        data["tasks"][1]["verifySteps"] = [{"name": "thing", "command": ""}]
        self.task_path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertIn("command", result.stderr)
        self.assertIn("(id=2)", result.stderr)

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

    # ---- verify with per-task override --------------------------------
    # `verify --id N` resolves steps per task: task N's `verifySteps` if
    # declared (total replacement, no merge), else the top-level array.

    def test_verify_uses_per_task_override_when_present(self):
        data = fixture_data()
        # Top-level: a step that would FAIL if it accidentally ran.
        data["verifySteps"] = [{"name": "should-not-run", "command": "exit 99"}]
        # Per-task override on task 1: a step that succeeds.
        data["tasks"][0]["verifySteps"] = [
            {"name": "override-step", "command": "echo OVERRIDE-RAN"}
        ]
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # Pre-clean to avoid false positives from a previous run.
        for stale in Path("/tmp").glob("verify-1-step*.log"):
            stale.unlink()
        result = self.run_cli("verify", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "verify[1/1] override-step exit=0 log=/tmp/verify-1-step1-override-step.log",
            result.stdout,
        )
        self.assertIn("verify: all 1 step passed", result.stdout)
        log = Path("/tmp/verify-1-step1-override-step.log").read_text(encoding="utf-8")
        self.assertIn("OVERRIDE-RAN", log)
        # The top-level step's slug log must not exist for task 1.
        self.assertFalse(
            Path("/tmp/verify-1-step1-should-not-run.log").exists(),
            "top-level step must not run when task has an override",
        )

    def test_verify_falls_back_to_top_level_when_task_has_no_override(self):
        data = fixture_data()
        data["verifySteps"] = [{"name": "default-step", "command": "echo TOP-LEVEL-RAN"}]
        # Task 1 has no verifySteps key (default fixture state); task 2
        # has an override that must NOT run when verifying task 1.
        self.assertNotIn("verifySteps", data["tasks"][0])
        data["tasks"][1]["verifySteps"] = [
            {"name": "task-2-only", "command": "echo SHOULD-NOT-RUN-FOR-1"}
        ]
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        for stale in Path("/tmp").glob("verify-1-step*.log"):
            stale.unlink()
        result = self.run_cli("verify", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "verify[1/1] default-step exit=0 log=/tmp/verify-1-step1-default-step.log",
            result.stdout,
        )
        log = Path("/tmp/verify-1-step1-default-step.log").read_text(encoding="utf-8")
        self.assertIn("TOP-LEVEL-RAN", log)
        # Task 2's override must not have leaked into task 1's verification.
        self.assertFalse(
            Path("/tmp/verify-1-step1-task-2-only.log").exists(),
            "task 2's override must not run when verifying task 1",
        )

    def test_verify_per_task_override_runs_multi_step_in_order_and_fails_fast(self):
        # Per-task override is a full array; ordering and fail-fast still apply.
        data = fixture_data()
        data["verifySteps"] = [{"name": "should-not-run", "command": "exit 1"}]
        data["tasks"][0]["verifySteps"] = [
            {"name": "first", "command": "true"},
            {"name": "second-fails", "command": "exit 9"},
            {"name": "third-skipped", "command": "echo SHOULD-NOT-RUN"},
        ]
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        for stale in Path("/tmp").glob("verify-1-step*.log"):
            stale.unlink()
        result = self.run_cli("verify", "--id", "1")
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertIn(
            "verify[2/3] second-fails exit=9 log=/tmp/verify-1-step2-second-fails.log",
            result.stdout,
        )
        self.assertNotIn("all 3 steps passed", result.stdout)
        self.assertFalse(
            Path("/tmp/verify-1-step3-third-skipped.log").exists(),
            "step 3 must be skipped after step 2 fails",
        )

    # ---- status --------------------------------------------------------

    def test_status_returns_counts_and_plan(self):
        # Fixture: 1 pending, 1 in-progress, 0 drafted, 1 complete, 0 failed → total 3
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["in_progress"], 1)
        self.assertEqual(summary["drafted"], 0)
        self.assertEqual(summary["complete"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["plan"], "docs/exec-plans/active/test.md")
        # `verifySteps` is intentionally NOT in the status payload — agents
        # use `verify` to access it, so embedding it in `status` would just
        # tempt them to bypass that subcommand.
        self.assertNotIn("verifySteps", summary)

    def test_status_remaining_is_precomputed_integer(self):
        # `status.remaining` is the halt-gate's one number — pending +
        # in_progress + drafted, computed by the CLI so the agent reads it
        # without any addition. The full task array lives behind the
        # `remaining` subcommand so a 30–50-task file doesn't pay an O(N)
        # payload on every loop iteration.
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["remaining"], 2)
        self.assertEqual(
            summary["remaining"],
            summary["pending"] + summary["in_progress"] + summary["drafted"],
        )

    def test_status_drafted_count_included(self):
        # Drafted tasks must surface in both the count breakdown and the
        # `remaining` integer. The runner uses `remaining` as its halt gate;
        # a missing drafted count would make the loop think it's done while
        # a staging file is still parked.
        data = fixture_data()
        # Task 2 was in-progress in the fixture — flip it to drafted directly.
        # We can't go through `draft` here (would need `--commit-msg` etc.) but
        # the schema allows the literal string in tasks[].status.
        data["tasks"][1]["status"] = "drafted"
        data["tasks"][1]["log"] = "implementation log"
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["drafted"], 1)
        self.assertEqual(summary["in_progress"], 0)
        # Drafted is non-terminal, so it must still count as "remaining"
        self.assertEqual(summary["remaining"], 2)

    def test_status_no_remaining_array_field_leaks_through(self):
        # Guard against accidental reintroduction of the heavy array
        # under the `remaining` key (or a renamed variant). `remaining`
        # must be the integer; the array shape only appears under the
        # separate `remaining` subcommand.
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertIsInstance(summary["remaining"], int)

    def test_remaining_returns_non_terminal_in_source_order(self):
        # The new subcommand replaces what `status.remaining` used to
        # carry. Same compact projection (id/title/effort/status), same
        # filter (pending + in-progress + drafted), same source order.
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

    def test_remaining_includes_drafted(self):
        # Drafted tasks count as "remaining" — the runner still owes them a
        # publish-or-fail call. Pin this to the same NON_TERMINAL_STATUSES
        # set the count uses so the two views stay consistent.
        data = fixture_data()
        data["tasks"][1]["status"] = "drafted"
        data["tasks"][1]["log"] = "x"
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("remaining")
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = json.loads(result.stdout)
        statuses = [e["status"] for e in entries]
        self.assertIn("drafted", statuses)
        self.assertIn("pending", statuses)
        self.assertEqual(len(entries), 2)

    def test_remaining_entries_are_compact_only(self):
        # Each entry must expose ONLY the four display fields — no full-task
        # leakage (no `what`, `resolves`, `agentValidations`, `log`, etc.).
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
                "agentValidations": [],
                "log": None,
            }
        )
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = self.run_cli("next")
        self.assertEqual(result.returncode, 0, result.stderr)
        task = json.loads(result.stdout)
        self.assertEqual(task["id"], 4)
        self.assertEqual(task["status"], "in-progress")

    def test_next_set_status_round_trip(self):
        # Resume task 2 via next, terminate it via set-status, next picks up task 1.
        # The no-commit completion path (set-status complete from in-progress) is
        # the right round-trip target here: it exercises the "next pulls the next
        # pending after a terminal flip" loop without needing a real git repo.
        log = self._write_log("done")
        first = self.run_cli("next")
        self.assertEqual(json.loads(first.stdout)["id"], 2)
        flip = self.run_cli(
            "set-status", "--id", "2", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(flip.returncode, 0, flip.stderr)
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

    # ---- set-status ----------------------------------------------------
    # `set-status` replaces the old `finish` verb (v6.0.0 hard-rename) and
    # narrows the legal transitions: in-progress → complete | failed, and
    # drafted → failed. The drafted → complete success path is exclusively
    # `publish` so a task cannot reach `complete` without a git commit.

    def _write_log(self, content: str) -> Path:
        log = self.tmp_dir / "log.txt"
        log.write_text(content, encoding="utf-8")
        return log

    def test_set_status_in_progress_to_complete(self):
        log = self._write_log("done\nthings\n")
        result = self.run_cli(
            "set-status", "--id", "2", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        task = self.read_task_file()["tasks"][1]
        self.assertEqual(task["status"], "complete")
        self.assertEqual(task["log"], "done\nthings")  # one trailing \n stripped

    def test_set_status_preserves_quotes_and_unicode(self):
        payload = 'embedded "quotes", unicode 漢字, and {"json": "looking"}'
        log = self._write_log(payload)
        result = self.run_cli(
            "set-status", "--id", "2", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.read_task_file()["tasks"][1]["log"], payload)

    def test_set_status_strips_only_one_trailing_newline(self):
        log = self._write_log("line\n\n")
        result = self.run_cli(
            "set-status", "--id", "2", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.read_task_file()["tasks"][1]["log"], "line\n")

    def test_set_status_log_file_dash_reads_stdin(self):
        # `--log-file -` is the Unix convention for stdin. Lets the dispatched
        # agent pipe a heredoc directly without an intermediate /tmp file
        # (and the Write-tool classifier gating that comes with one).
        result = self.run_cli_stdin(
            "log content from stdin\nmultiple lines\n",
            "set-status", "--id", "2", "--status", "complete", "--log-file", "-",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        task = self.read_task_file()["tasks"][1]
        self.assertEqual(task["status"], "complete")
        self.assertEqual(task["log"], "log content from stdin\nmultiple lines")

    def test_set_status_log_file_dash_preserves_quotes_and_unicode(self):
        # Same safety property as --log-file <path>: stdin bytes are verbatim,
        # no shell-arg quoting hazard. The agent's heredoc payload arrives
        # untransformed.
        payload = 'embedded "quotes", unicode 漢字, and {"json": "looking"}'
        result = self.run_cli_stdin(
            payload,
            "set-status", "--id", "2", "--status", "complete", "--log-file", "-",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_task_file()["tasks"][1]["log"], payload)

    def test_set_status_log_file_dash_with_empty_stdin(self):
        # An empty heredoc yields an empty log — accepted (the "did the agent
        # actually do anything" check belongs to the runner's iteration count
        # delta, not the CLI's input validation).
        result = self.run_cli_stdin(
            "",
            "set-status", "--id", "2", "--status", "complete", "--log-file", "-",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_task_file()["tasks"][1]["log"], "")

    def test_set_status_pending_task_exits_eleven(self):
        log = self._write_log("x")
        result = self.run_cli(
            "set-status", "--id", "1", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 11)
        self.assertIn('current status is "pending"', result.stderr)

    def test_set_status_with_failed_status_from_in_progress(self):
        log = self._write_log("broke")
        result = self.run_cli(
            "set-status", "--id", "2", "--status", "failed", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.read_task_file()["tasks"][1]["status"], "failed")

    def test_set_status_drafted_to_failed_allowed(self):
        # Drafted → failed is the validation-rejected path. The staging file
        # is intentionally NOT removed (the implementer needs the evidence to
        # diagnose why the draft was rejected). See the next test for that.
        self._make_drafted(task_id=2, commit_msg="wip: subject", log="impl notes")
        log = self._write_log("validation rejected")
        result = self.run_cli(
            "set-status", "--id", "2", "--status", "failed", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        task = self.read_task_file()["tasks"][1]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["log"], "validation rejected")

    def test_set_status_drafted_to_failed_preserves_staging_file(self):
        # Per the documented design: failure path keeps the staging file so the
        # implementer has the parked commit subject for inspection. The runner
        # garbage-collects orphaned staging files separately if it ever needs to.
        self._make_drafted(task_id=2, commit_msg="wip: subject for inspection", log="x")
        staging = self._staging_path_for(task_id=2)
        self.assertTrue(staging.is_file(), "precondition: staging file exists post-draft")
        log = self._write_log("rejected")
        result = self.run_cli(
            "set-status", "--id", "2", "--status", "failed", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(
            staging.is_file(),
            "staging file must survive set-status failed so the implementer can inspect it",
        )

    def test_set_status_drafted_to_complete_rejected(self):
        # The drafted → complete success path is `publish` (which runs git commit).
        # `set-status complete` from drafted is forbidden so a task cannot reach
        # `complete` without a corresponding commit.
        self._make_drafted(task_id=2, commit_msg="wip", log="x")
        log = self._write_log("trying to skip publish")
        result = self.run_cli(
            "set-status", "--id", "2", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn("publish", result.stderr)
        # Task must remain drafted, log must remain unchanged
        task = self.read_task_file()["tasks"][1]
        self.assertEqual(task["status"], "drafted")
        self.assertEqual(task["log"], "x")

    def test_set_status_complete_task_rejected(self):
        # set-status only operates on in-progress and drafted; a `complete` task
        # is terminal and cannot be moved by this verb.
        log = self._write_log("x")
        result = self.run_cli(
            "set-status", "--id", "3", "--status", "failed", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 11)
        self.assertIn('current status is "complete"', result.stderr)

    def test_set_status_rejects_non_terminal_status(self):
        log = self._write_log("x")
        result = self.run_cli(
            "set-status", "--id", "2", "--status", "in-progress", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_set_status_non_utf8_log_file_exits_thirteen(self):
        # Non-UTF-8 bytes (e.g. latin-1 0xff) must map to a controlled
        # CLI error, not a Python UnicodeDecodeError traceback.
        log = self.tmp_dir / "log.bin"
        log.write_bytes(b"\xff\xfe\xfd not utf-8 here")
        result = self.run_cli(
            "set-status", "--id", "2", "--status", "complete", "--log-file", str(log)
        )
        self.assertEqual(result.returncode, 13)
        self.assertIn("not valid UTF-8", result.stderr)

    def test_status_non_utf8_file_exits_thirteen(self):
        self.task_path.write_bytes(b"\xff\xfe\xfd not utf-8 here")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 13)
        self.assertIn("not valid UTF-8", result.stderr)

    def test_set_status_missing_log_file_exits_one(self):
        result = self.run_cli(
            "set-status",
            "--id",
            "2",
            "--status",
            "complete",
            "--log-file",
            str(self.tmp_dir / "absent.txt"),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file", result.stderr)

    # ---- next blocks on drafted ---------------------------------------
    # Architectural invariant: a drafted task means the runner skipped the
    # publish-or-fail step (typically because of a crash). `next` must
    # refuse to claim new work while one is parked, so the runner can't
    # accumulate two non-terminal tasks competing for the same agent.

    def test_next_refuses_when_a_drafted_task_exists(self):
        # Drive task 2 to drafted, then ask `next` for the next claim.
        # Should exit 11 with a message naming both recovery paths so the
        # runner (or a human reading stderr) knows publish vs set-status.
        self._make_drafted(task_id=2, commit_msg="wip: x", log="impl notes")
        result = self.run_cli("next")
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn("drafted", result.stderr)
        self.assertIn("publish", result.stderr)
        self.assertIn("set-status", result.stderr)
        # No claim happened: task 1 must still be pending
        self.assertEqual(self.read_task_file()["tasks"][0]["status"], "pending")

    # ---- draft ---------------------------------------------------------
    # `draft` is the in-progress → drafted transition. It writes the log
    # into the task and parks the commit subject in a per-task /tmp
    # staging file. Does NOT touch git — `publish` is the only verb that
    # invokes git commit. The split exists so runner-level validation can
    # run between draft and publish, with set-status-failed as the rejection
    # exit. Source: task_list_cli.cmd_draft.

    def test_draft_in_progress_to_drafted_writes_staging_and_log(self):
        staging = self._make_drafted(
            task_id=2, commit_msg="feat: implement thing", log="impl notes line 1"
        )
        # Task state: drafted, with log persisted in the task file
        task = self.read_task_file()["tasks"][1]
        self.assertEqual(task["status"], "drafted")
        self.assertEqual(task["log"], "impl notes line 1")
        # Staging file present at the deterministic per-task path with the
        # commit subject parked (publish reads it from here later)
        self.assertTrue(staging.is_file(), f"staging file missing at {staging}")
        payload = json.loads(staging.read_text(encoding="utf-8"))
        self.assertEqual(payload["task_id"], 2)
        self.assertEqual(payload["commit_msg"], "feat: implement thing")
        self.assertEqual(payload["task_file"], str(self.task_path.resolve()))

    def test_draft_prints_staging_path(self):
        # The runner reads stdout to learn where the staging file landed
        # (used by error messages when publish is later asked to clean up).
        result = self.run_cli_stdin(
            "log",
            "draft", "--id", "2", "--commit-msg", "subject", "--log-file", "-",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        expected_staging = self._staging_path_for(2)
        self.assertIn(str(expected_staging), result.stdout)
        self.assertIn("drafted task 2", result.stdout)

    def test_draft_pending_task_rejected(self):
        # `draft` requires in-progress; the schema's pending → drafted
        # transition is forbidden (must go through `start` or `next` first).
        result = self.run_cli_stdin(
            "log",
            "draft", "--id", "1", "--commit-msg", "subject", "--log-file", "-",
        )
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn('current status is "pending"', result.stderr)
        # No staging file should have been written
        self.assertFalse(self._staging_path_for(1).exists())
        # Task 1 must still be pending
        self.assertEqual(self.read_task_file()["tasks"][0]["status"], "pending")

    def test_draft_complete_task_rejected(self):
        result = self.run_cli_stdin(
            "log",
            "draft", "--id", "3", "--commit-msg", "subject", "--log-file", "-",
        )
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn('current status is "complete"', result.stderr)
        self.assertFalse(self._staging_path_for(3).exists())

    def test_draft_already_drafted_rejected(self):
        # Drafted → drafted is not a valid transition (the staging file
        # would silently overwrite). cmd_draft requires in-progress source.
        self._make_drafted(task_id=2, commit_msg="first subject", log="x")
        result = self.run_cli_stdin(
            "log",
            "draft", "--id", "2", "--commit-msg", "second subject", "--log-file", "-",
        )
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn('current status is "drafted"', result.stderr)
        # First staging file's commit subject must be intact
        payload = json.loads(self._staging_path_for(2).read_text(encoding="utf-8"))
        self.assertEqual(payload["commit_msg"], "first subject")

    def test_draft_empty_commit_msg_rejected(self):
        # An empty/whitespace-only commit subject is a usage error (exit 2,
        # not 11) — git would reject it later anyway, and we want the
        # failure at the draft step where the human-readable error lives.
        result = self.run_cli_stdin(
            "log",
            "draft", "--id", "2", "--commit-msg", "   ", "--log-file", "-",
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("commit-msg", result.stderr)
        # Task must remain in-progress; staging file must not exist
        self.assertEqual(self.read_task_file()["tasks"][1]["status"], "in-progress")
        self.assertFalse(self._staging_path_for(2).exists())

    def test_draft_preserves_quotes_and_unicode_in_log(self):
        payload = 'embedded "quotes", unicode 漢字, and {"json": "looking"}'
        self._make_drafted(task_id=2, commit_msg="subject", log=payload)
        self.assertEqual(self.read_task_file()["tasks"][1]["log"], payload)

    def test_draft_missing_id_exits_ten(self):
        result = self.run_cli_stdin(
            "log",
            "draft", "--id", "999", "--commit-msg", "x", "--log-file", "-",
        )
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIn("not found", result.stderr)

    # ---- publish -------------------------------------------------------
    # `publish` is the drafted → complete transition. Reads the staging
    # file's commit subject, verifies the git index has at least one staged
    # file, runs `git commit`, then flips status. Order matters: commit
    # first, status second, so a commit failure leaves the task drafted
    # and re-runnable. Source: task_list_cli.cmd_publish.

    def test_publish_drafted_to_complete_runs_git_commit(self):
        self._init_git_repo()
        self._make_drafted(task_id=2, commit_msg="feat: real commit subject", log="x")
        self._stage_file("hello.txt", "world\n")
        result = self.run_cli("publish", "--id", "2", cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Task is now complete
        self.assertEqual(self.read_task_file()["tasks"][1]["status"], "complete")
        # Git history's newest subject is the staged subject
        self.assertEqual(self._git_log_subjects()[0], "feat: real commit subject")
        # Staging file has been removed on the success path
        self.assertFalse(self._staging_path_for(2).exists())
        # CLI announces what it did so the runner can log it
        self.assertIn("published task 2", result.stdout)

    def test_publish_in_progress_task_rejected(self):
        # publish must only run from drafted — running it on an
        # in-progress task means the agent skipped the draft step.
        self._init_git_repo()
        self._stage_file("a.txt", "1\n")
        result = self.run_cli("publish", "--id", "2", cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn('current status is "in-progress"', result.stderr)
        # Git history must be unchanged (only the seed commit)
        self.assertEqual(self._git_log_subjects(), ["seed"])

    def test_publish_pending_task_rejected(self):
        self._init_git_repo()
        result = self.run_cli("publish", "--id", "1", cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn('current status is "pending"', result.stderr)

    def test_publish_complete_task_rejected(self):
        self._init_git_repo()
        result = self.run_cli("publish", "--id", "3", cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn('current status is "complete"', result.stderr)

    def test_publish_missing_staging_file_exits_one(self):
        # Manually flip task 2 to drafted but skip the staging-file write.
        # publish must surface a clear "did you call draft?" error rather
        # than crashing with FileNotFoundError or silently committing the
        # index with a placeholder message.
        self._init_git_repo()
        data = fixture_data()
        data["tasks"][1]["status"] = "drafted"
        data["tasks"][1]["log"] = "synthetic"
        self.task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # Make sure no leftover staging file from a previous test run
        self._staging_path_for(2).unlink(missing_ok=True)
        self._stage_file("a.txt", "1\n")
        result = self.run_cli("publish", "--id", "2", cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("staging file", result.stderr)
        self.assertIn("draft", result.stderr)

    def test_publish_empty_git_index_exits_fifteen(self):
        # A drafted task with no `git add` output is the "implementer
        # forgot to stage" failure mode. publish should surface that
        # explicitly rather than letting git's "nothing to commit" message
        # confuse the runner.
        self._init_git_repo()
        self._make_drafted(task_id=2, commit_msg="will-not-commit", log="x")
        result = self.run_cli("publish", "--id", "2", cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 15, result.stderr)
        self.assertIn("no files staged", result.stderr)
        # Task must remain drafted; staging file must remain present
        self.assertEqual(self.read_task_file()["tasks"][1]["status"], "drafted")
        self.assertTrue(self._staging_path_for(2).is_file())
        # No commit happened
        self.assertEqual(self._git_log_subjects(), ["seed"])

    def test_publish_git_commit_failure_keeps_task_drafted(self):
        # Wire up a pre-commit hook that always fails. publish must
        # surface the hook's stderr verbatim and leave the task drafted
        # + staging file intact so the runner can fix the underlying
        # cause and re-run `publish --id N`.
        self._init_git_repo()
        # Override the /dev/null hooksPath set by _init_git_repo
        subprocess.run(
            ["git", "config", "--unset", "core.hooksPath"],
            cwd=str(self.tmp_dir), capture_output=True, text=True,
        )
        hooks_dir = self.tmp_dir / ".git" / "hooks"
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/sh\necho HOOK-REJECTED 1>&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        self._make_drafted(task_id=2, commit_msg="should-not-land", log="x")
        self._stage_file("a.txt", "1\n")
        result = self.run_cli("publish", "--id", "2", cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 15, result.stderr)
        self.assertIn("git commit failed", result.stderr)
        self.assertIn("HOOK-REJECTED", result.stderr)
        # Task stays drafted, staging file stays put — the runner can
        # disable the hook (or fix the underlying problem) and re-run.
        self.assertEqual(self.read_task_file()["tasks"][1]["status"], "drafted")
        self.assertTrue(self._staging_path_for(2).is_file())
        self.assertEqual(self._git_log_subjects(), ["seed"])

    def test_publish_missing_id_exits_ten(self):
        self._init_git_repo()
        result = self.run_cli("publish", "--id", "999", cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIn("not found", result.stderr)

    def test_publish_staging_file_invalid_utf8_exits_thirteen(self):
        # _staging_read opens with encoding="utf-8"; a corrupt staging
        # file that contains non-UTF-8 bytes must surface as a clean
        # TaskCliError (exit 13), matching the convention used by
        # load_and_validate (line 93) and _read_log_input (line 212).
        # Without the explicit handler, the decode error escapes the
        # try block and crashes the CLI with a raw traceback.
        self._init_git_repo()
        staging = self._make_drafted(task_id=2, commit_msg="ok", log="x")
        # Overwrite the valid staging file with bytes that fail UTF-8
        staging.write_bytes(b"\xff\xfe not utf-8 bytes \x80")
        result = self.run_cli("publish", "--id", "2", cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 13, result.stderr)
        self.assertIn("not valid UTF-8", result.stderr)
        # No commit, task remains drafted
        self.assertEqual(self.read_task_file()["tasks"][1]["status"], "drafted")
        self.assertEqual(self._git_log_subjects(), ["seed"])

    def test_publish_staging_task_id_mismatch_rejected(self):
        # Defense against a stale or hand-tampered staging file: even if
        # the per-task path scheme makes collision improbable, publishing
        # with the wrong task_id payload would commit work under the
        # wrong subject. The identity check refuses to proceed.
        self._init_git_repo()
        staging = self._make_drafted(task_id=2, commit_msg="real", log="x")
        # Hand-rewrite the staging payload with a mismatched task_id
        payload = json.loads(staging.read_text(encoding="utf-8"))
        payload["task_id"] = 999
        staging.write_text(json.dumps(payload), encoding="utf-8")
        self._stage_file("a.txt", "1\n")
        result = self.run_cli("publish", "--id", "2", cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("task_id mismatch", result.stderr)
        # Task stays drafted, no commit
        self.assertEqual(self.read_task_file()["tasks"][1]["status"], "drafted")
        self.assertEqual(self._git_log_subjects(), ["seed"])

    def test_publish_staging_task_file_mismatch_rejected(self):
        # Same defense, but for the task_file path. Catches the case
        # where the same task list lives at two paths (e.g., a moved
        # or renamed file) and publish is called against the new path
        # while the staging payload still references the old one.
        self._init_git_repo()
        staging = self._make_drafted(task_id=2, commit_msg="real", log="x")
        payload = json.loads(staging.read_text(encoding="utf-8"))
        payload["task_file"] = "/some/other/path/tasks.json"
        staging.write_text(json.dumps(payload), encoding="utf-8")
        self._stage_file("a.txt", "1\n")
        result = self.run_cli("publish", "--id", "2", cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("task_file mismatch", result.stderr)
        self.assertEqual(self.read_task_file()["tasks"][1]["status"], "drafted")
        self.assertEqual(self._git_log_subjects(), ["seed"])

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
        "set-status",
        "draft",
        "publish",
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
