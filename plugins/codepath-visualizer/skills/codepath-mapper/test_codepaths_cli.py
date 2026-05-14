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
import time
import unittest
from pathlib import Path

CLI = Path(__file__).parent / "codepaths_cli.py"

# Allow direct (in-process) imports from codepaths_cli for tests that target
# pure functions — subprocess invocations still go via `run(...)` below.
# merge_selected_codepath is a pure function (no IO, no global state), so
# SelectMergeTests imports it directly rather than paying the subprocess
# cost per case. CliError is imported alongside so the not-found case can
# assertRaises on the typed exception.
sys.path.insert(0, str(CLI.parent))
from codepaths_cli import CliError, merge_selected_codepath  # noqa: E402  # pyright: ignore[reportMissingImports]

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


class ListGetTests(unittest.TestCase):
    """Exercise `list` and `get` end-to-end via the CLI subprocess.

    setUp writes BOTH `architecture.json` (via valid_arch_with_components)
    and `codepaths.json` (via valid_codepaths) so the cross-ref validator
    inside load_both has its source — list/get both call load_both, which
    requires both files to be present and mutually consistent.

    Each happy-path test parses the CLI's stdout as JSON via json.loads
    and asserts structural content (ids, labels) — not stdout substring
    matches — so the contract is "list/get print a JSON document" not
    "list/get print a particular textual format". The unknown-id case
    asserts exit 10, the documented not-found code shared with
    cmd_update_codepath / cmd_remove_codepath.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        (self.tmp_dir / "architecture.json").write_text(
            json.dumps(valid_arch_with_components())
        )
        (self.tmp_dir / "codepaths.json").write_text(
            json.dumps(valid_codepaths())
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_list_components(self) -> None:
        result = run("list", "--kind", "components", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads(result.stdout)
        self.assertEqual([c["id"] for c in data], ["web", "srv"])

    def test_list_codepaths(self) -> None:
        result = run("list", "--kind", "codepaths", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads(result.stdout)
        self.assertEqual([cp["id"] for cp in data], ["make-request"])

    def test_list_categories(self) -> None:
        result = run("list", "--kind", "categories", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads(result.stdout)
        self.assertEqual([c["id"] for c in data], ["ui", "api"])

    def test_list_edges(self) -> None:
        result = run("list", "--kind", "edges", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads(result.stdout)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["from"], "web")
        self.assertEqual(data[0]["to"], "srv")

    def test_get_component(self) -> None:
        result = run(
            "get", "--kind", "components", "--id", "web",
            dir_=self.tmp_dir,
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads(result.stdout)
        self.assertEqual(data["label"], "Web app")

    def test_get_unknown_fails_10(self) -> None:
        result = run(
            "get", "--kind", "components", "--id", "ghost",
            dir_=self.tmp_dir,
        )
        self.assertEqual(result.returncode, 10)


class StatusTests(unittest.TestCase):
    """Exercise the `status` verb end-to-end via the CLI subprocess.

    The status verb's value is a cheap precondition gate for the
    visualizer skill — "are the JSON files OK + is the rendered HTML
    stale relative to the inputs?" — so the tests cover both axes:
    schema validity (corrupt arch → exit 12) and freshness
    (renderStale flips with mtime ordering between the JSONs and
    architecture.html). Each freshness test uses time.sleep(0.01)
    between writes to guarantee deterministic mtime ordering on
    filesystems whose stat resolution is coarse enough to otherwise
    collapse "before" and "after" writes onto the same timestamp.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_status_on_empty_dir(self) -> None:
        result = run("status", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads(result.stdout)
        self.assertEqual(data["categories"], 6)  # default seed
        self.assertEqual(data["components"], 0)
        self.assertEqual(data["codepaths"], 0)
        self.assertFalse(data["htmlExists"])
        self.assertTrue(data["renderStale"])

    def test_status_html_fresh(self) -> None:
        (self.tmp_dir / "architecture.json").write_text(
            json.dumps(valid_arch_with_components())
        )
        (self.tmp_dir / "codepaths.json").write_text(
            json.dumps(valid_codepaths())
        )
        # Make HTML newer than both JSONs.
        time.sleep(0.01)
        (self.tmp_dir / "architecture.html").write_text("<html></html>")
        result = run("status", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads(result.stdout)
        self.assertTrue(data["htmlExists"])
        self.assertFalse(data["renderStale"])

    def test_status_html_stale(self) -> None:
        (self.tmp_dir / "architecture.html").write_text("<html></html>")
        time.sleep(0.01)
        (self.tmp_dir / "architecture.json").write_text(
            json.dumps(valid_arch_with_components())
        )
        (self.tmp_dir / "codepaths.json").write_text(
            json.dumps(valid_codepaths())
        )
        result = run("status", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        data = json.loads(result.stdout)
        self.assertTrue(data["renderStale"])

    def test_status_exit_12_on_corrupt_arch(self) -> None:
        (self.tmp_dir / "architecture.json").write_text("[1,2,3]")
        result = run("status", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 12)


class RenderTests(unittest.TestCase):
    """Exercise `render` end-to-end via the CLI subprocess.

    setUp writes BOTH valid architecture.json and codepaths.json into the
    tempdir so load_both inside cmd_render has its source. The canonical
    invariant under test is "substitution actually happened" — the output
    HTML must NOT contain the literal placeholders (__APP_NAME__, __DATA__)
    that template.html ships with. If a future refactor accidentally
    skipped the .replace() chain (or renamed a placeholder), the test
    would still see a file on disk, so the substring assertions on
    placeholders are what catches that silent failure.

    test_render_fails_12_on_corrupt_inputs is the canonical regression for
    the validate-before-write discipline in cmd_render: a corrupt
    architecture.json must surface as exit 12 AND leave the output HTML
    absent — same contract as cmd_set_architecture's
    test_invalid_arch_fails_12.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        (self.tmp_dir / "architecture.json").write_text(
            json.dumps(valid_arch_with_components())
        )
        (self.tmp_dir / "codepaths.json").write_text(
            json.dumps(valid_codepaths())
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_render_writes_html(self) -> None:
        result = run("render", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        output = self.tmp_dir / "architecture.html"
        self.assertTrue(output.exists())
        html = output.read_text(encoding="utf-8")
        # Content actually substituted in (app name + component + codepath id).
        self.assertIn("Demo", html)
        self.assertIn("web", html)
        self.assertIn("make-request", html)
        # Substitution actually happened — placeholders are GONE.
        self.assertNotIn("__APP_NAME__", html)
        self.assertNotIn("__DATA__", html)

    def test_render_custom_output(self) -> None:
        custom = self.tmp_dir / "sub" / "out.html"
        result = run("render", "--output", str(custom), dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        self.assertTrue(custom.exists())
        # Default path must NOT have been written when --output is given.
        self.assertFalse((self.tmp_dir / "architecture.html").exists())

    def test_render_fails_12_on_corrupt_inputs(self) -> None:
        # Corrupt arch.json — top-level not an object (schema violation).
        (self.tmp_dir / "architecture.json").write_text("[1,2,3]")
        output = self.tmp_dir / "architecture.html"
        self.assertFalse(output.exists())
        result = run("render", dir_=self.tmp_dir)
        self.assertEqual(result.returncode, 12)
        self.assertFalse(
            output.exists(),
            msg="architecture.html must NOT exist after corrupt input — "
                "validate-before-write discipline violated",
        )


class SelectMergeTests(unittest.TestCase):
    """Exercise `merge_selected_codepath` directly (in-process).

    Unlike the subprocess-driven tests above, these import the helper
    directly because it's a pure function (no IO, no global state) —
    the dispatched-agent invocation pattern doesn't apply here, and
    paying the subprocess cost per case would be wasted overhead.

    test_merge_inlines_referenced_components is the canonical regression
    for the "filter components by step references, preserve arch order"
    contract — downstream renderers depend on the architecture-order
    invariant for layout stability, so the assertion checks the exact
    id sequence (`["web", "srv"]`) and not just set-equality.

    test_merge_unknown_id_raises locks in the exit-code 10 contract
    shared with cmd_update_codepath / cmd_remove_codepath / cmd_get —
    callers can branch on the typed exception's `.code` attribute alone
    without parsing stderr.
    """

    def test_merge_inlines_referenced_components(self) -> None:
        arch = valid_arch_with_components()
        cps = valid_codepaths()
        merged = merge_selected_codepath("make-request", arch, cps)
        self.assertEqual(merged["codepath"]["id"], "make-request")
        # web + srv are referenced by the step's from/to; assertion checks
        # the order matches arch.components (architecture-author order),
        # not the step traversal order, per the helper's docstring.
        self.assertEqual([c["id"] for c in merged["components"]], ["web", "srv"])

    def test_merge_unknown_id_raises(self) -> None:
        arch = valid_arch_with_components()
        cps = valid_codepaths()
        with self.assertRaises(CliError) as ctx:
            merge_selected_codepath("ghost", arch, cps)
        self.assertEqual(ctx.exception.code, 10)


if __name__ == "__main__":
    unittest.main()
