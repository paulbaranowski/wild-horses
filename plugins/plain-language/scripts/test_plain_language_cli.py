#!/usr/bin/env python3
"""Tests for plain_language_cli.py. Run by path: python3 <this file>."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plain_language_cli as cli

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plain_language_cli.py")


def run_cli(args: list[str], stdin: str | None = None,
            cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, CLI, *args],
        input=stdin, capture_output=True, text=True, cwd=cwd,
    )


class TestFileTyping(unittest.TestCase):
    def test_extension_map_modes(self):
        self.assertEqual(cli.EXTENSIONS[".py"], ("python", "comment"))
        self.assertEqual(cli.EXTENSIONS[".ts"], ("javascript", "comment"))
        self.assertEqual(cli.EXTENSIONS[".sh"], ("shell", "comment"))
        self.assertEqual(cli.EXTENSIONS[".md"], ("markdown", "prose"))
        self.assertEqual(cli.EXTENSIONS[".txt"], ("text", "prose"))

    def test_scan_skips_unknown_extension(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.rs"
            p.write_text("// hi\n")
            proc = run_cli(["scan", str(p)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["skipped_files"], [
                {"path": str(p), "reason": "unknown-extension"}])

    def test_scan_skips_non_utf8(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.txt"
            p.write_bytes(b"\xff\xfe\x00bad")
            report = json.loads(run_cli(["scan", str(p)]).stdout)
            self.assertEqual(report["skipped_files"][0]["reason"], "not-utf8")


if __name__ == "__main__":
    unittest.main()
