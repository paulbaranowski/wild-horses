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
import sys
from pathlib import Path
from typing import Any

VALID_STATUSES = {"pending", "in-progress", "complete", "failed"}


class TaskCliError(Exception):
    """Expected, user-facing errors. Carries an exit code."""

    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.code = code


def load_and_validate(path: Path) -> dict:
    """Read + parse + minimal-schema-check the task file.

    Validates only the fields this script touches (top-level shape,
    per-task id/title/status/log, unique ids). Other fields pass through
    so this script doesn't need to be co-updated when the schema grows.
    """
    if not path.is_file():
        raise TaskCliError(f"file {path}: no such file or not readable", code=1)
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise TaskCliError(
            f"file is not valid JSON at line {e.lineno} column {e.colno}: {e.msg}",
            code=13,
        )

    if not isinstance(data, dict):
        raise TaskCliError("top-level value must be a JSON object", code=12)
    if "tasks" not in data or not isinstance(data["tasks"], list):
        raise TaskCliError('top-level "tasks" must be an array', code=12)
    if "testCommand" not in data or not isinstance(data["testCommand"], str):
        raise TaskCliError('top-level "testCommand" must be a string', code=12)

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
        if task["id"] in seen_ids:
            raise TaskCliError(f'duplicate task id {task["id"]}', code=12)
        seen_ids.add(task["id"])

    return data


def write_atomic(path: Path, data: dict) -> None:
    """Write JSON to a sibling tmp file, fsync, then os.replace.

    POSIX-atomic. The original file is untouched until the rename, so
    no half-written intermediate state is observable.
    """
    tmp = path.with_name(path.name + ".tmp")
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
    log_path = Path(args.log_file)
    if not log_path.is_file():
        raise TaskCliError(f"log file {log_path}: no such file or not readable", code=1)
    try:
        log_content = log_path.read_text(encoding="utf-8")
    except OSError as e:
        raise TaskCliError(f"log file {log_path}: {e}", code=1)
    if log_content.endswith("\n"):
        log_content = log_content[:-1]
    task["status"] = args.status
    task["log"] = log_content
    write_atomic(path, data)


def cmd_get(args: argparse.Namespace, data: dict, path: Path) -> None:
    task = find_task(data, args.id, path)
    print(json.dumps(task, indent=2, ensure_ascii=False))


def cmd_list(args: argparse.Namespace, data: dict, path: Path) -> None:
    del path
    tasks: list[Any] = data["tasks"]
    if args.remaining:
        tasks = [t for t in tasks if t["status"] in {"pending", "in-progress"}]
    elif args.status:
        tasks = [t for t in tasks if t["status"] == args.status]
    print(json.dumps(tasks, indent=2, ensure_ascii=False))


def cmd_validate(args: argparse.Namespace, data: dict, path: Path) -> None:
    del args, data, path  # load_and_validate already ran; reaching here means valid.


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="task_list_cli",
        description="Mutator + reader for harness task-list JSON files.",
    )
    parser.add_argument("--file", required=True, help="path to the task-list JSON file")
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<subcommand>")

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
        help="path to a file whose contents become the task's log field",
    )

    p_get = sub.add_parser("get", help="print one task as pretty JSON to stdout")
    p_get.add_argument("--id", type=int, required=True, help="task id")

    p_list = sub.add_parser("list", help="print tasks as a pretty JSON array to stdout")
    list_filter = p_list.add_mutually_exclusive_group()
    list_filter.add_argument(
        "--status",
        choices=sorted(VALID_STATUSES),
        help="filter by exact status",
    )
    list_filter.add_argument(
        "--remaining",
        action="store_true",
        help="sugar for status in {pending, in-progress}",
    )

    sub.add_parser("validate", help="strict-parse + minimal schema check; exit 0 if valid")

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
            "list": cmd_list,
            "validate": cmd_validate,
        }
        dispatch[args.cmd](args, data, path)
        return 0
    except TaskCliError as e:
        print(f"task_list_cli: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
