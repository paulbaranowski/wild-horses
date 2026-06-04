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
import unittest
from pathlib import Path
from unittest.mock import patch

from support import IsolatedHomeTestCase
from plan_keeper import storage
from plan_keeper.crew_install import (
    SENTINEL_END,
    SENTINEL_START,
    build_patched_config,
    resolve_config_path,
    run_crew_install,
)
from plan_keeper.errors import PlanKeeperCliError

# A minimal but realistic groundcrew config: both anchors present, each array
# already holding one foreign entry so we can prove the managed region
# coexists with hand-maintained content.
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


class TestBuildPatchedConfig(unittest.TestCase):
    """The pure patcher: anchoring, idempotency, and content."""

    def test_fresh_insert_adds_both_regions(self):
        out = build_patched_config(BASE_CONFIG, PK, ["alpha", "beta"])
        assert out is not None
        # One managed region per array.
        self.assertEqual(out.count(SENTINEL_START), 2)
        self.assertEqual(out.count(SENTINEL_END), 2)
        # Foreign content is preserved (coexists with the managed region).
        self.assertIn('{ kind: "github", name: "issues" }', out)
        self.assertIn('"existing-repo"', out)

    def test_command_strings_bake_in_resolved_binary(self):
        out = build_patched_config(BASE_CONFIG, PK, ["alpha"])
        assert out is not None
        self.assertIn(f'fetch: "{PK} crew fetch"', out)
        self.assertIn(f'verify: "{PK} crew fetch >/dev/null"', out)
        # ${id} is groundcrew's literal token, not interpolated by us.
        self.assertIn(f'resolveOne: "{PK} crew get ${{id}}"', out)
        self.assertIn(f'markInProgress: "{PK} crew start ${{id}}"', out)

    def test_discovered_repos_are_listed(self):
        out = build_patched_config(BASE_CONFIG, PK, ["alpha", "beta"])
        assert out is not None
        self.assertIn('"alpha",', out)
        self.assertIn('"beta",', out)

    def test_rerun_is_idempotent(self):
        once = build_patched_config(BASE_CONFIG, PK, ["alpha"])
        assert once is not None
        twice = build_patched_config(once, PK, ["alpha"])
        assert twice is not None
        self.assertEqual(once, twice)
        # No duplicated regions on re-run.
        self.assertEqual(twice.count(SENTINEL_START), 2)

    def test_rerun_idempotent_on_crew_init_config(self):
        """The created-sources path (commented-out `sources:`) must also be
        idempotent: re-running must not stack a second created array."""
        once = build_patched_config(CREW_INIT_CONFIG, PK, ["repoA"])
        assert once is not None
        twice = build_patched_config(once, PK, ["repoA"])
        assert twice is not None
        self.assertEqual(once, twice)
        self.assertEqual(twice.count(SENTINEL_START), 2)

    def test_rerun_refreshes_repo_set_without_duplicating(self):
        once = build_patched_config(BASE_CONFIG, PK, ["alpha"])
        assert once is not None
        refreshed = build_patched_config(once, PK, ["alpha", "beta"])
        assert refreshed is not None
        self.assertEqual(refreshed.count(SENTINEL_START), 2)  # still two regions
        self.assertIn('"beta",', refreshed)

    def test_rerun_repoints_binary_path(self):
        once = build_patched_config(BASE_CONFIG, "/old/plan-keeper", ["alpha"])
        assert once is not None
        moved = build_patched_config(once, "/new/plan-keeper", ["alpha"])
        assert moved is not None
        self.assertIn("/new/plan-keeper crew fetch", moved)
        self.assertNotIn("/old/plan-keeper", moved)

    def test_creates_sources_array_when_only_commented_out(self):
        """crew init comments `sources:` out — anchor it inside the comment and
        the TS breaks. Instead a fresh active `sources` key is created, and the
        commented original is left untouched."""
        out = build_patched_config(CREW_INIT_CONFIG, PK, ["repoA"])
        assert out is not None
        self.assertEqual(out.count(SENTINEL_START), 2)  # created + knownRepos
        self.assertIn(f'fetch: "{PK} crew fetch"', out)
        self.assertIn('"repoA",', out)
        # The commented-out template `sources:` is preserved as-is.
        self.assertIn("//   { kind:", out)

    def test_patches_nested_known_repositories(self):
        """crew init nests knownRepositories inside workspace; the managed
        region coexists with the entry already there."""
        out = build_patched_config(CREW_INIT_CONFIG, PK, ["repoA"])
        assert out is not None
        self.assertIn('"repoA",', out)
        self.assertIn('"your-org/your-repo"', out)  # pre-existing entry kept

    def test_no_export_default_returns_none(self):
        """No active sources AND no export object to create one in → safety
        valve (the caller prints the blocks for manual paste)."""
        orphan = 'const cfg = { knownRepositories: [] };'
        self.assertIsNone(build_patched_config(orphan, PK, []))

    def test_missing_known_repositories_anchor_returns_none(self):
        no_repos = 'export default { sources: [] } satisfies Config;'
        self.assertIsNone(build_patched_config(no_repos, PK, []))

    def test_empty_repo_set_still_patches_sources(self):
        out = build_patched_config(BASE_CONFIG, PK, [])
        assert out is not None
        self.assertEqual(out.count(SENTINEL_START), 2)
        self.assertIn(f'fetch: "{PK} crew fetch"', out)


class TestResolveConfigPath(unittest.TestCase):
    """--config > $GROUNDCREW_CONFIG > ~/.config/groundcrew/crew.config.ts."""

    def test_explicit_config_arg_wins(self):
        p = resolve_config_path("/tmp/x.ts", {"GROUNDCREW_CONFIG": "/env.ts"},
                                Path("/home/u"))
        self.assertEqual(p, Path("/tmp/x.ts"))

    def test_env_used_when_no_arg(self):
        p = resolve_config_path(None, {"GROUNDCREW_CONFIG": "/env.ts"},
                                Path("/home/u"))
        self.assertEqual(p, Path("/env.ts"))

    def test_default_when_neither_set(self):
        p = resolve_config_path(None, {}, Path("/home/u"))
        self.assertEqual(
            p, Path("/home/u/.config/groundcrew/crew.config.ts")
        )


class TestRunCrewInstall(IsolatedHomeTestCase):
    """Orchestration: backup, validate, rollback, dry-run, safety valve."""

    def setUp(self) -> None:
        super().setUp()
        self.plans_root.mkdir(parents=True)
        (self.plans_root / "myrepo").mkdir()
        (self.plans_root / "myrepo" / "2026-01-01-x.md").write_text(
            "---\nStatus: todo\n---\n# X\n"
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
        self.assertEqual(patched.count(SENTINEL_START), 2)
        self.assertIn('"myrepo",', patched)  # discovered from PLAN_ROOT
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
        self.assertEqual(self.config.read_text().count(SENTINEL_START), 2)
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
        # knownRepositories absent (and not auto-created) → safety valve.
        self.config.write_text(
            'export default { sources: [] } satisfies Config;\n'
        )
        original = self.config.read_text()
        with self.assertRaises(PlanKeeperCliError) as ctx:
            run_crew_install(
                self.config, dry_run=False, pk=PK,
                run_doctor=self._ok_doctor, out=self.out,
            )
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(self.config.read_text(), original)  # nothing written
        self.assertFalse(self.config.with_name("crew.config.ts.bak").exists())
        # The blocks to paste are printed for the user.
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


if __name__ == "__main__":
    unittest.main()
