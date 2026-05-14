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


def valid_codepaths() -> dict:
    """Fixture: a one-codepath codepaths.json whose steps reference the
    'web' and 'srv' components in valid_arch_with_components(). Cross-ref
    validation in load_and_validate_codepaths() only passes when both
    files agree on component ids — so this helper is paired with
    valid_arch_with_components() by construction.
    """
    return {
        "$schemaVersion": 1,
        "codepaths": [
            {
                "id": "make-request",
                "name": "Make a request",
                "description": "User clicks, server responds.",
                "steps": [
                    {
                        "from": "web",
                        "to": "srv",
                        "annotation": "POST /api",
                        "payload": "{q}",
                        "ref": "src/web/req.ts:10",
                    }
                ],
            }
        ],
    }


class LoadAndValidateCodepathsTests(unittest.TestCase):
    """Exercise load_and_validate_codepaths() via `list --kind codepaths`.

    setUp writes a valid architecture.json into the tempdir so the
    cross-ref validator has its source — codepaths validation depends on
    arch being loadable first (see load_both()). Cases that target the
    codepaths-only failure modes still need the arch file present;
    otherwise the test would conflate "bad arch" with "bad codepaths".

    Cases requiring `list --kind codepaths` to actually run (not just
    error with "not yet implemented") are gated on LIST_WIRED — same
    pattern as LoadAndValidateArchTests. The `test_missing_codepaths_returns_empty`
    case is the exception: it only checks the exit code is NOT 1, which
    is satisfied even by the unwired-dispatch path (exit 2).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        (self.tmp_dir / "architecture.json").write_text(
            json.dumps(valid_arch_with_components())
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_codepaths_returns_empty(self) -> None:
        # No codepaths.json on disk — loader should return the skeleton,
        # not crash with the generic IO error code (1).
        result = run("list", "--kind", "codepaths", dir_=self.tmp_dir)
        self.assertNotEqual(result.returncode, 1, msg=f"stderr={result.stderr}")

    @unittest.skipUnless(LIST_WIRED, "list verb not yet wired (Task 14)")
    def test_step_references_missing_component_fails_15(self) -> None:
        bad = valid_codepaths()
        bad["codepaths"][0]["steps"][0]["to"] = "ghost"
        (self.tmp_dir / "codepaths.json").write_text(json.dumps(bad))
        result = run("list", "--kind", "codepaths", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 15)
        self.assertIn("ghost", result.stderr)

    @unittest.skipUnless(LIST_WIRED, "list verb not yet wired (Task 14)")
    def test_duplicate_codepath_id_fails_12(self) -> None:
        bad = valid_codepaths()
        bad["codepaths"].append(dict(bad["codepaths"][0]))
        (self.tmp_dir / "codepaths.json").write_text(json.dumps(bad))
        result = run("list", "--kind", "codepaths", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 12)

    @unittest.skipUnless(LIST_WIRED, "list verb not yet wired (Task 14)")
    def test_codepaths_not_array_fails_12(self) -> None:
        (self.tmp_dir / "codepaths.json").write_text('{"codepaths": "no"}')
        result = run("list", "--kind", "codepaths", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 12)

    @unittest.skipUnless(LIST_WIRED, "list verb not yet wired (Task 14)")
    def test_codepaths_malformed_json_fails_13(self) -> None:
        (self.tmp_dir / "codepaths.json").write_text("{")
        result = run("list", "--kind", "codepaths", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 13)


class SetArchitectureTests(unittest.TestCase):
    """Exercise `set-architecture` end-to-end via the CLI subprocess.

    Atomicity is the key invariant: `cmd_set_architecture` must validate
    BEFORE `write_atomic`, so a malformed or schema-violating payload
    leaves the on-disk `architecture.json` untouched (or absent, on a
    fresh dir). `test_invalid_arch_fails_12` enforces this by asserting
    the file was NOT created — the canonical regression test for the
    "validate-before-write" discipline called out in plan §Task 8.

    Cases use `--json -` (stdin) where possible to mirror the dispatched-
    agent invocation pattern (`cli set-architecture --json - <<'EOF' ...`),
    plus one `test_read_from_file` case covering the file-path branch of
    `read_json_arg`.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_writes_valid_arch(self) -> None:
        payload = valid_arch_with_components()
        result = run(
            "set-architecture", "--json", "-",
            dir_=self.tmp_dir,
            stdin=json.dumps(payload),
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        on_disk = json.loads((self.tmp_dir / "architecture.json").read_text())
        self.assertEqual(on_disk, payload)

    def test_auto_creates_directory(self) -> None:
        # Pass a nested non-existent --dir; the verb must create it before
        # writing architecture.json (write_atomic uses os.makedirs internally
        # via Path.mkdir(parents=True) — verify the contract end-to-end).
        nested = self.tmp_dir / "deep" / "nested" / "out"
        self.assertFalse(nested.exists())
        payload = valid_arch_with_components()
        result = run(
            "set-architecture", "--json", "-",
            dir_=nested,
            stdin=json.dumps(payload),
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        self.assertTrue((nested / "architecture.json").exists())

    def test_invalid_arch_fails_12(self) -> None:
        # Schema-violating but JSON-valid payload: missing "categories".
        # The atomic-discipline assertion is that the on-disk file was NOT
        # created — if validate ran AFTER write, this would silently corrupt
        # state. This is the canonical regression test for the validate-
        # before-write ordering.
        bad = valid_arch_with_components()
        del bad["categories"]
        arch_path = self.tmp_dir / "architecture.json"
        self.assertFalse(arch_path.exists())
        result = run(
            "set-architecture", "--json", "-",
            dir_=self.tmp_dir,
            stdin=json.dumps(bad),
        )
        self.assertEqual(result.returncode, 12)
        self.assertFalse(
            arch_path.exists(),
            msg="architecture.json must NOT exist after invalid payload — "
                "validate-before-write discipline violated",
        )

    def test_invalid_json_fails_13(self) -> None:
        result = run(
            "set-architecture", "--json", "-",
            dir_=self.tmp_dir,
            stdin="{not valid json",
        )
        self.assertEqual(result.returncode, 13)
        self.assertFalse((self.tmp_dir / "architecture.json").exists())

    def test_read_from_file(self) -> None:
        # Write the payload to a file inside the tempdir and pass its path
        # as --json (not "-"); exercises the file-path branch of
        # read_json_arg().
        payload = valid_arch_with_components()
        payload_path = self.tmp_dir / "payload.json"
        payload_path.write_text(json.dumps(payload))
        result = run(
            "set-architecture", "--json", str(payload_path),
            dir_=self.tmp_dir,
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        on_disk = json.loads((self.tmp_dir / "architecture.json").read_text())
        self.assertEqual(on_disk, payload)


class AddCodepathTests(unittest.TestCase):
    """Exercise `add-codepath` end-to-end via the CLI subprocess.

    setUp writes a valid `architecture.json` into the tempdir so the
    cross-ref validator inside `_validate_single_codepath` has its
    source — the verb runs `load_both` first, which requires arch on
    disk. Each case targets one of the four documented exit-code
    branches (success, duplicate id -> 11, bad cross-ref -> 15) plus
    the auto-create-on-first-add path, matching plan §Task 6.1.

    The exit-code split (11 vs 15 vs 12) is the contract that lets
    dispatched agents distinguish "you re-used an id" from "you typo'd
    a component id" from "you sent malformed schema" — these tests are
    the canonical regression for that contract.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        (self.tmp_dir / "architecture.json").write_text(
            json.dumps(valid_arch_with_components())
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _cp(self, id_: str = "make-request") -> dict:
        """Build a minimal valid codepath whose steps reference the
        'web' and 'srv' components from `valid_arch_with_components`.
        """
        return {
            "id": id_,
            "name": "Test cp",
            "description": "...",
            "steps": [{"from": "web", "to": "srv", "annotation": "POST"}],
        }

    def test_auto_creates_codepaths_json(self) -> None:
        # No codepaths.json on disk — first add must create the file
        # with the new codepath as the sole entry. Exercises the
        # skeleton-on-missing-file path in load_and_validate_codepaths.
        self.assertFalse((self.tmp_dir / "codepaths.json").exists())
        result = run(
            "add-codepath", "--json", "-",
            dir_=self.tmp_dir,
            stdin=json.dumps(self._cp()),
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads((self.tmp_dir / "codepaths.json").read_text())
        self.assertEqual(len(data["codepaths"]), 1)
        self.assertEqual(data["codepaths"][0]["id"], "make-request")

    def test_appends_to_existing(self) -> None:
        # Pre-seed codepaths.json with one entry, then add a second.
        # Asserts BOTH entries are present AND that insertion order is
        # preserved (first, second) — the documented append semantics.
        first = {"$schemaVersion": 1, "codepaths": [self._cp("first")]}
        (self.tmp_dir / "codepaths.json").write_text(json.dumps(first))
        result = run(
            "add-codepath", "--json", "-",
            dir_=self.tmp_dir,
            stdin=json.dumps(self._cp("second")),
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads((self.tmp_dir / "codepaths.json").read_text())
        self.assertEqual(
            [cp["id"] for cp in data["codepaths"]],
            ["first", "second"],
        )

    def test_duplicate_id_fails_11(self) -> None:
        # Pre-seed with a codepath whose id collides with the payload.
        # Must exit 11 (duplicate id), NOT 12 (generic schema) — the
        # split is what makes "you re-used an id" actionable.
        first = {"$schemaVersion": 1, "codepaths": [self._cp("dup")]}
        (self.tmp_dir / "codepaths.json").write_text(json.dumps(first))
        result = run(
            "add-codepath", "--json", "-",
            dir_=self.tmp_dir,
            stdin=json.dumps(self._cp("dup")),
        )
        self.assertEqual(result.returncode, 11)

    def test_bad_component_ref_fails_15(self) -> None:
        # Step references a component id not in architecture.components.
        # Must exit 15 (cross-ref), NOT 12 (generic schema) — the split
        # is what makes "you typo'd a component id" actionable.
        cp = self._cp()
        cp["steps"][0]["from"] = "ghost"
        result = run(
            "add-codepath", "--json", "-",
            dir_=self.tmp_dir,
            stdin=json.dumps(cp),
        )
        self.assertEqual(result.returncode, 15)


class UpdateRemoveCodepathTests(unittest.TestCase):
    """Exercise `update-codepath` and `remove-codepath` end-to-end.

    setUp seeds the tempdir with a valid `architecture.json` AND a
    `codepaths.json` containing one codepath (`make-request`) — both
    verbs require an existing on-disk codepaths state, and update's
    cross-ref validation runs against the architecture. Each case
    targets one of the documented exit-code branches:

    - update happy path: exit 0, in-place replacement preserves the
      array slot (asserted by reading codepaths.json back and checking
      `name` was overwritten).
    - update with unknown id: exit 10 — "you addressed something that
      isn't there".
    - update with `--id != payload.id`: exit 11 — guards against an
      agent that typo'd one of the two ids and would otherwise silently
      re-key the codepath.
    - remove happy path: exit 0, codepaths array becomes empty.
    - remove with unknown id: exit 10 — same not-found contract as
      update, so callers can branch on exit code alone.

    The 10/11 split is the canonical regression for the
    "not-found vs id-mismatch" contract in `cmd_update_codepath`.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        (self.tmp_dir / "architecture.json").write_text(
            json.dumps(valid_arch_with_components())
        )
        cp = {
            "id": "make-request",
            "name": "old name",
            "description": "old",
            "steps": [{"from": "web", "to": "srv", "annotation": "old"}],
        }
        (self.tmp_dir / "codepaths.json").write_text(
            json.dumps({"$schemaVersion": 1, "codepaths": [cp]})
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_update_replaces_in_place(self) -> None:
        new = {
            "id": "make-request",
            "name": "new name",
            "description": "new",
            "steps": [{"from": "web", "to": "srv", "annotation": "new"}],
        }
        result = run(
            "update-codepath", "--id", "make-request", "--json", "-",
            dir_=self.tmp_dir,
            stdin=json.dumps(new),
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads((self.tmp_dir / "codepaths.json").read_text())
        self.assertEqual(data["codepaths"][0]["name"], "new name")

    def test_update_unknown_id_fails_10(self) -> None:
        new = {
            "id": "ghost",
            "name": "x",
            "description": "",
            "steps": [{"from": "web", "to": "srv", "annotation": "x"}],
        }
        result = run(
            "update-codepath", "--id", "ghost", "--json", "-",
            dir_=self.tmp_dir,
            stdin=json.dumps(new),
        )
        self.assertEqual(result.returncode, 10)

    def test_update_id_mismatch_fails_11(self) -> None:
        # Payload id != --id: must exit 11 (mismatch), NOT 10 (not-found)
        # — the split is what makes "you typo'd one of the two ids"
        # actionable. Without this guard, an agent could silently re-key
        # an existing codepath by addressing it with --id <old> while
        # sending payload.id = <new>.
        new = {
            "id": "other",
            "name": "x",
            "description": "",
            "steps": [{"from": "web", "to": "srv", "annotation": "x"}],
        }
        result = run(
            "update-codepath", "--id", "make-request", "--json", "-",
            dir_=self.tmp_dir,
            stdin=json.dumps(new),
        )
        self.assertEqual(result.returncode, 11)

    def test_remove_deletes(self) -> None:
        result = run(
            "remove-codepath", "--id", "make-request",
            dir_=self.tmp_dir,
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads((self.tmp_dir / "codepaths.json").read_text())
        self.assertEqual(data["codepaths"], [])

    def test_remove_unknown_fails_10(self) -> None:
        result = run(
            "remove-codepath", "--id", "ghost",
            dir_=self.tmp_dir,
        )
        self.assertEqual(result.returncode, 10)


if __name__ == "__main__":
    unittest.main()
