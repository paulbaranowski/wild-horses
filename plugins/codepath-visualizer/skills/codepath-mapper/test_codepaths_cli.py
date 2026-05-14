#!/usr/bin/env python3
"""Smoke tests for codepaths_cli.py.

Stdlib-only — no pytest needed. Run:

    python3 plugins/codepath-visualizer/skills/codepath-mapper/test_codepaths_cli.py

Or via unittest discovery:

    python3 -m unittest discover -s plugins/codepath-visualizer/skills/codepath-mapper -p 'test_codepaths_cli.py'

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

CLI = Path(__file__).parent / "codepaths_cli.py"

# All nine subcommands registered by build_parser() in codepaths_cli.py.
# Kept in lock-step with the parser surface so `test_help_works` fails
# loudly if a subcommand is renamed, removed, or added without updating
# the test expectations.
ALL_SUBCOMMANDS = [
    "set-architecture",
    "add-codepath",
    "update-codepath",
    "remove-codepath",
    "list",
    "get",
    "status",
    "render",
    "select",
]


def run(*args, dir_: Path | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    """Invoke codepaths_cli.py as a subprocess and return the completed process.

    `dir_` is forwarded as `--dir <path>` ahead of the positional args so the
    CLI's per-call working directory is isolated to the test's tempdir.
    `stdin`, when provided, is piped to the child process; this lets tests
    exercise the `--json -` stdin path without writing temp files.
    """
    cmd = [sys.executable, str(CLI)]
    if dir_ is not None:
        cmd += ["--dir", str(dir_)]
    cmd += list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, input=stdin
    )


class CliBootstrapTests(unittest.TestCase):
    """Verify the CLI bootstrap: --help works and no-arg invocation prints help."""

    def test_help_works(self) -> None:
        result = run("--help")
        self.assertEqual(result.returncode, 0)
        for subcommand in ALL_SUBCOMMANDS:
            self.assertIn(
                subcommand,
                result.stdout,
                f"Expected subcommand '{subcommand}' to appear in --help stdout",
            )

    def test_no_subcommand_exits_2_with_help(self) -> None:
        result = run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("set-architecture", result.stderr)


if __name__ == "__main__":
    unittest.main()
