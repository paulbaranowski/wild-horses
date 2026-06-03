#!/usr/bin/env python3
"""Tests for render_clearance_env.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).parent / "render_clearance_env.py"
_PYTHON3 = sys.executable


def _run_cli(home: Path, target: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    args = [_PYTHON3, str(CLI)]
    if target is not None:
        args.extend(["--target", str(target)])
    return subprocess.run(args, capture_output=True, text=True, timeout=15, env=env)


class TestRenderClearanceEnv(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.home = self.tmpdir / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read_sidecar(self, target: Path) -> str:
        self.assertTrue(target.exists(), f"sidecar not written at {target}")
        return target.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # No rc conflicts → both exports active
    # ------------------------------------------------------------------
    def test_no_conflicts_writes_both_exports(self) -> None:
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["target"], str(target))
        self.assertTrue(report["wrote"])
        self.assertEqual(report["rcConflicts"], [])

        content = self._read_sidecar(target)
        # Both exports active (not commented).
        self.assertRegex(content, r"(?m)^export CLEARANCE_ALLOW_HOSTS_FILES=")
        self.assertRegex(content, r"(?m)^export CLEARANCE_PERSONAL_HOSTS=1\b")

    def test_default_target_path(self) -> None:
        """No --target flag → default ~/.config/clearance/env.sh."""
        r = _run_cli(self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        default_target = self.home / ".config" / "clearance" / "env.sh"
        self.assertEqual(Path(report["target"]), default_target)
        self.assertTrue(default_target.exists())

    # ------------------------------------------------------------------
    # rc conflict on one var → only that var commented
    # ------------------------------------------------------------------
    def test_allow_hosts_already_in_zshrc_comments_only_that_var(self) -> None:
        zshrc = self.home / ".zshrc"
        # Mirror the layout this PR's smoke test found on the author's machine
        # (.zshrc:169 holds CLEARANCE_ALLOW_HOSTS_FILES, no PERSONAL_HOSTS).
        prelude = "\n".join(["# noise"] * 168) + "\n"
        zshrc.write_text(
            prelude + 'export CLEARANCE_ALLOW_HOSTS_FILES="/some/path"\n',
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(len(report["rcConflicts"]), 1)
        conflict = report["rcConflicts"][0]
        self.assertEqual(conflict["var"], "CLEARANCE_ALLOW_HOSTS_FILES")
        self.assertEqual(conflict["file"], str(zshrc))
        self.assertEqual(conflict["line"], 169)

        content = self._read_sidecar(target)
        # ALLOW_HOSTS export must be commented; PERSONAL must be active.
        self.assertNotRegex(content, r"(?m)^export CLEARANCE_ALLOW_HOSTS_FILES=")
        self.assertRegex(content, r"(?m)^# export CLEARANCE_ALLOW_HOSTS_FILES=")
        self.assertIn(str(zshrc) + ":169", content)
        self.assertRegex(content, r"(?m)^export CLEARANCE_PERSONAL_HOSTS=1\b")

    def test_both_vars_in_rc_both_commented(self) -> None:
        zshrc = self.home / ".zshrc"
        zshrc.write_text(
            'export CLEARANCE_ALLOW_HOSTS_FILES="/p"\n'
            "export CLEARANCE_PERSONAL_HOSTS=1\n",
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(len(report["rcConflicts"]), 2)
        content = self._read_sidecar(target)
        # Neither export is active.
        self.assertNotRegex(content, r"(?m)^export CLEARANCE_ALLOW_HOSTS_FILES=")
        self.assertNotRegex(content, r"(?m)^export CLEARANCE_PERSONAL_HOSTS=")
        self.assertRegex(content, r"(?m)^# export CLEARANCE_ALLOW_HOSTS_FILES=")
        self.assertRegex(content, r"(?m)^# export CLEARANCE_PERSONAL_HOSTS=")

    def test_commented_rc_line_does_not_count_as_conflict(self) -> None:
        """A # export ... in the rc must NOT prevent the sidecar from owning the var."""
        (self.home / ".zshrc").write_text(
            "# export CLEARANCE_ALLOW_HOSTS_FILES=/p\n",
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["rcConflicts"], [])
        content = self._read_sidecar(target)
        self.assertRegex(content, r"(?m)^export CLEARANCE_ALLOW_HOSTS_FILES=")

    def test_substring_mention_is_not_a_conflict(self) -> None:
        """Lines that mention the var name in passing must NOT count as conflicts.

        Regression: the original scanner used `if var in stripped`, which would
        flag an `echo "set ..."` reminder, an `unset alias`, or — most painfully
        — the var name appearing inside another export's `${VAR:+...}` value as
        a "conflict," silently commenting out the sidecar's actual export.
        """
        (self.home / ".zshrc").write_text(
            'echo "Remember to set CLEARANCE_ALLOW_HOSTS_FILES if not in sidecar"\n'
            'alias unset-clearance="unset CLEARANCE_PERSONAL_HOSTS"\n'
            'export OTHER_VAR="/p${CLEARANCE_PERSONAL_HOSTS:+:foo}"\n',
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(
            report["rcConflicts"], [],
            "non-export mentions of the var name must not be flagged as conflicts",
        )
        content = self._read_sidecar(target)
        self.assertRegex(content, r"(?m)^export CLEARANCE_PERSONAL_HOSTS=1")
        self.assertRegex(content, r"(?m)^export CLEARANCE_ALLOW_HOSTS_FILES=")

    # ------------------------------------------------------------------
    # Rc scanning across multiple files
    # ------------------------------------------------------------------
    def test_bashrc_is_scanned_too(self) -> None:
        (self.home / ".bashrc").write_text(
            'export CLEARANCE_PERSONAL_HOSTS=1\n',
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(len(report["rcConflicts"]), 1)
        self.assertEqual(report["rcConflicts"][0]["var"], "CLEARANCE_PERSONAL_HOSTS")

    def test_zshrc_wins_over_bashrc_for_same_var(self) -> None:
        (self.home / ".zshrc").write_text(
            'export CLEARANCE_PERSONAL_HOSTS=1\n',
            encoding="utf-8",
        )
        (self.home / ".bashrc").write_text(
            'export CLEARANCE_PERSONAL_HOSTS=1\n',
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(len(report["rcConflicts"]), 1)
        self.assertEqual(report["rcConflicts"][0]["file"], str(self.home / ".zshrc"))

    # ------------------------------------------------------------------
    # Sidecar shape sanity
    # ------------------------------------------------------------------
    def test_sidecar_value_uses_runtime_expansion(self) -> None:
        """The sidecar must use $(npm root -g) and $HOME so it resolves at source-time."""
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        content = self._read_sidecar(target)
        self.assertIn("$(npm root -g)", content)
        self.assertIn("$HOME/.config/clearance/personal-allow-hosts", content)
        self.assertIn("${CLEARANCE_PERSONAL_HOSTS:+", content)

    def test_sidecar_has_source_hint_comment(self) -> None:
        """Sidecar must include the source-from-rc hint so the user knows what to do."""
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        content = self._read_sidecar(target)
        self.assertIn("Source this from your shell rc", content)
        self.assertIn("~/.config/clearance/env.sh", content)
        self.assertIn("~/.config/agent-safehouse/env.sh", content)

    # ------------------------------------------------------------------
    # Atomic-write + idempotency
    # ------------------------------------------------------------------
    def test_rewrite_overwrites_existing_sidecar(self) -> None:
        target = self.tmpdir / "env.sh"
        target.write_text("stale content\n", encoding="utf-8")
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        content = self._read_sidecar(target)
        self.assertNotIn("stale content", content)
        self.assertIn("CLEARANCE_ALLOW_HOSTS_FILES", content)

    def test_idempotent_byte_identical_rewrites(self) -> None:
        target = self.tmpdir / "env.sh"
        r1 = _run_cli(self.home, target=target)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        first = target.read_bytes()
        r2 = _run_cli(self.home, target=target)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        second = target.read_bytes()
        self.assertEqual(first, second, "two successive renders should produce identical bytes")

    def test_creates_parent_directories(self) -> None:
        target = self.tmpdir / "deep" / "nested" / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(target.exists())

    # ------------------------------------------------------------------
    # Sourceable check
    # ------------------------------------------------------------------
    def test_sidecar_sources_cleanly_in_sh(self) -> None:
        """The sidecar must be valid /bin/sh — sourcing must not error."""
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        # Stub npm so $(npm root -g) doesn't fail if npm isn't on the test PATH.
        stub_bin = self.tmpdir / "bin"
        stub_bin.mkdir(exist_ok=True)
        npm_stub = stub_bin / "npm"
        npm_stub.write_text(
            "#!/bin/sh\n[ \"$1\" = root ] && echo /opt/homebrew/lib/node_modules\nexit 0\n",
            encoding="utf-8",
        )
        npm_stub.chmod(0o755)
        check = subprocess.run(
            [
                "/bin/sh",
                "-c",
                f". {target} && echo CLEARANCE_ALLOW_HOSTS_FILES=$CLEARANCE_ALLOW_HOSTS_FILES",
            ],
            capture_output=True,
            text=True,
            env={"PATH": f"{stub_bin}:/usr/bin:/bin", "HOME": str(self.home)},
            timeout=5,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        # CLEARANCE_PERSONAL_HOSTS is set to 1 by the sidecar, so the personal
        # path is appended after the colon.
        self.assertIn(
            "/opt/homebrew/lib/node_modules/@clipboard-health/groundcrew/clearance-allow-hosts:"
            f"{self.home}/.config/clearance/personal-allow-hosts",
            check.stdout,
        )


if __name__ == "__main__":
    unittest.main()
