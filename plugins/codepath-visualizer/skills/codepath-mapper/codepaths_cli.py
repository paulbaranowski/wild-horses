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
import sys
from typing import Callable

ARCH_FILE = "architecture.json"
CODEPATHS_FILE = "codepaths.json"
DEFAULT_DIR = "docs/codepaths"
ID_PATTERN = r"^[a-z0-9-]+$"


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


# DISPATCH maps subcommand string to handler. Bootstrap leaves it empty;
# subsequent tasks register their cmd_* handlers here. main() looks up
# the handler by `args.cmd`; a missing entry surfaces as "not yet
# implemented" with exit code 2 so partial-implementation failures stay
# distinguishable from real schema/IO failures.
DISPATCH: dict[str, Callable[[argparse.Namespace], None]] = {}


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
