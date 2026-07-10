#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from update_cursor_plugins import (
    load_catalog,
    normalize_plugin_root,
    plugin_dest_dir,
    plugin_dest_error,
    plugin_source_path,
    resolve_source_dir,
)


class TestManifestHelpers(unittest.TestCase):
    def test_plugin_source_path_string(self) -> None:
        self.assertEqual(plugin_source_path("plugins/harness"), "plugins/harness")

    def test_plugin_source_path_object(self) -> None:
        self.assertEqual(plugin_source_path({"path": "plugins/harness"}), "plugins/harness")

    def test_normalize_plugin_root(self) -> None:
        self.assertEqual(normalize_plugin_root("./plugins"), "plugins")
        self.assertIsNone(normalize_plugin_root(None))

    def test_resolve_source_dir_with_plugin_root(self) -> None:
        root = Path("/marketplace")
        resolved = resolve_source_dir(root, "harness", "plugins")
        self.assertEqual(resolved, root / "plugins" / "harness")

    def test_resolve_source_dir_already_prefixed(self) -> None:
        root = Path("/marketplace")
        resolved = resolve_source_dir(root, "plugins/harness", "plugins")
        self.assertEqual(resolved, root / "plugins" / "harness")

    def test_load_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "marketplace.json"
            manifest.write_text(
                json.dumps(
                    {
                        "metadata": {"pluginRoot": "plugins"},
                        "plugins": [
                            {"name": "harness", "source": "harness"},
                            {"name": "linting-hooks", "source": {"path": "linting-hooks"}},
                            {"name": "bad", "source": 42},
                        ],
                    }
                )
            )
            plugin_root, entries = load_catalog(manifest)
            self.assertEqual(plugin_root, "plugins")
            self.assertEqual(
                entries,
                [("harness", "harness"), ("linting-hooks", "linting-hooks")],
            )

    def test_plugin_dest_error_rejects_unsafe_names(self) -> None:
        for bad in ("", ".", "..", "foo/bar", r"foo\bar"):
            self.assertIsNotNone(plugin_dest_error(bad))
        self.assertIsNone(plugin_dest_error("pr-status-hook"))

    def test_plugin_dest_dir_stays_under_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "local"
            root.mkdir()
            dest = plugin_dest_dir(root, "harness")
            self.assertEqual(dest, (root / "harness").resolve())


if __name__ == "__main__":
    unittest.main()
