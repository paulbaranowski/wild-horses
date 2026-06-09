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
        self.assertIn(f'fetch: "{PK} crew fetch"', out)
        self.assertIn(f'verify: "{PK} crew fetch >/dev/null"', out)
        # ${id} is groundcrew's literal token, not interpolated by us.
        self.assertIn(f'resolveOne: "{PK} crew get ${{id}}"', out)
        self.assertIn(f'markInProgress: "{PK} crew start ${{id}}"', out)
        self.assertIn(f'markInReview: "{PK} crew review ${{id}}"', out)

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


if __name__ == "__main__":
    unittest.main()
