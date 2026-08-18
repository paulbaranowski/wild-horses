#!/usr/bin/env python3
"""Grok package files must exist and stay aligned with the Claude catalog."""

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS = REPO_ROOT / "plugins"
CLAUDE_MARKET = REPO_ROOT / ".claude-plugin" / "marketplace.json"
GROK_MARKET = REPO_ROOT / ".grok-plugin" / "marketplace.json"

GROK_FALLBACK = "${GROK_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _catalog_names(path: Path) -> set[str]:
    return {entry["name"] for entry in _load(path)["plugins"]}


def _plugin_dirs() -> list[Path]:
    return sorted(p for p in PLUGINS.iterdir() if p.is_dir())


class TestGrokCatalog(unittest.TestCase):
    def test_grok_marketplace_exists(self):
        self.assertTrue(GROK_MARKET.is_file(), f"missing {GROK_MARKET}")

    def test_grok_catalog_names_match_claude(self):
        self.assertEqual(_catalog_names(GROK_MARKET), _catalog_names(CLAUDE_MARKET))

    def test_grok_catalog_sources_match_plugin_dirs(self):
        for entry in _load(GROK_MARKET)["plugins"]:
            source = entry["source"]
            self.assertEqual(source, f"./plugins/{entry['name']}")
            self.assertTrue((REPO_ROOT / source).is_dir())

    def test_every_plugin_dir_has_grok_manifest(self):
        missing = [
            p.name
            for p in _plugin_dirs()
            if not (p / ".grok-plugin" / "plugin.json").is_file()
        ]
        self.assertEqual(missing, [], f"missing .grok-plugin/plugin.json: {missing}")

    def test_grok_manifest_fields_match_claude(self):
        keys = ("name", "description", "version", "author")
        for plugin_dir in _plugin_dirs():
            claude = _load(plugin_dir / ".claude-plugin" / "plugin.json")
            grok = _load(plugin_dir / ".grok-plugin" / "plugin.json")
            for key in keys:
                self.assertEqual(
                    grok.get(key),
                    claude.get(key),
                    f"{plugin_dir.name} {key} drifted from Claude manifest",
                )


class TestGrokHookCommands(unittest.TestCase):
    def test_every_hooks_json_command_uses_grok_fallback(self):
        bad = []
        for hooks_file in sorted(PLUGINS.glob("*/hooks/hooks.json")):
            data = _load(hooks_file)
            for event, groups in data["hooks"].items():
                for group in groups:
                    for hook in group["hooks"]:
                        command = hook["command"]
                        if GROK_FALLBACK not in command:
                            bad.append(f"{hooks_file}: {command}")
                            continue
                        rest = command.replace(GROK_FALLBACK, "ROOT")
                        if "CLAUDE_PLUGIN_ROOT" in rest or "GROK_PLUGIN_ROOT" in rest:
                            bad.append(f"{hooks_file}: leftover root var in {command}")
        self.assertEqual(bad, [], "hook commands must use GROK_FALLBACK only:\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
