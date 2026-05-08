#!/usr/bin/env python3
"""Mutator + reader for harness task-list JSON files.

Single canonical interface for the `task-list-runner` skill and dispatched
agents. Eliminates per-agent JSON-mutation improvisation and the
silent-corruption class of bug it caused.

See SKILL.md in the same directory for invocation patterns.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

VALID_STATUSES = {"pending", "in-progress", "complete", "failed"}
NON_TERMINAL_STATUSES = {"pending", "in-progress"}


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


def cmd_start(args: argparse.Namespace, data: dict, path: Path) -> None:
    task = find_task(data, args.id, path)
    if task["status"] != "pending":
        raise TaskCliError(
            f'cannot start task {args.id}: current status is "{task["status"]}", expected "pending"',
            code=11,
        )
    task["status"] = "in-progress"
    write_atomic(path, data)


def cmd_finish(args: argparse.Namespace, data: dict, path: Path) -> None:
    task = find_task(data, args.id, path)
    if task["status"] != "in-progress":
        raise TaskCliError(
            f'cannot finish task {args.id}: current status is "{task["status"]}", expected "in-progress"',
            code=11,
        )
    if args.log_file == "-":
        # Unix convention: `-` means read from stdin. Lets the agent pipe a
        # heredoc directly without an intermediate /tmp file (and the Write-
        # tool classifier gating that comes with one). Heredoc content is
        # verbatim bytes from the shell — no shell-arg quoting hazard.
        try:
            log_content = sys.stdin.read()
        except UnicodeDecodeError as e:
            raise TaskCliError(f"stdin is not valid UTF-8: {e}", code=13) from e
    else:
        log_path = Path(args.log_file)
        if not log_path.is_file():
            raise TaskCliError(f"log file {log_path}: no such file or not readable", code=1)
        try:
            log_content = log_path.read_text(encoding="utf-8")
        except OSError as e:
            raise TaskCliError(f"log file {log_path}: {e}", code=1) from e
        except UnicodeDecodeError as e:
            raise TaskCliError(f"log file {log_path} is not valid UTF-8: {e}", code=13) from e
    if log_content.endswith("\n"):
        log_content = log_content[:-1]
    task["status"] = args.status
    task["log"] = log_content
    write_atomic(path, data)


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
    # `remaining` here is a precomputed integer (pending + in_progress)
    # so the halt-gate can read one number without agent-side math. The
    # full task array lives behind the separate `remaining` subcommand —
    # Phase 3 calls it once for the user-facing summary; the loop's hot
    # path never pays for that payload.
    summary = {
        "total": len(data["tasks"]),
        "pending": counts["pending"],
        "in_progress": counts["in-progress"],
        "complete": counts["complete"],
        "failed": counts["failed"],
        "remaining": counts["pending"] + counts["in-progress"],
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

    p_finish = sub.add_parser("finish", help="flip task <id> from in-progress → complete|failed")
    p_finish.add_argument("--id", type=int, required=True, help="task id")
    p_finish.add_argument(
        "--status",
        required=True,
        choices=["complete", "failed"],
        help="terminal status to set",
    )
    p_finish.add_argument(
        "--log-file",
        required=True,
        help="path to a file whose contents become the task's log field, "
        "or `-` to read from stdin (use a quoted heredoc to avoid shell quoting)",
    )

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
        help="print non-terminal tasks (pending + in-progress) as a compact JSON array (id, title, effort, status)",
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
            "finish": cmd_finish,
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
