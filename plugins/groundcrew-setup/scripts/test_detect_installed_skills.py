#!/usr/bin/env python3
"""Stdlib unittest tests for detect_installed_skills.sh.

Run from anywhere:

    python3 plugins/groundcrew-setup/scripts/test_detect_installed_skills.py -v

Or via unittest discovery:

    python3 -m unittest discover -s plugins/groundcrew-setup/scripts \
        -p 'test_detect_installed_skills.py'

Each test sets HOME to an isolated tmpdir, so the script never reads the
user's real ~/.claude directory.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent / "detect_installed_skills.sh"


def run_script(home: Path) -> subprocess.CompletedProcess:
    """Run detect_installed_skills.sh with an isolated HOME."""
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": os.environ["PATH"]},
        timeout=15,
    )


def write_installed_plugins(home: Path, plugins: dict) -> None:
    """Write a well-formed installed_plugins.json under home."""
    plugins_dir = home / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    data = {"version": 2, "plugins": plugins}
    (plugins_dir / "installed_plugins.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def create_glob_marker(home: Path, path_tail: str) -> None:
    """Create a file at home/.claude/plugins/cache/<path_tail>, making parents."""
    target = home / ".claude" / "plugins" / "cache" / path_tail
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()


class TestDetectInstalledSkills(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    # ------------------------------------------------------------------
    # JSON primary-path tests
    # ------------------------------------------------------------------

    def test_both_installed_via_json(self) -> None:
        """Both keys present → both true."""
        write_installed_plugins(
            self.home,
            {
                "superpowers@claude-plugins-official": [{"installed": True}],
                "core@clipboard": [{"installed": True}],
                "other-plugin@somewhere": [],
            },
        )
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertEqual(result, {"superpowers": True, "babysitPr": True})

    def test_neither_installed_via_json(self) -> None:
        """JSON present but only unrelated keys → both false."""
        write_installed_plugins(
            self.home,
            {"some-other-plugin@somewhere": [{"installed": True}]},
        )
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertEqual(result, {"superpowers": False, "babysitPr": False})

    def test_only_superpowers_via_json(self) -> None:
        """Only superpowers key present → superpowers true, babysitPr false."""
        write_installed_plugins(
            self.home,
            {"superpowers@claude-plugins-official": [{"installed": True}]},
        )
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertEqual(result, {"superpowers": True, "babysitPr": False})

    # ------------------------------------------------------------------
    # Missing ~/.claude directory
    # ------------------------------------------------------------------

    def test_no_claude_dir_at_all(self) -> None:
        """HOME is a bare tmpdir with no .claude/ → both false, exit 0, no stderr."""
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        result = json.loads(proc.stdout)
        self.assertEqual(result, {"superpowers": False, "babysitPr": False})

    # ------------------------------------------------------------------
    # Fallback (glob) path tests
    # ------------------------------------------------------------------

    def test_malformed_json_falls_back_to_glob(self) -> None:
        """Malformed JSON triggers fallback; glob finds superpowers marker."""
        plugins_dir = self.home / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "installed_plugins.json").write_text(
            "{not valid json", encoding="utf-8"
        )
        create_glob_marker(
            self.home,
            "foo/superpowers/1.0.0/skills/using-superpowers/SKILL.md",
        )
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertTrue(result["superpowers"])

    def test_json_root_not_dict_falls_back_to_glob(self) -> None:
        """Valid JSON with non-dict root (e.g. array) triggers fallback."""
        plugins_dir = self.home / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "installed_plugins.json").write_text(
            "[]", encoding="utf-8"
        )
        create_glob_marker(
            self.home,
            "foo/superpowers/1.0.0/skills/using-superpowers/SKILL.md",
        )
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertEqual(result, {"superpowers": True, "babysitPr": False})

    def test_json_root_not_dict_no_markers_both_false(self) -> None:
        """Valid JSON with non-dict root and no glob markers → both false."""
        plugins_dir = self.home / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "installed_plugins.json").write_text(
            "[]", encoding="utf-8"
        )
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertEqual(result, {"superpowers": False, "babysitPr": False})

    def test_glob_fallback_when_json_absent(self) -> None:
        """No JSON file; glob markers exist for both skills → both true."""
        create_glob_marker(
            self.home,
            "official/superpowers/2.0.0/skills/using-superpowers/SKILL.md",
        )
        create_glob_marker(
            self.home,
            "clipboard/core/3.4.0/skills/babysit-pr/SKILL.md",
        )
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertEqual(result, {"superpowers": True, "babysitPr": True})

    def test_glob_fallback_babysit_pr_marker(self) -> None:
        """No JSON; only babysit-pr glob marker exists → superpowers false, babysitPr true."""
        create_glob_marker(
            self.home,
            "clipboard/core/3.4.0/skills/babysit-pr/SKILL.md",
        )
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertEqual(result, {"superpowers": False, "babysitPr": True})

    # ------------------------------------------------------------------
    # Exit-code guarantee
    # ------------------------------------------------------------------

    def test_malformed_json_still_exits_zero(self) -> None:
        """Malformed JSON does not cause error exit; fallback is used."""
        plugins_dir = self.home / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "installed_plugins.json").write_text(
            "not valid at all", encoding="utf-8"
        )
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")


if __name__ == "__main__":
    unittest.main()
