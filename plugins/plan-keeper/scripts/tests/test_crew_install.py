#!/usr/bin/env python3
"""`crew install` config patcher + orchestration (crew_install.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests

Splits into two layers, matching the module: the pure patcher
(build_patched_config / resolve_config_path) is exercised with plain strings,
and the IO/process orchestration (run_crew_install) is driven with a fake
`crew doctor` runner and an isolated PLAN_ROOT so neither needs a real
groundcrew install on the machine.
"""
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from support import IsolatedHomeTestCase
from plan_keeper import cli
from plan_keeper import storage
from plan_keeper.crew_install import (
    SENTINEL_END,
    SENTINEL_START,
    build_patched_config,
    build_patched_json_config,
    looks_like_json,
    resolve_config_path,
    run_crew_install,
)
from plan_keeper.errors import PlanKeeperCliError

# A minimal but realistic groundcrew config: the `sources:` anchor is present
# and already holds one foreign entry so we can prove the managed region
# coexists with hand-maintained content. `knownRepositories` is present too —
# plan-keeper must leave it untouched.
BASE_CONFIG = """\
import type { Config } from "groundcrew";

export default {
  sources: [
    { kind: "github", name: "issues" },
  ],
  knownRepositories: [
    "existing-repo",
  ],
} satisfies Config;
"""

PK = "/opt/homebrew/bin/plan-keeper"

# A trimmed but faithful copy of what `crew init` generates: `sources:` is
# COMMENTED OUT (the built-in Linear adapter is implicit) and
# `knownRepositories` is NESTED inside `workspace`. This is the config shape
# `crew install` must actually handle, distinct from the idealized BASE_CONFIG.
CREW_INIT_CONFIG = """\
import type { Config } from "@clipboard-health/groundcrew";

export default {
  workspace: {
    projectDir: "~/dev/groundcrew",
    knownRepositories: ["your-org/your-repo"],
  },
  models: { default: "claude", definitions: { claude: {} } },
  // // Additional pluggable ticket sources beyond the implicit Linear adapter.
  // sources: [
  //   { kind: "shell", name: "jira", commands: { fetch: "jira-fetch.sh" } },
  // ],
} satisfies Config;
"""


# A realistic groundcrew JSON config (the shape `crew init` can also emit, and
# what this user actually had). `sources` already holds a foreign `linear`
# entry AND a stale `plankeeper` entry wired to an OLD binary path — proving the
# patcher replaces the named entry in place (idempotent) rather than appending a
# duplicate, and leaves the foreign entry untouched.
JSON_CONFIG = json.dumps(
    {
        "workspace": {"knownRepositories": ["existing-repo"]},
        "sources": [
            {
                "kind": "shell",
                "name": "plankeeper",
                "commands": {"fetch": "/old/plan-keeper crew fetch"},
            },
            {"kind": "linear"},
        ],
    },
    indent=2,
)


class TestBuildPatchedConfig(unittest.TestCase):
    """The pure patcher: anchoring, idempotency, and content."""

    def test_fresh_insert_adds_one_sources_region(self):
        out = build_patched_config(BASE_CONFIG, PK)
        assert out is not None
        # Exactly one managed region — only `sources:` is managed now.
        self.assertEqual(out.count(SENTINEL_START), 1)
        self.assertEqual(out.count(SENTINEL_END), 1)
        # Foreign content is preserved (coexists with the managed region).
        self.assertIn('{ kind: "github", name: "issues" }', out)

    def test_command_strings_bake_in_resolved_binary(self):
        out = build_patched_config(BASE_CONFIG, PK)
        assert out is not None
        # The shell source is named for the tool (plankeeper), not the ~/plans
        # directory it reads — this is the label groundcrew shows.
        self.assertIn('name: "plankeeper"', out)
        self.assertIn(f'fetch: "{PK} crew fetch"', out)
        self.assertIn(f'verify: "{PK} crew fetch >/dev/null"', out)
        # ${id} is groundcrew's literal token, not interpolated by us.
        self.assertIn(f'resolveOne: "{PK} crew get ${{id}}"', out)
        self.assertIn(f'markInProgress: "{PK} crew start ${{id}}"', out)
        self.assertIn(f'markInReview: "{PK} crew review ${{id}}"', out)
        # markDone archives via the file-meta engine (relocate to done/ +
        # stamp Completed on), addressed by the plan's Plan-keeper Ticket
        # (== groundcrew's ${id}); suffix keeps the unattended hook safe.
        self.assertIn(
            f'markDone: "{PK} file-meta set --ticket ${{id}} --status done '
            f'--on-collision suffix"',
            out,
        )

    def test_grants_plans_dir_to_the_sandbox(self):
        """The shell source declares sandboxWritePaths so groundcrew opens
        ~/plans read+write inside the sandbox; without it a sandboxed agent
        can't reach the plan files this source is built on."""
        out = build_patched_config(BASE_CONFIG, PK)
        assert out is not None
        self.assertIn('sandboxWritePaths: ["~/plans"]', out)

    def test_known_repositories_left_untouched(self):
        """plan-keeper no longer manages knownRepositories: the array and its
        entries must come through the patch byte-identical, with no sentinel."""
        out = build_patched_config(BASE_CONFIG, PK)
        assert out is not None
        # The pre-existing entry survives and no managed region was inserted
        # into the knownRepositories array.
        kr_start = out.index("knownRepositories:")
        self.assertNotIn(SENTINEL_START, out[kr_start:])
        self.assertIn('"existing-repo"', out)

    def test_rerun_is_idempotent(self):
        once = build_patched_config(BASE_CONFIG, PK)
        assert once is not None
        twice = build_patched_config(once, PK)
        assert twice is not None
        self.assertEqual(once, twice)
        # No duplicated regions on re-run.
        self.assertEqual(twice.count(SENTINEL_START), 1)

    def test_rerun_idempotent_on_crew_init_config(self):
        """The created-sources path (commented-out `sources:`) must also be
        idempotent: re-running must not stack a second created array."""
        once = build_patched_config(CREW_INIT_CONFIG, PK)
        assert once is not None
        twice = build_patched_config(once, PK)
        assert twice is not None
        self.assertEqual(once, twice)
        self.assertEqual(twice.count(SENTINEL_START), 1)

    def test_rerun_repoints_binary_path(self):
        once = build_patched_config(BASE_CONFIG, "/old/plan-keeper")
        assert once is not None
        moved = build_patched_config(once, "/new/plan-keeper")
        assert moved is not None
        self.assertIn("/new/plan-keeper crew fetch", moved)
        self.assertNotIn("/old/plan-keeper", moved)

    def test_creates_sources_array_when_only_commented_out(self):
        """crew init comments `sources:` out — anchor it inside the comment and
        the TS breaks. Instead a fresh active `sources` key is created, and the
        commented original is left untouched."""
        out = build_patched_config(CREW_INIT_CONFIG, PK)
        assert out is not None
        self.assertEqual(out.count(SENTINEL_START), 1)  # the created sources
        self.assertIn(f'fetch: "{PK} crew fetch"', out)
        # The commented-out template `sources:` is preserved as-is.
        self.assertIn("//   { kind:", out)
        # knownRepositories nested in workspace is left alone.
        self.assertIn('"your-org/your-repo"', out)

    def test_patches_existing_sources_array(self):
        out = build_patched_config(
            'export default { sources: [] } satisfies Config;', PK
        )
        assert out is not None
        self.assertEqual(out.count(SENTINEL_START), 1)
        self.assertIn(f'fetch: "{PK} crew fetch"', out)

    def test_no_sources_and_no_export_default_returns_none(self):
        """No active sources AND no export object to create one in → safety
        valve (the caller prints the block for manual paste). Absence of a
        knownRepositories array no longer matters."""
        orphan = 'const cfg = { knownRepositories: [] };'
        self.assertIsNone(build_patched_config(orphan, PK))

    def test_active_sources_with_malformed_sentinel_returns_none(self):
        """An active `sources:` array whose managed region is malformed — a
        SENTINEL_START with no matching SENTINEL_END — must fail fast (None),
        not fall through to creating a second `sources` key beside the broken
        one. An `export default {` is present so the create path *would* fire if
        the malformed case weren't caught."""
        malformed = (
            "export default {\n"
            "  sources: [\n"
            f"{SENTINEL_START}\n"
            '      { kind: "shell", name: "plans", commands: {} },\n'
            "  ],\n"
            "} satisfies Config;\n"
        )
        self.assertNotIn(SENTINEL_END, malformed)  # guard: truly unterminated
        self.assertIsNone(build_patched_config(malformed, PK))


class TestLooksLikeJson(unittest.TestCase):
    """The content-based format discriminator (JSON vs TS/JS)."""

    def test_json_object_is_json(self):
        self.assertTrue(looks_like_json(JSON_CONFIG))

    def test_ts_export_default_is_not_json(self):
        self.assertFalse(looks_like_json(BASE_CONFIG))
        self.assertFalse(looks_like_json(CREW_INIT_CONFIG))

    def test_non_object_json_still_counts_as_json(self):
        # A bare array/scalar parses as JSON, so the discriminator routes it to
        # the JSON path — patchability (object with `sources`) is the patcher's
        # job, and routing here is what gets the user the JSON safety-valve block
        # rather than the wrong-format TS sentinel block.
        self.assertTrue(looks_like_json("[1, 2, 3]"))
        self.assertTrue(looks_like_json('"just a string"'))

    def test_empty_or_garbage_is_not_json(self):
        self.assertFalse(looks_like_json(""))
        self.assertFalse(looks_like_json("const cfg = { foo: 1 };"))


class TestBuildPatchedJsonConfig(unittest.TestCase):
    """The JSON patcher: parse → upsert-by-name → re-serialize."""

    def _sources(self, out: str) -> list:
        return json.loads(out)["sources"]

    def test_output_is_valid_json(self):
        out = build_patched_json_config(JSON_CONFIG, PK)
        assert out is not None
        json.loads(out)  # must not raise
        self.assertTrue(out.endswith("\n"))

    def test_replaces_existing_plankeeper_in_place(self):
        """The stale plankeeper entry is overwritten — not duplicated — and its
        binary path is repointed to the freshly-resolved one."""
        out = build_patched_json_config(JSON_CONFIG, PK)
        assert out is not None
        sources = self._sources(out)
        pk_entries = [s for s in sources if s.get("name") == "plankeeper"]
        self.assertEqual(len(pk_entries), 1)
        self.assertEqual(pk_entries[0]["commands"]["fetch"], f"{PK} crew fetch")
        self.assertNotIn("/old/plan-keeper", out)

    def test_foreign_entry_preserved(self):
        out = build_patched_json_config(JSON_CONFIG, PK)
        assert out is not None
        self.assertIn({"kind": "linear"}, self._sources(out))

    def test_command_strings_bake_in_resolved_binary(self):
        out = build_patched_json_config(JSON_CONFIG, PK)
        assert out is not None
        cmds = next(
            s for s in self._sources(out) if s.get("name") == "plankeeper"
        )["commands"]
        self.assertEqual(cmds["fetch"], f"{PK} crew fetch")
        self.assertEqual(cmds["verify"], f"{PK} crew fetch >/dev/null")
        # ${id} is groundcrew's literal token, kept verbatim through JSON.
        self.assertEqual(cmds["resolveOne"], f"{PK} crew get ${{id}}")
        self.assertEqual(cmds["markInProgress"], f"{PK} crew start ${{id}}")
        self.assertEqual(cmds["markInReview"], f"{PK} crew review ${{id}}")
        self.assertEqual(
            cmds["markDone"],
            f"{PK} file-meta set --ticket ${{id}} --status done "
            f"--on-collision suffix",
        )

    def test_grants_plans_dir_to_the_sandbox(self):
        """The JSON shell source carries sandboxWritePaths just like the TS one,
        so a JSON-config install grants ~/plans to the sandbox too."""
        out = build_patched_json_config(JSON_CONFIG, PK)
        assert out is not None
        entry = next(
            s for s in self._sources(out) if s.get("name") == "plankeeper"
        )
        self.assertEqual(entry["sandboxWritePaths"], ["~/plans"])

    def test_creates_sources_when_absent(self):
        out = build_patched_json_config('{"workspace": {}}', PK)
        assert out is not None
        sources = self._sources(out)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["name"], "plankeeper")

    def test_appends_when_no_plankeeper_present(self):
        out = build_patched_json_config('{"sources": [{"kind": "linear"}]}', PK)
        assert out is not None
        names = [s.get("name") for s in self._sources(out)]
        self.assertIn("plankeeper", names)
        self.assertIn({"kind": "linear"}, self._sources(out))

    def test_rerun_is_idempotent(self):
        once = build_patched_json_config(JSON_CONFIG, PK)
        assert once is not None
        twice = build_patched_json_config(once, PK)
        self.assertEqual(once, twice)

    def test_non_object_returns_none(self):
        self.assertIsNone(build_patched_json_config("[1, 2, 3]", PK))

    def test_malformed_sources_returns_none(self):
        # `sources` present but not an array — refuse to silently overwrite.
        self.assertIsNone(
            build_patched_json_config('{"sources": "oops"}', PK)
        )


class TestResolveConfigPath(unittest.TestCase):
    """--config > $GROUNDCREW_CONFIG > first existing crew.config.* in XDG."""

    def test_explicit_config_arg_wins(self):
        p = resolve_config_path("/tmp/x.ts", {"GROUNDCREW_CONFIG": "/env.ts"},
                                Path("/home/u"))
        self.assertEqual(p, Path("/tmp/x.ts"))

    def test_env_used_when_no_arg(self):
        p = resolve_config_path(None, {"GROUNDCREW_CONFIG": "/env.ts"},
                                Path("/home/u"))
        self.assertEqual(p, Path("/env.ts"))

    def test_default_to_ts_when_none_exist(self):
        # Nonexistent home dir → no candidate exists → canonical .ts path, so
        # the caller's "run crew init" error points at the conventional file.
        p = resolve_config_path(None, {}, Path("/home/u"))
        self.assertEqual(
            p, Path("/home/u/.config/groundcrew/crew.config.ts")
        )


class TestResolveConfigPathOnDisk(IsolatedHomeTestCase):
    """Path resolution that depends on which candidate files actually exist."""

    def _cfg_dir(self) -> Path:
        d = self.home / ".config" / "groundcrew"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_finds_json_when_only_json_exists(self):
        cfg = self._cfg_dir() / "crew.config.json"
        cfg.write_text("{}")
        p = resolve_config_path(None, {}, self.home)
        self.assertEqual(p, cfg)

    def test_ts_preferred_over_json_when_both_exist(self):
        d = self._cfg_dir()
        (d / "crew.config.ts").write_text("export default {}")
        (d / "crew.config.json").write_text("{}")
        p = resolve_config_path(None, {}, self.home)
        self.assertEqual(p, d / "crew.config.ts")


class TestRunCrewInstall(IsolatedHomeTestCase):
    """Orchestration: backup, validate, rollback, dry-run, safety valve."""

    def setUp(self) -> None:
        super().setUp()
        self.plans_root.mkdir(parents=True)
        (self.plans_root / "myrepo").mkdir()
        # Assigned (has an Agent) so it survives the unassigned-plan gate and
        # counts toward the "visible to fetch" total the installer reports.
        (self.plans_root / "myrepo" / "2026-01-01-x.md").write_text(
            "---\nAgent: claude\nStatus: todo\n---\n# X\n"
        )
        self._root_patch = patch.object(storage, "PLAN_ROOT", self.plans_root)
        self._root_patch.start()
        self.config = self.home / "crew.config.ts"
        self.config.write_text(BASE_CONFIG)
        self.out = io.StringIO()

    def tearDown(self) -> None:
        self._root_patch.stop()
        super().tearDown()

    def _ok_doctor(self, _config_path: Path) -> "tuple[int, str]":
        return 0, "[ok] config loaded\nall checks passed"

    def _config_broken_doctor(self, _config_path: Path) -> "tuple[int, str]":
        # Doctor couldn't parse the TS — no `config loaded` line.
        return 1, "[--] config: Unexpected token `{`"

    def _env_unhealthy_doctor(self, _config_path: Path) -> "tuple[int, str]":
        # Config is valid (loads) but the environment isn't configured.
        return 1, (
            "[ok] config loaded\n"
            "[--] source linear failed verify: Linear API key not set"
        )

    def test_success_patches_and_keeps_backup(self):
        rc = run_crew_install(
            self.config, dry_run=False, pk=PK,
            run_doctor=self._ok_doctor, out=self.out,
        )
        self.assertEqual(rc, 0)
        patched = self.config.read_text()
        self.assertEqual(patched.count(SENTINEL_START), 1)
        # knownRepositories is left alone — no managed region, entry preserved.
        kr_start = patched.index("knownRepositories:")
        self.assertNotIn(SENTINEL_START, patched[kr_start:])
        self.assertIn('"existing-repo"', patched)
        # Backup holds the pristine original.
        backup = self.config.with_name("crew.config.ts.bak")
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(), BASE_CONFIG)
        # Reports the plan count it can see via fetch.
        self.assertIn("1 plan(s) visible to fetch", self.out.getvalue())

    def test_config_load_failure_rolls_back(self):
        with self.assertRaises(PlanKeeperCliError) as ctx:
            run_crew_install(
                self.config, dry_run=False, pk=PK,
                run_doctor=self._config_broken_doctor, out=self.out,
            )
        self.assertEqual(ctx.exception.code, 1)
        # Config restored to the original; the doctor output is surfaced.
        self.assertEqual(self.config.read_text(), BASE_CONFIG)
        self.assertIn("could not load", str(ctx.exception))

    def test_pre_existing_env_failures_keep_the_patch(self):
        """doctor exits non-zero for env reasons (no Linear key) but the config
        loads → the patch stays, with a warning surfacing doctor's output."""
        rc = run_crew_install(
            self.config, dry_run=False, pk=PK,
            run_doctor=self._env_unhealthy_doctor, out=self.out,
        )
        self.assertEqual(rc, 0)
        # Patch was NOT rolled back.
        self.assertEqual(self.config.read_text().count(SENTINEL_START), 1)
        # User is told doctor still has (unrelated) complaints.
        self.assertIn("unrelated to the plans source", self.out.getvalue())
        self.assertIn("Linear API key", self.out.getvalue())

    def test_dry_run_writes_nothing(self):
        rc = run_crew_install(
            self.config, dry_run=True, pk=PK,
            run_doctor=self._ok_doctor, out=self.out,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self.config.read_text(), BASE_CONFIG)  # untouched
        self.assertFalse(self.config.with_name("crew.config.ts.bak").exists())
        # Prints a diff of what it would do.
        self.assertIn(SENTINEL_START, self.out.getvalue())
        self.assertIn("+", self.out.getvalue())

    def test_anchor_missing_writes_nothing_and_prints_blocks(self):
        # No `sources:` array and no `export default {` to create one in →
        # safety valve.
        self.config.write_text('const cfg = { foo: 1 };\n')
        original = self.config.read_text()
        with self.assertRaises(PlanKeeperCliError) as ctx:
            run_crew_install(
                self.config, dry_run=False, pk=PK,
                run_doctor=self._ok_doctor, out=self.out,
            )
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(self.config.read_text(), original)  # nothing written
        self.assertFalse(self.config.with_name("crew.config.ts.bak").exists())
        # The block to paste is printed for the user.
        self.assertIn(SENTINEL_START, self.out.getvalue())
        self.assertIn(f"{PK} crew fetch", self.out.getvalue())

    def test_missing_config_file_errors(self):
        missing = self.home / "nope.config.ts"
        with self.assertRaises(PlanKeeperCliError) as ctx:
            run_crew_install(
                missing, dry_run=False, pk=PK,
                run_doctor=self._ok_doctor, out=self.out,
            )
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("crew init", str(ctx.exception))

    def test_success_patches_json_config(self):
        """A .json config is patched as JSON: valid output, the named source
        replaced in place (not duplicated), backup kept."""
        json_cfg = self.home / "crew.config.json"
        json_cfg.write_text(JSON_CONFIG)
        rc = run_crew_install(
            json_cfg, dry_run=False, pk=PK,
            run_doctor=self._ok_doctor, out=self.out,
        )
        self.assertEqual(rc, 0)
        data = json.loads(json_cfg.read_text())
        pk_entries = [s for s in data["sources"] if s.get("name") == "plankeeper"]
        self.assertEqual(len(pk_entries), 1)
        self.assertEqual(pk_entries[0]["commands"]["fetch"], f"{PK} crew fetch")
        # Foreign entry survives; backup holds the pristine original.
        self.assertIn({"kind": "linear"}, data["sources"])
        backup = json_cfg.with_name("crew.config.json.bak")
        self.assertEqual(backup.read_text(), JSON_CONFIG)
        self.assertIn("1 plan(s) visible to fetch", self.out.getvalue())

    def test_json_safety_valve_prints_json_block(self):
        """A JSON config whose `sources` is the wrong shape hits the safety
        valve and is handed a JSON object to paste (not a TS region)."""
        json_cfg = self.home / "crew.config.json"
        json_cfg.write_text('{"sources": "oops"}')
        with self.assertRaises(PlanKeeperCliError) as ctx:
            run_crew_install(
                json_cfg, dry_run=False, pk=PK,
                run_doctor=self._ok_doctor, out=self.out,
            )
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(json_cfg.read_text(), '{"sources": "oops"}')  # untouched
        # The printed block is JSON (an object the user can paste), not TS.
        self.assertNotIn(SENTINEL_START, self.out.getvalue())
        self.assertIn('"name": "plankeeper"', self.out.getvalue())

    def test_json_array_routes_to_json_safety_valve(self):
        """A valid-JSON-but-not-object config (a bare array) routes through the
        JSON path, so the safety valve prints the JSON block — not the TS
        sentinel block it would have hit if `looks_like_json` rejected non-objects."""
        json_cfg = self.home / "crew.config.json"
        json_cfg.write_text("[]")
        with self.assertRaises(PlanKeeperCliError) as ctx:
            run_crew_install(
                json_cfg, dry_run=False, pk=PK,
                run_doctor=self._ok_doctor, out=self.out,
            )
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(json_cfg.read_text(), "[]")  # untouched
        self.assertNotIn(SENTINEL_START, self.out.getvalue())  # not the TS block
        self.assertIn('"name": "plankeeper"', self.out.getvalue())


class TestCrewInstallBinaryPreference(unittest.TestCase):
    """`cmd_crew_install` resolves which binary path gets baked into the wiring.

    `pk` is the primary command and `plan-keeper` a same-entry-point alias, so
    re-running `crew install` must repoint existing wiring to `pk` when it's
    present, falling back to `plan-keeper` only on installs that predate it.
    """

    def _resolved_pk(self, which_map: dict) -> str:
        captured = {}

        def fake_run_crew_install(config_path, *, dry_run, pk, run_doctor, out):
            captured["pk"] = pk
            return 0

        with patch.object(
            cli.shutil, "which", side_effect=lambda name: which_map.get(name)
        ), patch.object(
            cli, "resolve_config_path", return_value=Path("/cfg.ts")
        ), patch.object(
            cli, "run_crew_install", side_effect=fake_run_crew_install
        ):
            cli.cmd_crew_install(SimpleNamespace(config=None, dry_run=True))
        return captured["pk"]

    def test_prefers_pk_when_both_present(self):
        pk = self._resolved_pk(
            {"pk": "/opt/homebrew/bin/pk",
             "plan-keeper": "/opt/homebrew/bin/plan-keeper"}
        )
        self.assertEqual(pk, "/opt/homebrew/bin/pk")

    def test_falls_back_to_plan_keeper_alias(self):
        pk = self._resolved_pk({"plan-keeper": "/opt/homebrew/bin/plan-keeper"})
        self.assertEqual(pk, "/opt/homebrew/bin/plan-keeper")

    def test_literal_pk_when_neither_on_path(self):
        self.assertEqual(self._resolved_pk({}), "pk")


if __name__ == "__main__":
    unittest.main()
