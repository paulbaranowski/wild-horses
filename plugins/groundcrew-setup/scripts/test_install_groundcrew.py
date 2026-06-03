#!/usr/bin/env python3
"""Tests for install_groundcrew.py.

Uses a stub `npm` binary placed in a tmpdir to simulate four states:
already-installed, missing-then-success, missing-then-fail, and
missing-stays-missing (install reports success but post-install probe
still finds no package — represents a corrupt npm registry).

Run from anywhere:

    python3 plugins/groundcrew-setup/scripts/test_install_groundcrew.py -v
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

CLI = Path(__file__).parent / "install_groundcrew.py"
_PYTHON3 = sys.executable


def _write_stub_npm(bin_dir: Path, script_body: str) -> Path:
    """Write a stub npm at bin_dir/npm with given body; return its path."""
    npm = bin_dir / "npm"
    npm.write_text(script_body, encoding="utf-8")
    npm.chmod(npm.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return npm


STUB_INSTALLED = textwrap.dedent("""\
    #!/bin/sh
    # stub npm — already installed at 4.9.0
    if [ "$1" = "ls" ]; then
      printf '%s' '{"dependencies":{"@clipboard-health/groundcrew":{"version":"4.9.0"}}}'
      exit 0
    fi
    if [ "$1" = "install" ]; then
      exit 0
    fi
    exit 99
""")


STUB_MISSING_NO_INSTALL_ATTEMPTED = textwrap.dedent("""\
    #!/bin/sh
    # stub npm — always reports missing; install would fail if called
    if [ "$1" = "ls" ]; then
      printf '%s' '{"dependencies":{}}'
      exit 1
    fi
    if [ "$1" = "install" ]; then
      echo "npm ERR! UNEXPECTED — --check should not have called install" >&2
      exit 199
    fi
    exit 99
""")


STUB_MISSING_INSTALL_FAIL = textwrap.dedent("""\
    #!/bin/sh
    # stub npm — ls reports missing; install fails with EACCES
    if [ "$1" = "ls" ]; then
      printf '%s' '{"dependencies":{}}'
      exit 1
    fi
    if [ "$1" = "install" ]; then
      echo "npm ERR! code EACCES: permission denied" >&2
      exit 244
    fi
    exit 99
""")


def _stub_missing_then_success(state_file: Path) -> str:
    """ls reports missing on first call, installed after `install` runs."""
    return textwrap.dedent(f"""\
        #!/bin/sh
        STATE_FILE='{state_file}'
        STATE=$(cat "$STATE_FILE" 2>/dev/null || echo missing)
        if [ "$1" = "ls" ]; then
          if [ "$STATE" = "installed" ]; then
            printf '%s' '{{"dependencies":{{"@clipboard-health/groundcrew":{{"version":"4.9.0"}}}}}}'
            exit 0
          fi
          printf '%s' '{{"dependencies":{{}}}}'
          exit 1
        fi
        if [ "$1" = "install" ]; then
          echo installed > "$STATE_FILE"
          exit 0
        fi
        exit 99
    """)


STUB_MISSING_STAYS_MISSING = textwrap.dedent("""\
    #!/bin/sh
    # ls always reports missing; install "succeeds" but doesn't change state
    if [ "$1" = "ls" ]; then
      printf '%s' '{"dependencies":{}}'
      exit 1
    fi
    if [ "$1" = "install" ]; then
      exit 0
    fi
    exit 99
""")


def _run_cli(bin_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Invoke CLI with bin_dir prefixed on PATH.

    PATH includes /usr/bin:/bin so the stub can call `cat`, `printf`, etc.,
    which are not shell builtins in /bin/sh. macOS's standard npm install
    paths (/opt/homebrew/bin, /usr/local/bin) are NOT included, so the
    stub at bin_dir/npm is the only npm reachable.
    """
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(bin_dir.parent),
        "LANG": "en_US.UTF-8",
    }
    return subprocess.run(
        [_PYTHON3, str(CLI), *args],
        capture_output=True, text=True, timeout=30, env=env,
    )


class TestInstallGroundcrew(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.bin_dir = self.tmpdir / "bin"
        self.bin_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_npm_missing_emits_failed(self) -> None:
        """PATH with no npm: action=failed, exit 1, mentions npm.

        Standard system dirs (/usr/bin, /bin) don't ship npm on macOS;
        npm lives in /opt/homebrew/bin or /usr/local/bin, neither of which
        is included in _run_cli's PATH. bin_dir is empty here, so
        shutil.which("npm") inside the CLI returns None.
        """
        r = _run_cli(self.bin_dir)  # bin_dir is empty
        self.assertEqual(r.returncode, 1, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "failed")
        self.assertIsNone(report["version"])
        self.assertIn("npm", report["details"].lower())

    def test_already_installed_no_op(self) -> None:
        _write_stub_npm(self.bin_dir, STUB_INSTALLED)
        r = _run_cli(self.bin_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "already-installed")
        self.assertEqual(report["version"], "4.9.0")

    def test_already_installed_with_check(self) -> None:
        _write_stub_npm(self.bin_dir, STUB_INSTALLED)
        r = _run_cli(self.bin_dir, "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "already-installed")
        self.assertEqual(report["version"], "4.9.0")

    def test_check_only_when_missing_does_not_install(self) -> None:
        """With --check, missing package should report 'missing' WITHOUT calling install."""
        _write_stub_npm(self.bin_dir, STUB_MISSING_NO_INSTALL_ATTEMPTED)
        r = _run_cli(self.bin_dir, "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "missing")
        self.assertIsNone(report["version"])

    def test_install_succeeds(self) -> None:
        state_file = self.tmpdir / "npm-state"
        _write_stub_npm(self.bin_dir, _stub_missing_then_success(state_file))
        r = _run_cli(self.bin_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "installed")
        self.assertEqual(report["version"], "4.9.0")

    def test_install_fails_propagates_npm_exit_code(self) -> None:
        _write_stub_npm(self.bin_dir, STUB_MISSING_INSTALL_FAIL)
        r = _run_cli(self.bin_dir)
        self.assertEqual(r.returncode, 244)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "failed")
        self.assertIsNone(report["version"])
        self.assertIn("EACCES", report["details"])

    def test_install_succeeds_but_post_probe_finds_nothing(self) -> None:
        """If install exits 0 but second probe still reports missing, version is null."""
        _write_stub_npm(self.bin_dir, STUB_MISSING_STAYS_MISSING)
        r = _run_cli(self.bin_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "installed")
        self.assertIsNone(report["version"])


if __name__ == "__main__":
    unittest.main()
