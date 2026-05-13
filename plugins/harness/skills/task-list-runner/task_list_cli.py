#!/usr/bin/env python3
"""Mutator + reader for harness task-list JSON files.

Single canonical interface for the `task-list-runner` skill and dispatched
agents. Eliminates per-agent JSON-mutation improvisation and the
silent-corruption class of bug it caused.

See SKILL.md in the same directory for invocation patterns.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

VALID_STATUSES = {"pending", "in-progress", "drafted", "complete", "failed"}
NON_TERMINAL_STATUSES = {"pending", "in-progress", "drafted"}


class HelpfulArgumentParser(argparse.ArgumentParser):
    """Print full help (not just usage) before erroring on bad/missing args.

    The default argparse error path prints `usage: ...` + a one-line error
    and exits — without the subcommand list. That's enough for someone
    who already knows the surface, but agents and humans alike benefit
    from seeing the available subcommand names when they mistype one.
    """

    def error(self, message: str):
        self.print_help(sys.stderr)
        self.exit(2, f"\n{self.prog}: error: {message}\n")


class TaskCliError(Exception):
    """Expected, user-facing errors. Carries an exit code."""

    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.code = code


def _validate_verify_steps_array(steps: Any, where: str, id_suffix: str = "") -> None:
    """Shape-check a verifySteps array.

    Used for both the top-level array and the optional per-task override.
    The caller decides whether the field's *presence* is required; this
    helper assumes the field is present and validates the array's shape.

    `where` names the location in the JSON tree (e.g. `top-level "verifySteps"`
    or `tasks[2].verifySteps`); `id_suffix` is an optional parenthetical
    appended verbatim to every error message (e.g. ` (id=3)` for per-task)
    so that a corrupt per-task override surfaces the offending task id
    without the caller threading it through every raise site.
    """
    if not isinstance(steps, list):
        raise TaskCliError(f"{where} must be an array{id_suffix}", code=12)
    if len(steps) == 0:
        raise TaskCliError(f"{where} must contain at least one step{id_suffix}", code=12)
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise TaskCliError(f"{where}[{i}] must be an object{id_suffix}", code=12)
        if not isinstance(step.get("name"), str) or not step["name"]:
            raise TaskCliError(
                f"{where}[{i}].name must be a non-empty string{id_suffix}", code=12
            )
        if not isinstance(step.get("command"), str) or not step["command"]:
            raise TaskCliError(
                f"{where}[{i}].command must be a non-empty string{id_suffix}", code=12
            )


def load_and_validate(path: Path) -> dict:
    """Read + parse + minimal-schema-check the task file.

    Validates only the fields this script touches (top-level shape,
    per-task id/title/status/log, unique ids). Other fields pass through
    so this script doesn't need to be co-updated when the schema grows.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise TaskCliError(f"file {path}: no such file or not readable", code=1) from None
    except IsADirectoryError:
        raise TaskCliError(f"file {path}: is a directory, not a file", code=1) from None
    except OSError as e:
        raise TaskCliError(f"file {path}: {e}", code=1) from e
    except UnicodeDecodeError as e:
        raise TaskCliError(f"file {path} is not valid UTF-8: {e}", code=13) from e
    except json.JSONDecodeError as e:
        raise TaskCliError(
            f"file is not valid JSON at line {e.lineno} column {e.colno}: {e.msg}",
            code=13,
        ) from e

    if not isinstance(data, dict):
        raise TaskCliError("top-level value must be a JSON object", code=12)
    if "tasks" not in data or not isinstance(data["tasks"], list):
        raise TaskCliError('top-level "tasks" must be an array', code=12)
    if "testCommand" in data:
        # Hard break: old single-string field was replaced by verifySteps in v4.
        # Checked before the verifySteps shape check so users on the old schema
        # get the actionable migration message, not a generic "missing field" error.
        raise TaskCliError(
            'field "testCommand" was replaced by "verifySteps" (an array of '
            "{name, command} objects). To migrate, replace "
            '`"testCommand": "X"` with `"verifySteps": [{"name": "tests", "command": "X"}]`.',
            code=12,
        )
    if "verifySteps" not in data:
        raise TaskCliError('top-level "verifySteps" must be an array', code=12)
    _validate_verify_steps_array(data["verifySteps"], where='top-level "verifySteps"')

    seen_ids: set = set()
    for i, task in enumerate(data["tasks"]):
        if not isinstance(task, dict):
            raise TaskCliError(f"tasks[{i}] must be an object", code=12)
        if not isinstance(task.get("id"), int) or isinstance(task.get("id"), bool):
            raise TaskCliError(f"tasks[{i}].id must be an integer", code=12)
        if not isinstance(task.get("title"), str):
            raise TaskCliError(f'tasks[{i}].title must be a string (id={task.get("id")})', code=12)
        if task.get("status") not in VALID_STATUSES:
            raise TaskCliError(
                f'tasks[{i}].status must be one of {sorted(VALID_STATUSES)} '
                f'(id={task["id"]}, got {task.get("status")!r})',
                code=12,
            )
        log = task.get("log", None)
        if log is not None and not isinstance(log, str):
            raise TaskCliError(f'tasks[{i}].log must be a string or null (id={task["id"]})', code=12)
        # Per-task verifySteps is an optional override that *replaces* the
        # top-level array for this task's `verify --id <N>` call. Field is
        # optional; when present, must satisfy the same shape rules as the
        # top-level array. Absence inherits the top-level default.
        if "verifySteps" in task:
            _validate_verify_steps_array(
                task["verifySteps"],
                where=f"tasks[{i}].verifySteps",
                id_suffix=f' (id={task["id"]})',
            )
        if task["id"] in seen_ids:
            raise TaskCliError(f'duplicate task id {task["id"]}', code=12)
        seen_ids.add(task["id"])

    return data


def write_atomic(path: Path, data: dict) -> None:
    """Write JSON to a sibling tmp file, fsync, then os.replace.

    POSIX-atomic. The original file is untouched until the rename, so
    no half-written intermediate state is observable. The tmp file gets
    a unique mkstemp-generated name so two concurrent writers can't
    clobber each other (the spec says runner is single-writer by
    design, but defending against accidental violations is cheap).
    """
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def find_task(data: dict, task_id: int, file_path: Path) -> dict:
    for task in data["tasks"]:
        if task["id"] == task_id:
            return task
    raise TaskCliError(f"task id {task_id} not found in {file_path}", code=10)


def _read_log_input(log_arg: str) -> str:
    """Resolve the `--log-file` argument to its UTF-8 string contents.

    Shared by `set-status` and `draft` — both accept either a file path or
    `-` (read from stdin). Centralised so the stdin/file branching, error
    mapping (UnicodeDecodeError → exit 13, missing file → exit 1), and the
    one-trailing-newline strip stay consistent across the two callers.
    """
    if log_arg == "-":
        try:
            content = sys.stdin.read()
        except UnicodeDecodeError as e:
            raise TaskCliError(f"stdin is not valid UTF-8: {e}", code=13) from e
    else:
        log_path = Path(log_arg)
        if not log_path.is_file():
            raise TaskCliError(f"log file {log_path}: no such file or not readable", code=1)
        try:
            content = log_path.read_text(encoding="utf-8")
        except OSError as e:
            raise TaskCliError(f"log file {log_path}: {e}", code=1) from e
        except UnicodeDecodeError as e:
            raise TaskCliError(f"log file {log_path} is not valid UTF-8: {e}", code=13) from e
    if content.endswith("\n"):
        content = content[:-1]
    return content


def _staging_path(task_file: Path, task_id: int) -> Path:
    """Per-task-file, per-task staging path under /tmp.

    Hashes the absolute task-file path so two worktrees driving different
    task files (or the same file with different IDs) cannot collide. 12
    hex chars of md5 is plenty — collisions would require deliberate
    crafting and the cost is just an overwrite of someone else's staging
    file, which the runner detects on the next status-check anyway.
    """
    digest = hashlib.md5(str(task_file.resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(f"/tmp/harness-stage-{digest}-{task_id}.json")


def _staging_write(staging_path: Path, payload: dict) -> None:
    """Atomic write to the staging file (tmp + os.replace).

    Same atomicity property as write_atomic for the task JSON: a crash
    mid-write leaves either the previous staging file or no staging file —
    never a half-written one. The staging file's only consumer is
    `publish`, which validates required keys before acting on it.
    """
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{staging_path.name}.",
        suffix=".tmp",
        dir=str(staging_path.parent),
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, staging_path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def _staging_read(staging_path: Path) -> dict:
    """Read + minimally validate the staging file's required keys.

    `publish` calls this; missing/malformed staging files exit 1 (IO/
    precondition) rather than 12 (schema), since the staging file is
    runtime scratch state, not part of the schema-governed task JSON.
    """
    if not staging_path.is_file():
        raise TaskCliError(
            f"staging file {staging_path} not found — was `draft --id <N>` called first?",
            code=1,
        )
    try:
        with staging_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise TaskCliError(f"staging file {staging_path}: {e}", code=1) from e
    except json.JSONDecodeError as e:
        raise TaskCliError(
            f"staging file {staging_path} is not valid JSON: {e.msg}", code=1
        ) from e
    if not isinstance(data, dict):
        raise TaskCliError(f"staging file {staging_path} must contain a JSON object", code=1)
    if not isinstance(data.get("commit_msg"), str) or not data["commit_msg"].strip():
        raise TaskCliError(
            f"staging file {staging_path} missing or empty `commit_msg`", code=1
        )
    return data


def cmd_start(args: argparse.Namespace, data: dict, path: Path) -> None:
    task = find_task(data, args.id, path)
    if task["status"] != "pending":
        raise TaskCliError(
            f'cannot start task {args.id}: current status is "{task["status"]}", expected "pending"',
            code=11,
        )
    task["status"] = "in-progress"
    write_atomic(path, data)


def cmd_set_status(args: argparse.Namespace, data: dict, path: Path) -> None:
    """Move a task to a terminal status without touching git.

    Allowed transitions:
      in-progress → complete  (no-code tasks like investigation work)
      in-progress → failed    (implementation gave up)
      drafted     → failed    (validation rejected the draft after retries)

    Disallowed: drafted → complete. Force the happy path through `publish`
    so a task cannot reach `complete` without a corresponding git commit.
    """
    task = find_task(data, args.id, path)
    current = task["status"]
    if current == "in-progress":
        if args.status not in ("complete", "failed"):
            raise TaskCliError(
                f'cannot set-status task {args.id} to "{args.status}" from "in-progress"; '
                'expected "complete" or "failed"',
                code=11,
            )
    elif current == "drafted":
        if args.status != "failed":
            raise TaskCliError(
                f'cannot set-status task {args.id} to "{args.status}" from "drafted"; '
                'only "failed" is allowed (use `publish` for the success path)',
                code=11,
            )
    else:
        raise TaskCliError(
            f'cannot set-status task {args.id}: current status is "{current}", '
            'expected "in-progress" or "drafted"',
            code=11,
        )
    task["status"] = args.status
    task["log"] = _read_log_input(args.log_file)
    write_atomic(path, data)


def cmd_draft(args: argparse.Namespace, data: dict, path: Path) -> None:
    """Move an in-progress task to `drafted`, parking the commit message.

    Writes the log into the task's `log` field (so it's persisted in the
    schema-governed file even if the staging file disappears) and writes
    the commit subject into a per-task staging file. Does NOT touch git —
    `publish` is the only place git is invoked. The split exists so the
    runner-level validation step happens *between* `draft` and `publish`,
    and a validation failure can re-route to `set-status failed` without
    needing a "rollback the commit" path.
    """
    task = find_task(data, args.id, path)
    if task["status"] != "in-progress":
        raise TaskCliError(
            f'cannot draft task {args.id}: current status is "{task["status"]}", expected "in-progress"',
            code=11,
        )
    commit_msg = args.commit_msg.strip()
    if not commit_msg:
        raise TaskCliError("--commit-msg must be a non-empty string", code=2)
    log_content = _read_log_input(args.log_file)
    staging = _staging_path(path, args.id)
    _staging_write(
        staging,
        {
            "task_id": args.id,
            "task_file": str(path.resolve()),
            "commit_msg": commit_msg,
        },
    )
    task["status"] = "drafted"
    task["log"] = log_content
    write_atomic(path, data)
    print(f"drafted task {args.id}; staging at {staging}")


def cmd_publish(args: argparse.Namespace, data: dict, path: Path) -> None:
    """Move a drafted task to `complete`, running `git commit` first.

    Order matters: commit first, status second. If the commit fails (e.g.,
    a pre-commit hook rejects it), the task stays `drafted` and the
    staging file stays put — the runner can fix the underlying issue and
    re-run `publish --id <N>` without re-implementing the task.

    Trust model: the git index is assumed already populated by the
    implementation agent's `git add <files>`. We verify the index has at
    least one staged file before invoking `git commit` so the failure
    mode is "you forgot to stage" with a useful message rather than git's
    less-actionable "nothing to commit, working tree clean".
    """
    task = find_task(data, args.id, path)
    if task["status"] != "drafted":
        raise TaskCliError(
            f'cannot publish task {args.id}: current status is "{task["status"]}", expected "drafted"',
            code=11,
        )
    staging = _staging_path(path, args.id)
    staging_data = _staging_read(staging)
    commit_msg = staging_data["commit_msg"]
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
    )
    if staged.returncode != 0:
        raise TaskCliError(
            f"git diff --cached failed (are we in a git repo?): {staged.stderr.strip()}",
            code=15,
        )
    if not staged.stdout.strip():
        raise TaskCliError(
            f"no files staged for commit (git index is empty); did the implementation "
            f"agent forget `git add` for task {args.id}?",
            code=15,
        )
    commit = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        # Surface git's stderr verbatim — pre-commit hook rejections, signing
        # failures, etc. all live there and are typically actionable.
        raise TaskCliError(
            f"git commit failed (task stays drafted; staging file preserved):\n"
            f"{commit.stderr.strip() or commit.stdout.strip()}",
            code=15,
        )
    task["status"] = "complete"
    write_atomic(path, data)
    try:
        staging.unlink()
    except FileNotFoundError:
        pass
    print(f"published task {args.id}; commit subject: {commit_msg}")


def cmd_get(args: argparse.Namespace, data: dict, path: Path) -> None:
    task = find_task(data, args.id, path)
    print(json.dumps(task, indent=2, ensure_ascii=False))


def _remaining_entries(data: dict) -> list[dict]:
    """Compact projection of non-terminal tasks — used by `remaining`.

    `status` exposes only the count derived from this list; the array
    itself is materialised on demand by the `remaining` subcommand. Both
    sites filter through `NON_TERMINAL_STATUSES` so the count and the
    list cannot disagree about what "remaining" means.
    """
    return [
        {"id": t["id"], "title": t["title"], "effort": t.get("effort"), "status": t["status"]}
        for t in data["tasks"]
        if t["status"] in NON_TERMINAL_STATUSES
    ]


def cmd_status(args: argparse.Namespace, data: dict, path: Path) -> None:
    del args, path
    counts = {status: 0 for status in VALID_STATUSES}
    for task in data["tasks"]:
        counts[task["status"]] += 1
    # Count keys use snake_case so prose can use dot-access uniformly
    # (status.in_progress vs the awkward status["in-progress"]). The
    # schema enum value in tasks[].status is still "in-progress" — only
    # the summary count key is renamed.
    #
    # `remaining` here is a precomputed integer summed from the same
    # NON_TERMINAL_STATUSES set the `remaining` subcommand filters on,
    # so the halt-gate count and the listing cannot disagree. `drafted`
    # is non-terminal (the runner still owes it a publish-or-fail call),
    # so it counts toward remaining; otherwise the loop would exit
    # leaving uncommitted code in the index and a stale staging file.
    summary = {
        "total": len(data["tasks"]),
        "pending": counts["pending"],
        "in_progress": counts["in-progress"],
        "drafted": counts["drafted"],
        "complete": counts["complete"],
        "failed": counts["failed"],
        "remaining": sum(counts[s] for s in NON_TERMINAL_STATUSES),
        "plan": data.get("plan"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def cmd_remaining(args: argparse.Namespace, data: dict, path: Path) -> None:
    del args, path
    print(json.dumps(_remaining_entries(data), indent=2, ensure_ascii=False))


def _slug(name: str) -> str:
    """Reduce an arbitrary step name to a safe path/identifier component.

    Lowercase a–z / 0–9 only, hyphens collapse, no leading/trailing hyphens.
    Used for log-file paths and shell-safe labels — the original name still
    appears verbatim in the script's leading comment for readability.
    """
    out: list[str] = []
    for ch in name.lower():
        if ch.isalnum() and ch.isascii():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "step"


def _verify_log_path(task_id: int, index: int, slug: str) -> Path:
    """Single source of truth for the per-step log path.

    Agents are told in SKILL.md that the failing step's log lives at
    `/tmp/verify-<id>-step<i>-<slug>.log`, so this helper centralises the
    contract — every code path that names that file goes through here.
    """
    return Path(f"/tmp/verify-{task_id}-step{index}-{slug}.log")


def cmd_verify(args: argparse.Namespace, data: dict, path: Path) -> None:
    """Execute verifySteps for task <id> in order, capturing per-step output.

    Each step's stdout+stderr goes to its own log file under /tmp; the
    loop stops on the first failing step and exits with that step's
    exit code; a `verify[i/n] <slug> exit=<EX> log=<path>` line is
    printed per executed step; on full success, a final
    `verify: all <n> step(s) passed` line is printed.

    Trust model: this subcommand is auto-approved by the harness's
    PreToolUse hook (covers all `task_list_cli.py` invocations), so
    each per-task call runs without a per-iteration permission prompt.
    Trust for verifySteps content is delegated to the upstream
    task-list-builder; users who need per-call interception should
    disable the hook.
    """
    task = find_task(data, args.id, path)
    # Per-task verifySteps, when present, *replaces* the top-level array
    # for this task's verification — total replacement, not a merge. The
    # validator rejects an empty per-task array, so a present field is
    # always non-empty (the `or` short-circuit only fires when the field
    # is absent, in which case `task.get` returns None).
    steps = task.get("verifySteps") or data["verifySteps"]
    n = len(steps)
    plural = "" if n == 1 else "s"
    for i, step in enumerate(steps, start=1):
        slug = _slug(step["name"])
        log_path = _verify_log_path(args.id, i, slug)
        try:
            with log_path.open("w", encoding="utf-8") as log_f:
                result = subprocess.run(
                    step["command"],
                    shell=True,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                )
        except OSError as e:
            raise TaskCliError(f"log file {log_path}: {e}", code=1) from e
        print(f"verify[{i}/{n}] {slug} exit={result.returncode} log={log_path}")
        if result.returncode != 0:
            # SystemExit propagates past main()'s TaskCliError handler —
            # different exception type — so the process exits cleanly
            # with the failing step's code as the agent expects.
            sys.exit(result.returncode)
    print(f"verify: all {n} step{plural} passed")


def cmd_next(args: argparse.Namespace, data: dict, path: Path) -> None:
    del args
    tasks = data["tasks"]
    # Architectural invariant: a drafted task blocks `next`. A drafted task
    # means the implementation agent finished but the runner has not yet
    # called `publish` or `set-status failed` for it — typically because the
    # runner crashed between draft and validation. Force the runner to
    # resolve it (publish on validation pass, set-status failed on retry
    # exhaustion) before claiming new work, otherwise the staging file
    # would be orphaned and the git index would carry stale staged changes
    # into the next task.
    for task in tasks:
        if task["status"] == "drafted":
            raise TaskCliError(
                f'task {task["id"]} is drafted; resolve via '
                f'`publish --id {task["id"]}` or `set-status --id {task["id"]} --status failed` '
                "before claiming new work",
                code=11,
            )
    # Resume preference: an already-in-progress task means a previous iteration
    # crashed mid-task. Return it without changing status so the agent can
    # finish what it started.
    for task in tasks:
        if task["status"] == "in-progress":
            print(json.dumps(task, indent=2, ensure_ascii=False))
            return
    # No in-progress: claim the first pending task and flip it.
    for task in tasks:
        if task["status"] == "pending":
            task["status"] = "in-progress"
            write_atomic(path, data)
            print(json.dumps(task, indent=2, ensure_ascii=False))
            return
    raise TaskCliError("no remaining tasks", code=14)


def cmd_list(args: argparse.Namespace, data: dict, path: Path) -> None:
    del path
    tasks: list[Any] = data["tasks"]
    if args.status:
        tasks = [t for t in tasks if t["status"] == args.status]
    print(json.dumps(tasks, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = HelpfulArgumentParser(
        prog="task_list_cli",
        description="Mutator + reader for harness task-list JSON files.",
    )
    parser.add_argument("--file", required=True, help="path to the task-list JSON file")
    sub = parser.add_subparsers(
        dest="cmd",
        required=True,
        metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )

    p_start = sub.add_parser("start", help="flip task <id> from pending → in-progress")
    p_start.add_argument("--id", type=int, required=True, help="task id")

    p_set_status = sub.add_parser(
        "set-status",
        help="flip task <id> to a terminal status (in-progress→complete|failed, drafted→failed). "
        "Use `publish` for the drafted→complete success path.",
    )
    p_set_status.add_argument("--id", type=int, required=True, help="task id")
    p_set_status.add_argument(
        "--status",
        required=True,
        choices=["complete", "failed"],
        help="terminal status to set",
    )
    p_set_status.add_argument(
        "--log-file",
        required=True,
        help="path to a file whose contents become the task's log field, "
        "or `-` to read from stdin (use a quoted heredoc to avoid shell quoting)",
    )

    p_draft = sub.add_parser(
        "draft",
        help="flip task <id> from in-progress → drafted; writes log into the task and "
        "the commit subject into a per-task /tmp staging file. Does not touch git.",
    )
    p_draft.add_argument("--id", type=int, required=True, help="task id")
    p_draft.add_argument(
        "--commit-msg",
        required=True,
        help="single-line commit subject; consumed later by `publish`",
    )
    p_draft.add_argument(
        "--log-file",
        required=True,
        help="path to a file whose contents become the task's log field, "
        "or `-` to read from stdin (use a quoted heredoc to avoid shell quoting)",
    )

    p_publish = sub.add_parser(
        "publish",
        help="flip task <id> from drafted → complete; runs `git commit` against the "
        "already-staged git index using the staged subject. Removes the staging file on success.",
    )
    p_publish.add_argument("--id", type=int, required=True, help="task id")

    p_get = sub.add_parser("get", help="print one task as pretty JSON to stdout")
    p_get.add_argument("--id", type=int, required=True, help="task id")

    sub.add_parser(
        "next",
        help="atomically claim and print the next task (resume in-progress, else flip first pending → in-progress)",
    )

    sub.add_parser(
        "status",
        help="print task counts + remaining count + plan path as JSON",
    )

    sub.add_parser(
        "remaining",
        help="print non-terminal tasks (pending + in-progress + drafted) as a compact JSON array (id, title, effort, status)",
    )

    p_verify = sub.add_parser(
        "verify",
        help="execute verifySteps in order, capturing per-step output to /tmp logs",
    )
    p_verify.add_argument("--id", type=int, required=True, help="task id (used for log-file paths)")

    p_list = sub.add_parser("list", help="print tasks as a pretty JSON array to stdout")
    p_list.add_argument(
        "--status",
        choices=sorted(VALID_STATUSES),
        help="filter by exact status",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        path = Path(args.file)
        data = load_and_validate(path)
        dispatch = {
            "start": cmd_start,
            "set-status": cmd_set_status,
            "draft": cmd_draft,
            "publish": cmd_publish,
            "get": cmd_get,
            "next": cmd_next,
            "status": cmd_status,
            "remaining": cmd_remaining,
            "verify": cmd_verify,
            "list": cmd_list,
        }
        dispatch[args.cmd](args, data, path)
        return 0
    except TaskCliError as e:
        print(f"task_list_cli: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
