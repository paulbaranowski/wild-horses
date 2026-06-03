#!/usr/bin/env python3
"""Shared test harness for the plan_keeper package test suite.

Holds the subprocess/in-process invocation helpers and the isolated-$HOME
base class every test_*.py module in this directory imports. The former
monolithic test_plan_keeper_cli.py was split into per-module files so each
test file aligns with the source module it exercises; this is their common
preamble.

Run the whole suite:

    python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import os
import subprocess
import sys as _sys
import tempfile
import unittest
from pathlib import Path

# The package and entry shim live one directory up (scripts/). The shim adds
# that dir to sys.path when run as a subprocess; we replicate it here so the
# in-process tests can `import plan_keeper.*`.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
CLI = _SCRIPTS_DIR / "plan_keeper_cli.py"
ALLOW_SCRIPT = _SCRIPTS_DIR / "plan-keeper-cli-allow.sh"

_sys.path.insert(0, str(_SCRIPTS_DIR))
from plan_keeper import cli as _cli_module  # noqa: E402
from plan_keeper import storage  # noqa: E402


def _import_cli_module():
    """Return the plan_keeper.cli module — the flat public surface tests use.

    cli.py imports every domain symbol the in-process tests reference
    (push_subcommand, groundcrew_id, save_config, the linear_*/jira_* clients,
    write_atomic, …), so tests keep a single `cli.<name>` namespace despite the
    split into modules. PLAN_ROOT is the one deliberate exception: it is NOT a
    cli attribute. Tests that relocate the plans root patch `storage.PLAN_ROOT`
    (the single source of truth every module resolves through), which also
    isolates them from the real ~/plans/ tree.
    """
    return _cli_module


def run_cli(
    *args: str,
    stdin: str = "",
    home: Path,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the CLI with isolated $HOME so it can't touch real ~/plans/."""
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        ["python3", str(CLI), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
        timeout=10,
    )


def run_allow(cmd: str) -> str:
    """Pipe a fake PreToolUse JSON to the allow-script; return its stdout."""
    payload = json.dumps({"tool_input": {"command": cmd}})
    result = subprocess.run(
        ["bash", str(ALLOW_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout


class IsolatedHomeTestCase(unittest.TestCase):
    """Each test gets a fresh $HOME pointing at a tempdir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.plans_root = self.home / "plans"
        # Use a non-git cwd so derive_repo's git path is a clean miss.
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()
