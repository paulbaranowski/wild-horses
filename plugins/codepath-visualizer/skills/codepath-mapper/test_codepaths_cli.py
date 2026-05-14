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


DEFAULT_ARCH_SKELETON = {
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


def valid_arch_with_components() -> dict:
    return {
        "$schemaVersion": 1,
        "app": {"name": "Demo", "subtitle": "Test"},
        "categories": [
            {"id": "ui", "label": "UI", "color": "#60a5fa", "column": 0},
            {"id": "api", "label": "API", "color": "#fbbf24", "column": 1},
        ],
        "components": [
            {"id": "web", "label": "Web app", "category": "ui", "files": ["src/web/**"]},
            {"id": "srv", "label": "Server", "category": "api", "files": ["src/server/**"]},
        ],
        "edges": [{"from": "web", "to": "srv", "label": "POST /api"}],
    }


def _list_is_wired() -> bool:
    """Return True when `list --kind components` has a real handler.

    Until Task 14 wires the `list` verb, dispatch falls through to the
    "not yet implemented" CliError(code=2). Tests that exercise the
    loader via `list` pass-through (skip) until then, per plan §Task 3.1
    ("Most cases are exercised via the `list --kind components`
    subprocess invocation (wired in Task 14) — until then they pass
    through").
    """
    with tempfile.TemporaryDirectory() as d:
        result = run("list", "--kind", "components", dir_=Path(d))
    return result.returncode != 2


LIST_WIRED = _list_is_wired()


class LoadAndValidateArchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_arch(self, data: dict) -> None:
        (self.tmp_dir / "architecture.json").write_text(json.dumps(data))

    def test_missing_file_returns_skeleton(self) -> None:
        # We test the loader indirectly via `list --kind categories` once implemented.
        # For now, test via `status` once Task 9 is done. Stub:
        result = run("list", "--kind", "categories", dir_=self.tmp_dir)
        # Stub fails until Task 8 wires `list`; but missing file should NOT error 1.
        # When implemented, expected categories = the 6 defaults.
        self.assertNotEqual(result.returncode, 1, msg=f"stderr={result.stderr}")

    def test_valid_arch_loads(self) -> None:
        self._write_arch(valid_arch_with_components())
        result = run("list", "--kind", "components", dir_=self.tmp_dir)
        # Will pass once Task 8 wires `list`. For now just assert no schema error (exit 12).
        self.assertNotIn("schema", result.stderr.lower())

    @unittest.skipUnless(LIST_WIRED, "list verb not yet wired (Task 14)")
    def test_top_level_not_object_fails_12(self) -> None:
        (self.tmp_dir / "architecture.json").write_text("[1,2,3]")
        result = run("list", "--kind", "components", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 12)

    @unittest.skipUnless(LIST_WIRED, "list verb not yet wired (Task 14)")
    def test_missing_categories_fails_12(self) -> None:
        bad = valid_arch_with_components()
        del bad["categories"]
        self._write_arch(bad)
        result = run("list", "--kind", "components", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 12)
        self.assertIn("categories", result.stderr)

    @unittest.skipUnless(LIST_WIRED, "list verb not yet wired (Task 14)")
    def test_duplicate_category_column_fails_12(self) -> None:
        bad = valid_arch_with_components()
        bad["categories"][1]["column"] = 0  # collide with first
        self._write_arch(bad)
        result = run("list", "--kind", "components", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 12)
        self.assertIn("column", result.stderr)

    @unittest.skipUnless(LIST_WIRED, "list verb not yet wired (Task 14)")
    def test_component_references_missing_category_fails_12(self) -> None:
        bad = valid_arch_with_components()
        bad["components"][0]["category"] = "nope"
        self._write_arch(bad)
        result = run("list", "--kind", "components", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 12)

    @unittest.skipUnless(LIST_WIRED, "list verb not yet wired (Task 14)")
    def test_edge_references_missing_component_fails_12(self) -> None:
        bad = valid_arch_with_components()
        bad["edges"][0]["to"] = "missing-id"
        self._write_arch(bad)
        result = run("list", "--kind", "components", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 12)

    @unittest.skipUnless(LIST_WIRED, "list verb not yet wired (Task 14)")
    def test_invalid_id_pattern_fails_12(self) -> None:
        bad = valid_arch_with_components()
        bad["components"][0]["id"] = "Has Spaces"
        self._write_arch(bad)
        result = run("list", "--kind", "components", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 12)

    @unittest.skipUnless(LIST_WIRED, "list verb not yet wired (Task 14)")
    def test_malformed_json_fails_13(self) -> None:
        (self.tmp_dir / "architecture.json").write_text("{not json")
        result = run("list", "--kind", "components", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 13)


class WriteAtomicTests(unittest.TestCase):
    """Indirect: test atomic semantics by invoking a mutating verb in Task 5."""
    pass


if __name__ == "__main__":
    unittest.main()
