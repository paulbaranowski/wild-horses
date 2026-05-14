#!/usr/bin/env python3
"""Mutator + reader for codepath-visualizer JSON files.

Single canonical interface for the `codepath-mapper` / `codepath-visualizer`
skills and dispatched agents. Maintains atomic writes, schema validation,
and a stable exit-code surface across two JSON files: `architecture.json`
(the codebase's components + categories + edges) and `codepaths.json`
(named traversals through the architecture).

This file is the bootstrap scaffold: it registers all nine subcommands
via argparse but leaves every handler unwired. Subsequent tasks fill in
each `cmd_*` handler and add it to the DISPATCH table.

See `codepaths-schema.md` in the plugin root for the JSON shapes,
invariants, and the full exit-code table.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ARCH_FILE = "architecture.json"
CODEPATHS_FILE = "codepaths.json"
DEFAULT_DIR = "docs/codepaths"
ID_PATTERN = r"^[a-z0-9-]+$"
ID_RE = re.compile(ID_PATTERN)


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


class CliError(Exception):
    """Expected, user-facing errors. Carries an exit code.

    See `codepaths-schema.md` for the canonical exit-code table.
    """

    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.code = code


def default_arch_skeleton() -> dict:
    return {
        "$schemaVersion": 1,
        "app": {"name": "Untitled app", "subtitle": ""},
        "categories": [
            {"id": "actor",    "label": "Actor",            "color": "#a78bfa", "column": 0},
            {"id": "ui",       "label": "UI / Client",      "color": "#60a5fa", "column": 1},
            {"id": "api",      "label": "API / Backend",    "color": "#fbbf24", "column": 2},
            {"id": "data",     "label": "Data store",       "color": "#34d399", "column": 3},
            {"id": "job",      "label": "Background job",   "color": "#f87171", "column": 4},
            {"id": "external", "label": "External service", "color": "#9ca3af", "column": 5},
        ],
        "components": [],
        "edges": [],
    }


def _check_id(value: Any, where: str) -> None:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise CliError(f'{where} must be a kebab-case id matching {ID_PATTERN!r}, got {value!r}', code=12)


def _read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise
    except OSError as e:
        raise CliError(f"file {path}: {e}", code=1) from e
    except UnicodeDecodeError as e:
        raise CliError(f"file {path} is not valid UTF-8: {e}", code=13) from e
    except json.JSONDecodeError as e:
        raise CliError(
            f"file {path}: not valid JSON at line {e.lineno} column {e.colno}: {e.msg}",
            code=13,
        ) from e


def _validate_arch_dict(data: Any) -> None:
    """Validate architecture dict in place; raise CliError(code=12) on any violation."""
    if not isinstance(data, dict):
        raise CliError("architecture.json: top-level must be a JSON object", code=12)
    for field in ("categories", "components", "edges"):
        if field not in data or not isinstance(data[field], list):
            raise CliError(f'architecture.json: "{field}" must be an array', code=12)

    # Categories
    cat_ids: set[str] = set()
    cat_columns: set[int] = set()
    for i, c in enumerate(data["categories"]):
        if not isinstance(c, dict):
            raise CliError(f"architecture.json: categories[{i}] must be an object", code=12)
        _check_id(c.get("id"), f"architecture.json: categories[{i}].id")
        if c["id"] in cat_ids:
            raise CliError(f"architecture.json: duplicate category id {c['id']!r}", code=12)
        cat_ids.add(c["id"])
        col = c.get("column")
        if not isinstance(col, int) or isinstance(col, bool) or col < 0:
            raise CliError(
                f"architecture.json: categories[{i}].column must be a non-negative integer", code=12
            )
        if col in cat_columns:
            raise CliError(
                f"architecture.json: duplicate category column {col} (each must be unique)", code=12
            )
        cat_columns.add(col)
        if not isinstance(c.get("label"), str) or not c["label"]:
            raise CliError(f"architecture.json: categories[{i}].label must be a non-empty string", code=12)

    # Components
    comp_ids: set[str] = set()
    for i, comp in enumerate(data["components"]):
        if not isinstance(comp, dict):
            raise CliError(f"architecture.json: components[{i}] must be an object", code=12)
        _check_id(comp.get("id"), f"architecture.json: components[{i}].id")
        if comp["id"] in comp_ids:
            raise CliError(f"architecture.json: duplicate component id {comp['id']!r}", code=12)
        comp_ids.add(comp["id"])
        if comp.get("category") not in cat_ids:
            raise CliError(
                f"architecture.json: components[{i}].category {comp.get('category')!r} not in categories",
                code=12,
            )

    # Edges
    for i, e in enumerate(data["edges"]):
        if not isinstance(e, dict):
            raise CliError(f"architecture.json: edges[{i}] must be an object", code=12)
        if e.get("from") not in comp_ids:
            raise CliError(
                f"architecture.json: edges[{i}].from {e.get('from')!r} not in components", code=12
            )
        if e.get("to") not in comp_ids:
            raise CliError(
                f"architecture.json: edges[{i}].to {e.get('to')!r} not in components", code=12
            )


def load_and_validate_arch(dir_: Path) -> dict:
    """Load architecture.json from dir_. Missing file → default skeleton (auto-init)."""
    path = dir_ / ARCH_FILE
    try:
        data = _read_json(path)
    except FileNotFoundError:
        return default_arch_skeleton()
    _validate_arch_dict(data)
    return data


def default_codepaths_skeleton() -> dict:
    return {"$schemaVersion": 1, "codepaths": []}


def load_and_validate_codepaths(dir_: Path, arch: dict) -> dict:
    """Load codepaths.json from dir_. Missing file → empty skeleton.

    Validates schema, unique ids, and cross-refs against the given `arch`
    (call `load_and_validate_arch(dir_)` first). Cross-ref violations
    raise CliError(code=15); other schema violations raise CliError(code=12).
    """
    path = dir_ / CODEPATHS_FILE
    try:
        data = _read_json(path)
    except FileNotFoundError:
        return default_codepaths_skeleton()

    if not isinstance(data, dict):
        raise CliError("codepaths.json: top-level must be a JSON object", code=12)
    if "codepaths" not in data or not isinstance(data["codepaths"], list):
        raise CliError('codepaths.json: "codepaths" must be an array', code=12)

    comp_ids = {c["id"] for c in arch["components"]}
    seen: set[str] = set()
    for i, cp in enumerate(data["codepaths"]):
        if not isinstance(cp, dict):
            raise CliError(f"codepaths.json: codepaths[{i}] must be an object", code=12)
        _check_id(cp.get("id"), f"codepaths.json: codepaths[{i}].id")
        if cp["id"] in seen:
            raise CliError(f"codepaths.json: duplicate codepath id {cp['id']!r}", code=12)
        seen.add(cp["id"])
        if not isinstance(cp.get("name"), str) or not cp["name"]:
            raise CliError(f"codepaths.json: codepaths[{i}].name must be a non-empty string", code=12)
        if "steps" not in cp or not isinstance(cp["steps"], list) or not cp["steps"]:
            raise CliError(
                f"codepaths.json: codepaths[{i}].steps must be a non-empty array", code=12
            )
        for j, step in enumerate(cp["steps"]):
            if not isinstance(step, dict):
                raise CliError(
                    f"codepaths.json: codepaths[{i}].steps[{j}] must be an object", code=12
                )
            for k in ("from", "to"):
                v = step.get(k)
                if v not in comp_ids:
                    raise CliError(
                        f"codepaths.json: codepaths[{i}].steps[{j}].{k} {v!r} not in architecture.components",
                        code=15,
                    )
            if not isinstance(step.get("annotation"), str) or not step["annotation"]:
                raise CliError(
                    f"codepaths.json: codepaths[{i}].steps[{j}].annotation must be a non-empty string",
                    code=12,
                )

    return data


def load_both(dir_: Path) -> tuple[dict, dict]:
    """Convenience: validate arch first, then codepaths against that arch."""
    arch = load_and_validate_arch(dir_)
    cps = load_and_validate_codepaths(dir_, arch)
    return arch, cps


def write_atomic(path: Path, data: dict) -> None:
    """Atomic write: tmp file + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", dir=str(path.parent), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json_arg(arg: str) -> Any:
    """Read JSON from a file path, or from stdin when `arg == '-'`.

    Mirrors the `--log-file -` pattern from `task_list_cli.py`: dispatched
    agents can pipe payloads through a single auto-approved Bash call
    (`cli mutate --json - <<'EOF' ... EOF`) without writing temp files
    first. JSON decode failures raise `CliError(code=13)` so partial /
    malformed input is distinguishable from schema-violation input
    (`code=12`) and IO failures (`code=1`).
    """
    if arg == "-":
        raw = sys.stdin.read()
        source = "<stdin>"
    else:
        try:
            raw = Path(arg).read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise CliError(f"file {arg}: not found", code=1) from e
        except OSError as e:
            raise CliError(f"file {arg}: {e}", code=1) from e
        except UnicodeDecodeError as e:
            raise CliError(f"file {arg} is not valid UTF-8: {e}", code=13) from e
        source = arg
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise CliError(
            f"{source}: not valid JSON at line {e.lineno} column {e.colno}: {e.msg}",
            code=13,
        ) from e


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse tree for all nine subcommands.

    Mutating verbs (`set-architecture`, `add-codepath`, `update-codepath`)
    take `--json` which accepts either a file path or `-` for stdin —
    same pattern as `task_list_cli.py`'s `--log-file -`. Read verbs
    (`list`, `get`) split by `--kind` so callers ask for components,
    categories, edges, or codepaths explicitly; `get` also takes `--id`
    to select a specific entry. `render` and `select` accept an optional
    `--output` to override the default HTML destination.
    """
    parser = HelpfulArgumentParser(
        prog="codepaths_cli.py",
        description=(
            "Mutator + reader for codepath-visualizer JSON files "
            "(architecture.json + codepaths.json)."
        ),
    )
    parser.add_argument(
        "--dir",
        default=DEFAULT_DIR,
        help=f"Directory containing architecture.json and codepaths.json (default: {DEFAULT_DIR})",
    )
    sub = parser.add_subparsers(dest="cmd", metavar="<subcommand>")

    # Mutating verbs --------------------------------------------------------

    p_set_arch = sub.add_parser(
        "set-architecture",
        help="Write a new architecture.json (replaces existing).",
    )
    p_set_arch.add_argument(
        "--json",
        required=True,
        help="Path to JSON file, or - for stdin",
    )

    p_add = sub.add_parser(
        "add-codepath",
        help="Append a codepath to codepaths.json.",
    )
    p_add.add_argument(
        "--json",
        required=True,
        help="Path to JSON file, or - for stdin",
    )

    p_update = sub.add_parser(
        "update-codepath",
        help="Replace an existing codepath by id.",
    )
    p_update.add_argument("--id", required=True, help="Codepath id to update")
    p_update.add_argument(
        "--json",
        required=True,
        help="Path to JSON file, or - for stdin",
    )

    p_remove = sub.add_parser(
        "remove-codepath",
        help="Remove a codepath by id.",
    )
    p_remove.add_argument("--id", required=True, help="Codepath id to remove")

    # Read verbs -----------------------------------------------------------

    p_list = sub.add_parser(
        "list",
        help="List entries of a given kind as a JSON array.",
    )
    p_list.add_argument(
        "--kind",
        required=True,
        choices=["components", "categories", "edges", "codepaths"],
        help="What to list",
    )

    p_get = sub.add_parser(
        "get",
        help="Get a single entry by id (or composite from->to for edges).",
    )
    p_get.add_argument(
        "--kind",
        required=True,
        choices=["components", "categories", "edges", "codepaths"],
        help="What to fetch",
    )
    p_get.add_argument("--id", required=True, help="Entry id (or 'from->to' for edges)")

    sub.add_parser(
        "status",
        help="Print counts, mtimes, and render-staleness as JSON.",
    )

    # Output verbs ---------------------------------------------------------

    p_render = sub.add_parser(
        "render",
        help="Bake JSON into the HTML template; writes architecture.html.",
    )
    p_render.add_argument(
        "--output",
        help="Override output path (default: <dir>/architecture.html).",
    )

    p_select = sub.add_parser(
        "select",
        help="Open the viz in select mode; wait for user pick; print merged JSON.",
    )
    p_select.add_argument(
        "--output",
        help="Override HTML path served to the browser (default: <dir>/architecture.html).",
    )

    return parser


def cmd_set_architecture(args: argparse.Namespace) -> None:
    """Write a new architecture.json (replaces any existing).

    Validation runs BEFORE `write_atomic` so a bad payload never touches
    disk — atomicity here means "either the new architecture is on disk
    intact, or the old one is untouched". This is why `_validate_arch_dict`
    is a top-level helper shared with `load_and_validate_arch`: load and
    write apply identical rules, so a payload that round-trips through
    `set-architecture` is guaranteed loadable.
    """
    payload = read_json_arg(args.json)
    _validate_arch_dict(payload)
    dir_ = Path(args.dir)
    write_atomic(dir_ / ARCH_FILE, payload)


def _validate_single_codepath(cp: Any, arch: dict, existing_ids: set[str]) -> None:
    """Validate one codepath payload against the architecture and existing-id set.

    Top-level helper so `add-codepath` and `update-codepath` apply identical
    rules to incoming payloads — same discipline as `_validate_arch_dict`.
    Duplicate id raises `CliError(code=11)`; cross-ref violations against
    `arch["components"]` raise `CliError(code=15)`; every other schema
    violation raises `CliError(code=12)`. The exit-code split is what lets
    dispatched agents (and humans) distinguish "you re-used an id" from
    "you typo'd a component id" from "you sent malformed schema" without
    parsing stderr.
    """
    if not isinstance(cp, dict):
        raise CliError("codepath payload must be an object", code=12)
    _check_id(cp.get("id"), "codepath.id")
    if cp["id"] in existing_ids:
        raise CliError(f"codepath id {cp['id']!r} already exists", code=11)
    if not isinstance(cp.get("name"), str) or not cp["name"]:
        raise CliError("codepath.name must be a non-empty string", code=12)
    if not isinstance(cp.get("steps"), list) or not cp["steps"]:
        raise CliError("codepath.steps must be a non-empty array", code=12)
    comp_ids = {c["id"] for c in arch["components"]}
    for j, step in enumerate(cp["steps"]):
        if not isinstance(step, dict):
            raise CliError(f"codepath.steps[{j}] must be an object", code=12)
        for k in ("from", "to"):
            v = step.get(k)
            if v not in comp_ids:
                raise CliError(
                    f"codepath.steps[{j}].{k} {v!r} not in architecture.components",
                    code=15,
                )
        if not isinstance(step.get("annotation"), str) or not step["annotation"]:
            raise CliError(
                f"codepath.steps[{j}].annotation must be a non-empty string", code=12
            )


def cmd_add_codepath(args: argparse.Namespace) -> None:
    """Append one codepath payload to codepaths.json.

    Order matters: load_both (which validates the existing on-disk state)
    runs first, then payload validation against the live architecture, and
    only then the append + write_atomic. A bad payload — duplicate id,
    bad cross-ref, schema violation — never touches disk, matching the
    validate-before-write discipline from `cmd_set_architecture`.
    """
    dir_ = Path(args.dir)
    arch, cps = load_both(dir_)
    payload = read_json_arg(args.json)
    existing_ids = {cp["id"] for cp in cps["codepaths"]}
    _validate_single_codepath(payload, arch, existing_ids)
    cps["codepaths"].append(payload)
    write_atomic(dir_ / CODEPATHS_FILE, cps)


def cmd_update_codepath(args: argparse.Namespace) -> None:
    """Replace one codepath in place by id.

    Two guards before the in-place write: (a) `--id` must match `payload.id`
    — mismatched ids surface as `CliError(code=11)` so an agent that
    typo'd one of the two doesn't silently re-key the codepath; (b) the
    target id must already exist, else `CliError(code=10)`. When we
    validate the payload, the existing-id set EXCLUDES the index being
    replaced — otherwise `_validate_single_codepath` would flag the
    payload's own id as a duplicate (it shares the on-disk id by design).
    Same validate-before-write discipline as `cmd_add_codepath`.
    """
    dir_ = Path(args.dir)
    arch, cps = load_both(dir_)
    payload = read_json_arg(args.json)
    if not isinstance(payload, dict) or payload.get("id") != args.id:
        raise CliError(
            f"--id ({args.id!r}) must match payload.id ({payload.get('id') if isinstance(payload, dict) else None!r})",
            code=11,
        )
    for i, cp in enumerate(cps["codepaths"]):
        if cp["id"] == args.id:
            other_ids = {c["id"] for j, c in enumerate(cps["codepaths"]) if j != i}
            _validate_single_codepath(payload, arch, other_ids)
            cps["codepaths"][i] = payload
            write_atomic(dir_ / CODEPATHS_FILE, cps)
            return
    raise CliError(f"codepath id {args.id!r} not found", code=10)


def cmd_remove_codepath(args: argparse.Namespace) -> None:
    """Delete one codepath by id.

    `load_both` first so a corrupt on-disk file fails loudly instead of
    being silently rewritten short one entry. Unknown id raises
    `CliError(code=10)` — same not-found semantics as
    `cmd_update_codepath`, so callers can branch on exit code alone.
    """
    dir_ = Path(args.dir)
    _, cps = load_both(dir_)
    for i, cp in enumerate(cps["codepaths"]):
        if cp["id"] == args.id:
            del cps["codepaths"][i]
            write_atomic(dir_ / CODEPATHS_FILE, cps)
            return
    raise CliError(f"codepath id {args.id!r} not found", code=10)


def cmd_list(args: argparse.Namespace) -> None:
    """Print a JSON array of entries for the requested `--kind`.

    Loads both architecture and codepaths via `load_both` so cross-ref
    validation runs (codepaths reference component ids) — the read verb
    behaves like the write verbs in trusting the on-disk state only after
    full validation. A corrupt file therefore surfaces as the appropriate
    schema/cross-ref/JSON exit code rather than as a silently-truncated
    list. Output is `json.dumps(..., indent=2, ensure_ascii=False)` to
    stdout, matching the docstring contract in the task description.
    """
    dir_ = Path(args.dir)
    arch, cps = load_both(dir_)
    if args.kind == "components":
        out: list = arch["components"]
    elif args.kind == "categories":
        out = arch["categories"]
    elif args.kind == "edges":
        out = arch["edges"]
    else:  # "codepaths"
        out = cps["codepaths"]
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_get(args: argparse.Namespace) -> None:
    """Print one entry by id (or composite `from->to` for edges).

    Edges have no id field — they're keyed by `from`/`to` pairs — so
    `get --kind edges --id <a>-><b>` matches on the composite. Every
    other kind matches on `item["id"]`. Not-found raises
    `CliError(code=10)`, matching the not-found semantics in
    `cmd_update_codepath` / `cmd_remove_codepath` so callers can branch
    on exit code alone.
    """
    dir_ = Path(args.dir)
    arch, cps = load_both(dir_)
    if args.kind == "components":
        pool: list = arch["components"]
    elif args.kind == "categories":
        pool = arch["categories"]
    elif args.kind == "edges":
        pool = arch["edges"]
    else:  # "codepaths"
        pool = cps["codepaths"]

    if args.kind == "edges":
        for e in pool:
            if f"{e['from']}->{e['to']}" == args.id:
                print(json.dumps(e, indent=2, ensure_ascii=False))
                return
    else:
        for item in pool:
            if item["id"] == args.id:
                print(json.dumps(item, indent=2, ensure_ascii=False))
                return
    raise CliError(f"{args.kind} id {args.id!r} not found", code=10)


def cmd_status(args: argparse.Namespace) -> None:
    """Print counts, mtimes, and render-staleness as JSON.

    Cheap precondition gate for the visualizer skill: surfaces "are the
    JSON files OK?" (any schema/JSON/IO error from `load_both` propagates
    with its native exit code — 12 schema, 13 JSON parse, etc.) and "is
    the rendered HTML stale relative to the inputs?" (`renderStale`).
    Stale = HTML missing OR any input JSON has a newer mtime than the
    HTML. Missing inputs don't drive staleness on their own — an empty
    dir with no architecture.json is "stale" only because there's no
    HTML either, and the seeded skeleton has nothing to render anyway.
    Output is `json.dumps(..., indent=2)` so it's both human-readable
    and trivially `jq`-able from the skill wrapper.
    """
    dir_ = Path(args.dir)
    arch, cps = load_both(dir_)
    arch_path = dir_ / ARCH_FILE
    cps_path = dir_ / CODEPATHS_FILE
    html_path = dir_ / "architecture.html"

    def mtime_or_none(p: Path) -> float | None:
        try:
            return p.stat().st_mtime
        except FileNotFoundError:
            return None

    arch_mt = mtime_or_none(arch_path)
    cps_mt = mtime_or_none(cps_path)
    html_mt = mtime_or_none(html_path)

    html_exists = html_mt is not None
    input_mtimes = [t for t in (arch_mt, cps_mt) if t is not None]
    inputs_max = max(input_mtimes) if input_mtimes else None
    render_stale = (not html_exists) or (
        inputs_max is not None and html_mt is not None and html_mt < inputs_max
    )

    out = {
        "app": arch["app"]["name"],
        "categories": len(arch["categories"]),
        "components": len(arch["components"]),
        "edges": len(arch["edges"]),
        "codepaths": len(cps["codepaths"]),
        "archMtime": arch_mt,
        "codepathsMtime": cps_mt,
        "htmlMtime": html_mt,
        "htmlExists": html_exists,
        "renderStale": render_stale,
        "dir": str(dir_),
    }
    print(json.dumps(out, indent=2))


# DISPATCH maps subcommand string to handler. Subsequent tasks register
# their cmd_* handlers here. main() looks up the handler by `args.cmd`;
# a missing entry surfaces as "not yet implemented" with exit code 2 so
# partial-implementation failures stay distinguishable from real
# schema/IO failures.
DISPATCH: dict[str, Callable[[argparse.Namespace], None]] = {
    "set-architecture": cmd_set_architecture,
    "add-codepath": cmd_add_codepath,
    "update-codepath": cmd_update_codepath,
    "remove-codepath": cmd_remove_codepath,
    "list": cmd_list,
    "get": cmd_get,
    "status": cmd_status,
}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code.

    Routes via DISPATCH on `args.cmd`. Unwired subcommands raise
    `CliError(code=2)` so the bootstrap scaffold reports something
    actionable instead of crashing with a KeyError.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd is None:
        parser.print_help(sys.stderr)
        return 2

    handler = DISPATCH.get(args.cmd)
    try:
        if handler is None:
            raise CliError(
                f"subcommand '{args.cmd}' is not yet implemented in this build",
                code=2,
            )
        handler(args)
    except CliError as e:
        sys.stderr.write(f"{parser.prog}: error: {e}\n")
        return e.code

    return 0


if __name__ == "__main__":
    sys.exit(main())
