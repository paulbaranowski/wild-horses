#!/usr/bin/env python3
"""Tests for install_safehouse.py.

Uses a stub `brew` binary placed in a tmpdir to simulate the install states.
Mirrors test_install_groundcrew.py's stub-based approach.

Run from anywhere:

    python3 plugins/groundcrew-setup/tests/test_install_safehouse.py -v
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

CLI = Path(__file__).parent.parent / "scripts" / "install_safehouse.py"
_PYTHON3 = sys.executable


def _write_stub_brew(bin_dir: Path, script_body: str) -> Path:
    p = bin_dir / "brew"
    p.write_text(script_body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


STUB_INSTALLED = textwrap.dedent("""\
    #!/bin/sh
    # stub brew — already installed at 0.9.0
    if [ "$1" = "list" ] && [ "$2" = "--versions" ]; then
      echo "agent-safehouse 0.9.0"
      exit 0
    fi
    if [ "$1" = "install" ]; then
      exit 0
    fi
    exit 99
""")


STUB_MISSING_NO_INSTALL = textwrap.dedent("""\
    #!/bin/sh
    # stub brew — always reports missing; install would fail
    if [ "$1" = "list" ] && [ "$2" = "--versions" ]; then
      exit 1
    fi
    if [ "$1" = "install" ]; then
      echo "FAIL — --check should not call install" >&2
      exit 199
    fi
    exit 99
""")


STUB_MISSING_INSTALL_FAIL = textwrap.dedent("""\
    #!/bin/sh
    # stub brew — list says missing; install fails
    if [ "$1" = "list" ] && [ "$2" = "--versions" ]; then
      exit 1
    fi
    if [ "$1" = "install" ]; then
      echo "Error: Failed to download tap eugene1g/safehouse" >&2
      exit 17
    fi
    exit 99
""")


def _stub_missing_then_success(state_file: Path) -> str:
    """list reports missing initially; install writes state; subsequent list reports installed."""
    return textwrap.dedent(f"""\
        #!/bin/sh
        STATE_FILE='{state_file}'
        STATE=$(cat "$STATE_FILE" 2>/dev/null || echo missing)
        if [ "$1" = "list" ] && [ "$2" = "--versions" ]; then
          if [ "$STATE" = "installed" ]; then
            echo "agent-safehouse 0.9.0"
            exit 0
          fi
          exit 1
        fi
        if [ "$1" = "install" ]; then
          echo installed > "$STATE_FILE"
          exit 0
        fi
        exit 99
    """)


def _run_cli(bin_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Invoke CLI with PATH that finds only the stub brew (plus standard sh utilities)."""
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(bin_dir.parent),
        "LANG": "en_US.UTF-8",
    }
    return subprocess.run(
        [_PYTHON3, str(CLI), *args],
        capture_output=True, text=True, timeout=30, env=env,
    )


class TestInstallSafehouse(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.bin_dir = self.tmpdir / "bin"
        self.bin_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_brew_missing_emits_failed(self) -> None:
        r = _run_cli(self.bin_dir)
        self.assertEqual(r.returncode, 1, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "failed")
        self.assertIn("brew", report["details"].lower())

    def test_already_installed(self) -> None:
        _write_stub_brew(self.bin_dir, STUB_INSTALLED)
        r = _run_cli(self.bin_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "already-installed")
        self.assertEqual(report["version"], "0.9.0")

    def test_already_installed_with_check(self) -> None:
        _write_stub_brew(self.bin_dir, STUB_INSTALLED)
        r = _run_cli(self.bin_dir, "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "already-installed")
        self.assertEqual(report["version"], "0.9.0")

    def test_check_only_when_missing_does_not_install(self) -> None:
        _write_stub_brew(self.bin_dir, STUB_MISSING_NO_INSTALL)
        r = _run_cli(self.bin_dir, "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "missing")
        self.assertIsNone(report["version"])

    def test_install_succeeds(self) -> None:
        state_file = self.tmpdir / "brew-state"
        _write_stub_brew(self.bin_dir, _stub_missing_then_success(state_file))
        r = _run_cli(self.bin_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "installed")
        self.assertEqual(report["version"], "0.9.0")

    def test_install_fails_propagates_brew_exit_code(self) -> None:
        _write_stub_brew(self.bin_dir, STUB_MISSING_INSTALL_FAIL)
        r = _run_cli(self.bin_dir)
        self.assertEqual(r.returncode, 17)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "failed")
        self.assertIn("eugene1g/safehouse", report["details"])


if __name__ == "__main__":
    unittest.main()
