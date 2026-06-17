#!/usr/bin/env python3
"""Global ~/plans/.plankeeper-global.json CRUD (global_config.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import unittest
from unittest.mock import patch

from support import IsolatedHomeTestCase  # noqa: F401 — inserts scripts/ onto sys.path

from plan_keeper import storage  # noqa: E402
from plan_keeper.errors import PlanKeeperCliError  # noqa: E402
from plan_keeper.global_config import (  # noqa: E402
    GLOBAL_CONFIG_FILE_NAME,
    global_config_path,
    load_global_config,
    save_global_config,
)


class TestGlobalConfigLoader(IsolatedHomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Relocate PLAN_ROOT into the per-test $HOME so reads/writes never touch
        # the real ~/plans/. Tests mutate storage.PLAN_ROOT in-place so the
        # change reaches every module that resolves through it.
        self._plan_root_patch = patch.object(storage, "PLAN_ROOT", self.plans_root)
        self._plan_root_patch.start()

    def tearDown(self) -> None:
        self._plan_root_patch.stop()
        super().tearDown()

    def test_path_is_plans_root_slash_global_filename(self) -> None:
        # The file lives at ~/plans/.plankeeper-global.json (parallel to the
        # per-repo .plankeeper.json), so the path is fully determined by
        # PLAN_ROOT — no repo argument.
        self.assertEqual(
            global_config_path(),
            self.plans_root / GLOBAL_CONFIG_FILE_NAME,
        )

    def test_missing_file_returns_default_shape(self) -> None:
        # No file on disk: the loader returns {"aliases": []} so callers can
        # iterate the list without a None guard.
        result = load_global_config()
        self.assertEqual(result, {"aliases": []})

    def test_round_trip_preserves_aliases(self) -> None:
        data = {
            "aliases": [
                {"remote": "carrot", "subpath": "catalog/flawless-inventory",
                 "name": "maple"},
                {"remote": "carrot", "subpath": "frontend/web-app",
                 "name": "frontend-web"},
            ]
        }
        save_global_config(data)
        self.assertEqual(load_global_config(), data)

    def test_malformed_json_raises_code_5(self) -> None:
        # Mirrors plan_keeper.config.load_config — same error code (5) and
        # error class, since a corrupted global config is the same class
        # of failure as a corrupted per-repo config.
        self.plans_root.mkdir(parents=True, exist_ok=True)
        global_config_path().write_text("not json", encoding="utf-8")
        with self.assertRaises(PlanKeeperCliError) as ctx:
            load_global_config()
        self.assertEqual(ctx.exception.code, 5)

    def test_save_creates_parent_dir(self) -> None:
        # First-ever `alias add` runs against a $HOME with no plans tree yet.
        # The save must create ~/plans/ rather than failing on missing parent.
        self.assertFalse(self.plans_root.exists())
        save_global_config({"aliases": []})
        self.assertTrue(self.plans_root.is_dir())
        self.assertTrue(global_config_path().exists())

    def test_save_writes_atomically_via_tmp_rename(self) -> None:
        # Atomicity is the load-bearing property: a crash mid-write must leave
        # the file in its prior (or absent) state, never half-written. We can't
        # crash the process, but we can verify save_global_config delegates to
        # write_atomic (the shared POSIX-atomic helper), which is the contract.
        with patch("plan_keeper.global_config.write_atomic") as mock_write:
            save_global_config({"aliases": [{"remote": "r", "subpath": "s",
                                              "name": "n"}]})
            self.assertEqual(mock_write.call_count, 1)
            args, _ = mock_write.call_args
            self.assertEqual(args[0], global_config_path())
            # The serialized payload round-trips to the same data.
            self.assertEqual(json.loads(args[1]), {"aliases": [
                {"remote": "r", "subpath": "s", "name": "n"}
            ]})

    def test_unknown_top_level_keys_preserved_across_load_save(self) -> None:
        # Forward-compat: a newer client may write keys this version doesn't
        # know about (e.g., `defaults`, `hooks`). load -> save must preserve
        # them so an older client doesn't silently erase them.
        self.plans_root.mkdir(parents=True, exist_ok=True)
        future = {
            "aliases": [{"remote": "r", "subpath": "s", "name": "n"}],
            "defaults": {"editor": "vim"},
            "hooks": {"on_save": "echo"},
        }
        global_config_path().write_text(json.dumps(future), encoding="utf-8")
        loaded = load_global_config()
        save_global_config(loaded)
        self.assertEqual(load_global_config(), future)


if __name__ == "__main__":
    unittest.main(verbosity=2)
