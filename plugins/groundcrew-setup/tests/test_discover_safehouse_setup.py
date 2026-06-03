#!/usr/bin/env python3
"""Tests for discover_safehouse_setup.py.

Stdlib-only. Isolates HOME and PATH per test so the real ~/.config and
the user's real `safehouse`/`brew` binaries are never observed.

Run from anywhere:

    python3 plugins/groundcrew-setup/tests/test_discover_safehouse_setup.py -v
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).parent.parent / "scripts" / "discover_safehouse_setup.py"
_PYTHON3 = sys.executable


def _make_stub(bin_dir: Path, name: str, exit_code: int = 0, stdout: str = "") -> None:
    p = bin_dir / name
    p.write_text(f"#!/bin/sh\nprintf '%s' '{stdout}'\nexit {exit_code}\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_cli(home: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "en_US.UTF-8",
    }
    return subprocess.run(
        [_PYTHON3, str(CLI)],
        capture_output=True, text=True, timeout=15, env=env,
    )


class TestDiscoverSafehouseSetup(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.home = self.tmpdir / "home"
        self.home.mkdir()
        self.bin_dir = self.tmpdir / "bin"
        self.bin_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _load(self, r: subprocess.CompletedProcess) -> dict:
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    # ------------------------------------------------------------------
    # nothing installed → all False / null
    # ------------------------------------------------------------------
    def test_nothing_installed_all_false(self) -> None:
        r = _run_cli(self.home, self.bin_dir)
        report = self._load(r)
        self.assertEqual(report, {
            "binaryAvailable": False,
            "binaryPath": None,
            "brewFormulaInstalled": False,
            "envExported": False,
            "sidecarPresent": False,
            "sidecarHasFunctions": False,
        })

    # ------------------------------------------------------------------
    # safehouse binary on PATH
    # ------------------------------------------------------------------
    def test_safehouse_binary_on_path(self) -> None:
        _make_stub(self.bin_dir, "safehouse", 0, "")
        r = _run_cli(self.home, self.bin_dir)
        report = self._load(r)
        self.assertTrue(report["binaryAvailable"])
        self.assertEqual(report["binaryPath"], str(self.bin_dir / "safehouse"))

    # ------------------------------------------------------------------
    # brew formula installed
    # ------------------------------------------------------------------
    def test_brew_formula_installed(self) -> None:
        # brew list agent-safehouse --formula → exits 0
        _make_stub(self.bin_dir, "brew", 0, "")
        r = _run_cli(self.home, self.bin_dir)
        report = self._load(r)
        self.assertTrue(report["brewFormulaInstalled"])

    def test_brew_formula_not_installed_nonzero_exit(self) -> None:
        _make_stub(self.bin_dir, "brew", 1, "Error: No such keg")
        r = _run_cli(self.home, self.bin_dir)
        report = self._load(r)
        self.assertFalse(report["brewFormulaInstalled"])

    # ------------------------------------------------------------------
    # rc env detection
    # ------------------------------------------------------------------
    def test_env_export_detected_in_zshrc(self) -> None:
        (self.home / ".zshrc").write_text(
            "# some comment\n"
            'export SAFEHOUSE_APPEND_PROFILE="$HOME/.config/agent-safehouse/local-overrides.sb"\n',
            encoding="utf-8",
        )
        r = _run_cli(self.home, self.bin_dir)
        report = self._load(r)
        self.assertTrue(report["envExported"])

    def test_commented_export_not_counted(self) -> None:
        (self.home / ".zshrc").write_text(
            "# export SAFEHOUSE_APPEND_PROFILE=somewhere\n",
            encoding="utf-8",
        )
        r = _run_cli(self.home, self.bin_dir)
        report = self._load(r)
        self.assertFalse(report["envExported"])

    def test_env_export_detected_in_bashrc(self) -> None:
        (self.home / ".bashrc").write_text(
            'export SAFEHOUSE_APPEND_PROFILE="/tmp/foo.sb"\n',
            encoding="utf-8",
        )
        r = _run_cli(self.home, self.bin_dir)
        report = self._load(r)
        self.assertTrue(report["envExported"])

    # ------------------------------------------------------------------
    # sidecar file detection
    # ------------------------------------------------------------------
    def test_sidecar_present_no_functions(self) -> None:
        sidecar = self.home / ".config" / "agent-safehouse" / "env.sh"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            'export SAFEHOUSE_APPEND_PROFILE="$HOME/.config/agent-safehouse/local-overrides.sb"\n',
            encoding="utf-8",
        )
        r = _run_cli(self.home, self.bin_dir)
        report = self._load(r)
        self.assertTrue(report["sidecarPresent"])
        self.assertFalse(report["sidecarHasFunctions"])

    def test_sidecar_with_both_functions(self) -> None:
        sidecar = self.home / ".config" / "agent-safehouse" / "env.sh"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            'export SAFEHOUSE_APPEND_PROFILE="$HOME/.config/agent-safehouse/local-overrides.sb"\n\n'
            'safe() {\n'
            '  safehouse --append-profile="$SAFEHOUSE_APPEND_PROFILE" "$@"\n'
            '}\n\n'
            'safe-claude() {\n'
            '  safe claude --dangerously-skip-permissions "$@"\n'
            '}\n',
            encoding="utf-8",
        )
        r = _run_cli(self.home, self.bin_dir)
        report = self._load(r)
        self.assertTrue(report["sidecarPresent"])
        self.assertTrue(report["sidecarHasFunctions"])

    def test_sidecar_only_one_function(self) -> None:
        sidecar = self.home / ".config" / "agent-safehouse" / "env.sh"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            'safe() { safehouse "$@"; }\n',
            encoding="utf-8",
        )
        r = _run_cli(self.home, self.bin_dir)
        report = self._load(r)
        self.assertTrue(report["sidecarPresent"])
        self.assertFalse(report["sidecarHasFunctions"], "only one of two functions present")

    # ------------------------------------------------------------------
    # exit-code & stderr guarantees
    # ------------------------------------------------------------------
    def test_exit_zero_and_clean_stderr_always(self) -> None:
        r = _run_cli(self.home, self.bin_dir)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")


if __name__ == "__main__":
    unittest.main()
