#!/usr/bin/env python3
"""Tests for render_safehouse_env.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).parent / "render_safehouse_env.py"
_PYTHON3 = sys.executable


def _run_cli(home: Path, *extra_args: str, target: Path | None = None, overrides: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    args = [_PYTHON3, str(CLI)]
    if target is not None:
        args.extend(["--target", str(target)])
    if overrides is not None:
        args.extend(["--overrides-file", str(overrides)])
    args.extend(extra_args)
    return subprocess.run(args, capture_output=True, text=True, timeout=15, env=env)


class TestRenderSafehouseEnv(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.home = self.tmpdir / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read(self, p: Path) -> str:
        self.assertTrue(p.exists(), f"expected {p} to exist")
        return p.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # No conflicts → everything emitted
    # ------------------------------------------------------------------
    def test_no_conflicts_emits_export_and_both_functions(self) -> None:
        target = self.tmpdir / "env.sh"
        overrides = self.tmpdir / "local-overrides.sb"
        r = _run_cli(self.home, target=target, overrides=overrides)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertTrue(report["wrote"])
        self.assertEqual(report["rcConflicts"], [])

        content = self._read(target)
        self.assertRegex(content, r"(?m)^export SAFEHOUSE_APPEND_PROFILE=")
        self.assertRegex(content, r"(?m)^safe\(\) \{$")
        self.assertRegex(content, r"(?m)^safe-claude\(\) \{$")
        self.assertIn(str(overrides), content)

    # ------------------------------------------------------------------
    # Default paths
    # ------------------------------------------------------------------
    def test_default_target_and_overrides_paths(self) -> None:
        r = _run_cli(self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        default_target = self.home / ".config" / "agent-safehouse" / "env.sh"
        default_overrides = self.home / ".config" / "agent-safehouse" / "local-overrides.sb"
        self.assertEqual(Path(report["target"]), default_target)
        self.assertEqual(Path(report["overridesStub"]), default_overrides)
        self.assertTrue(default_overrides.exists())

    # ------------------------------------------------------------------
    # rc conflict on SAFEHOUSE_APPEND_PROFILE
    # ------------------------------------------------------------------
    def test_env_in_zshrc_comments_only_export(self) -> None:
        zshrc = self.home / ".zshrc"
        zshrc.write_text(
            'export SAFEHOUSE_APPEND_PROFILE="/some/other/path.sb"\n',
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        items = [c["item"] for c in report["rcConflicts"]]
        self.assertEqual(items, ["SAFEHOUSE_APPEND_PROFILE"])

        content = self._read(target)
        # No active export in the sidecar; the "already exported in <rc>" note
        # plus an "(rc value: ...)" line replaces the previous commented-out
        # export line (which would have shown the wizard's default path, not
        # the rc's actual value — misleading).
        self.assertNotRegex(content, r"(?m)^export SAFEHOUSE_APPEND_PROFILE=")
        self.assertIn("Already exported in", content)
        self.assertIn("rc value:", content)
        self.assertRegex(content, r"(?m)^safe\(\) \{$")
        self.assertRegex(content, r"(?m)^safe-claude\(\) \{$")

    # ------------------------------------------------------------------
    # rc conflict on safe() / safe-claude() functions
    # ------------------------------------------------------------------
    def test_safe_function_in_rc_comments_only_function(self) -> None:
        (self.home / ".zshrc").write_text(
            'safe() { safehouse --append-profile="$HOME/x.sb" "$@"; }\n',
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        items = sorted(c["item"] for c in report["rcConflicts"])
        self.assertEqual(items, ["safe"])

        content = self._read(target)
        self.assertNotRegex(content, r"(?m)^safe\(\) \{$")
        self.assertRegex(content, r"(?m)^# safe\(\) \{$")
        # safe-claude is still active.
        self.assertRegex(content, r"(?m)^safe-claude\(\) \{$")

    def test_safe_claude_function_in_rc_comments_only_safe_claude(self) -> None:
        (self.home / ".zshrc").write_text(
            'safe-claude() { safe claude "$@"; }\n',
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        items = sorted(c["item"] for c in report["rcConflicts"])
        self.assertEqual(items, ["safe-claude"])

        content = self._read(target)
        # safe is active, safe-claude is commented.
        self.assertRegex(content, r"(?m)^safe\(\) \{$")
        self.assertRegex(content, r"(?m)^# safe-claude\(\) \{$")

    def test_all_three_conflicts_all_commented(self) -> None:
        (self.home / ".zshrc").write_text(
            'export SAFEHOUSE_APPEND_PROFILE="/x.sb"\n'
            "safe() { :; }\n"
            "safe-claude() { :; }\n",
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        items = sorted(c["item"] for c in report["rcConflicts"])
        self.assertEqual(items, ["SAFEHOUSE_APPEND_PROFILE", "safe", "safe-claude"])
        content = self._read(target)
        self.assertNotRegex(content, r"(?m)^export SAFEHOUSE_APPEND_PROFILE=")
        self.assertNotRegex(content, r"(?m)^safe\(\) \{$")
        self.assertNotRegex(content, r"(?m)^safe-claude\(\) \{$")

    def test_commented_definitions_do_not_count(self) -> None:
        (self.home / ".zshrc").write_text(
            "# export SAFEHOUSE_APPEND_PROFILE=/old\n"
            "# safe() { :; }\n",
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["rcConflicts"], [])

    def test_substring_mention_is_not_a_conflict(self) -> None:
        """Lines that mention SAFEHOUSE_APPEND_PROFILE in passing must not flag."""
        (self.home / ".zshrc").write_text(
            'echo "set SAFEHOUSE_APPEND_PROFILE if needed"\n'
            'alias unsafe="unset SAFEHOUSE_APPEND_PROFILE"\n',
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["rcConflicts"], [])

    def test_rc_value_shown_in_conflict_comment(self) -> None:
        """When the rc owns SAFEHOUSE_APPEND_PROFILE, the sidecar's note shows the rc's actual value."""
        (self.home / ".zshrc").write_text(
            'export SAFEHOUSE_APPEND_PROFILE="/my/very-specific/policy.sb"\n',
            encoding="utf-8",
        )
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        content = self._read(target)
        self.assertIn('(rc value: "/my/very-specific/policy.sb")', content)

    def test_overrides_path_derived_from_custom_target(self) -> None:
        """When --target is custom but --overrides-file is not, overrides should land beside the sidecar."""
        custom_dir = self.tmpdir / "custom-dir"
        custom_dir.mkdir()
        target = custom_dir / "env.sh"
        r = _run_cli(self.home, target=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        # Overrides stub should land in custom_dir, not in ~/.config/agent-safehouse/.
        self.assertEqual(Path(report["overridesStub"]), custom_dir / "local-overrides.sb")
        self.assertTrue((custom_dir / "local-overrides.sb").exists())
        # Default ~/.config/... must not have been written.
        default_overrides = self.home / ".config" / "agent-safehouse" / "local-overrides.sb"
        self.assertFalse(default_overrides.exists(), "default overrides path should NOT have been touched")

    # ------------------------------------------------------------------
    # local-overrides.sb stub
    # ------------------------------------------------------------------
    def test_overrides_stub_created_when_absent(self) -> None:
        overrides = self.tmpdir / "local-overrides.sb"
        target = self.tmpdir / "env.sh"
        self.assertFalse(overrides.exists())
        r = _run_cli(self.home, target=target, overrides=overrides)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(overrides.exists())
        report = json.loads(r.stdout)
        self.assertEqual(report["overridesStub"], str(overrides))

    def test_overrides_stub_not_overwritten_when_present(self) -> None:
        overrides = self.tmpdir / "local-overrides.sb"
        overrides.write_text(";; my existing policy\n", encoding="utf-8")
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, target=target, overrides=overrides)
        self.assertEqual(r.returncode, 0, r.stderr)
        # File untouched
        self.assertEqual(overrides.read_text(), ";; my existing policy\n")
        report = json.loads(r.stdout)
        self.assertIsNone(report["overridesStub"])

    def test_no_overrides_stub_flag_skips_creation(self) -> None:
        overrides = self.tmpdir / "local-overrides.sb"
        target = self.tmpdir / "env.sh"
        r = _run_cli(self.home, "--no-overrides-stub", target=target, overrides=overrides)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(overrides.exists())
        report = json.loads(r.stdout)
        self.assertIsNone(report["overridesStub"])

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------
    def test_idempotent_byte_identical_rewrites(self) -> None:
        target = self.tmpdir / "env.sh"
        overrides = self.tmpdir / "local-overrides.sb"
        r1 = _run_cli(self.home, target=target, overrides=overrides)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        first = target.read_bytes()
        r2 = _run_cli(self.home, target=target, overrides=overrides)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(first, target.read_bytes())

    # ------------------------------------------------------------------
    # Sourceable check
    # ------------------------------------------------------------------
    def test_sidecar_sources_cleanly_in_bash(self) -> None:
        """Sidecar is sourced from ~/.zshrc / ~/.bashrc, never strict POSIX /bin/sh.

        `safe-claude` is a valid function name in zsh and bash but not in POSIX
        /bin/sh (hyphens disallowed). Test under bash to match real-world usage.
        """
        target = self.tmpdir / "env.sh"
        overrides = self.tmpdir / "local-overrides.sb"
        r = _run_cli(self.home, target=target, overrides=overrides)
        self.assertEqual(r.returncode, 0, r.stderr)
        check = subprocess.run(
            [
                "/bin/bash",
                "-c",
                f". {target} && echo SAP=$SAFEHOUSE_APPEND_PROFILE && type safe && type safe-claude",
            ],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.home)},
            timeout=5,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn(f"SAP={overrides}", check.stdout)
        self.assertIn("safe", check.stdout)
        self.assertIn("safe-claude", check.stdout)


if __name__ == "__main__":
    unittest.main()
