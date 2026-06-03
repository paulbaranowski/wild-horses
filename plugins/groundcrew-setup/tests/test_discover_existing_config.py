#!/usr/bin/env python3
"""Tests for discover_existing_config.py.

Stdlib-only — no pytest. Run from anywhere:

    python3 plugins/groundcrew-setup/tests/test_discover_existing_config.py
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).parent.parent / "scripts" / "discover_existing_config.py"


def run_cli(cwd: Path, xdg_config_home: Path | None = None, home: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if xdg_config_home is not None:
        env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    else:
        env.pop("XDG_CONFIG_HOME", None)
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(
        ["python3", str(CLI)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestDiscoverExistingConfig(unittest.TestCase):
    def test_no_match_prints_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            r = run_cli(cwd=tmp_path, xdg_config_home=tmp_path / "xdg", home=tmp_path / "home")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "")

    def test_finds_groundcrew_config_in_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "groundcrew.config.ts"
            target.write_text("// config\n", encoding="utf-8")
            r = run_cli(cwd=tmp_path, xdg_config_home=tmp_path / "xdg", home=tmp_path / "home")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(Path(r.stdout.strip()), target.resolve())

    def test_ts_beats_js_beats_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "groundcrew.config.yaml").write_text("", encoding="utf-8")
            (tmp_path / "groundcrew.config.js").write_text("", encoding="utf-8")
            (tmp_path / "groundcrew.config.ts").write_text("", encoding="utf-8")
            r = run_cli(cwd=tmp_path, xdg_config_home=tmp_path / "xdg", home=tmp_path / "home")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(r.stdout.strip().endswith("groundcrew.config.ts"))

    def test_finds_xdg_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            xdg = tmp_path / "xdg"
            (xdg / "groundcrew").mkdir(parents=True)
            target = xdg / "groundcrew" / "config.ts"
            target.write_text("", encoding="utf-8")
            other_cwd = tmp_path / "elsewhere"
            other_cwd.mkdir()
            r = run_cli(cwd=other_cwd, xdg_config_home=xdg, home=tmp_path / "home")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(Path(r.stdout.strip()), target.resolve())

    def test_falls_back_to_home_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            (home / ".config" / "groundcrew").mkdir(parents=True)
            target = home / ".config" / "groundcrew" / "config.json"
            target.write_text("{}", encoding="utf-8")
            other_cwd = tmp_path / "elsewhere"
            other_cwd.mkdir()
            r = run_cli(cwd=other_cwd, home=home, xdg_config_home=None)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(Path(r.stdout.strip()), target.resolve())

    def test_cwd_match_wins_over_xdg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            xdg = tmp_path / "xdg"
            (xdg / "groundcrew").mkdir(parents=True)
            xdg_target = xdg / "groundcrew" / "config.ts"
            xdg_target.write_text("", encoding="utf-8")
            cwd_target = tmp_path / "groundcrew.config.ts"
            cwd_target.write_text("", encoding="utf-8")
            r = run_cli(cwd=tmp_path, xdg_config_home=xdg, home=tmp_path / "home")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(Path(r.stdout.strip()), cwd_target.resolve())


if __name__ == "__main__":
    unittest.main()
