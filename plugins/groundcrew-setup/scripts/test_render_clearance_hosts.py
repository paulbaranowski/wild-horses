#!/usr/bin/env python3
"""Tests for render_clearance_hosts.py.

Stdlib-only — no pytest needed. Run from anywhere:

    python3 plugins/groundcrew-setup/scripts/test_render_clearance_hosts.py

Uses --target pointed at paths inside a TemporaryDirectory so the real
~/.config/clearance/ directory is never touched.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Module-local import: resolved at runtime via the sys.path.insert below, so
# pyright (run from the repo root by the lint hook) can't see it statically.
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from render_clearance_hosts import DEFAULT_BODY  # pyright: ignore[reportMissingImports]

CLI = SCRIPTS_DIR / "render_clearance_hosts.py"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(CLI), *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


class TestCreateMode(unittest.TestCase):
    def test_create_when_missing_writes_default_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "personal-allow-hosts"
            r = run_cli("--target", str(target))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(target.exists(), "file should have been created")
            self.assertEqual(target.read_text(encoding="utf-8"), DEFAULT_BODY)
            # stdout should print the path
            self.assertIn(str(target), r.stdout)

    def test_create_refuses_to_overwrite_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "personal-allow-hosts"
            original = "my-host.internal\n"
            target.write_text(original, encoding="utf-8")
            r = run_cli("--target", str(target))
            self.assertEqual(r.returncode, 1)
            # stderr should mention refusing to overwrite
            self.assertTrue(
                "refus" in r.stderr.lower() or "overwrite" in r.stderr.lower(),
                f"expected refusal message in stderr, got: {r.stderr!r}",
            )
            # file content must be unchanged
            self.assertEqual(target.read_text(encoding="utf-8"), original)


class TestAppendMode(unittest.TestCase):
    def test_append_to_missing_creates_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "personal-allow-hosts"
            r = run_cli("--target", str(target), "--append")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), DEFAULT_BODY)

    def test_append_adds_missing_claude_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "personal-allow-hosts"
            target.write_text("example.internal\n", encoding="utf-8")
            r = run_cli("--target", str(target), "--append")
            self.assertEqual(r.returncode, 0, r.stderr)
            content = target.read_text(encoding="utf-8")
            self.assertIn("example.internal", content)
            self.assertIn("downloads.claude.ai", content)
            self.assertIn("mcp-proxy.anthropic.com", content)

    def test_append_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "personal-allow-hosts"
            target.write_text("example.internal\n", encoding="utf-8")
            run_cli("--target", str(target), "--append")
            run_cli("--target", str(target), "--append")
            content = target.read_text(encoding="utf-8")
            count = content.lower().count("downloads.claude.ai")
            self.assertEqual(count, 1, f"expected 1 occurrence, found {count}:\n{content}")

    def test_append_case_insensitive_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "personal-allow-hosts"
            target.write_text("Downloads.Claude.AI\n", encoding="utf-8")
            r = run_cli("--target", str(target), "--append")
            self.assertEqual(r.returncode, 0, r.stderr)
            content = target.read_text(encoding="utf-8")
            # Count all lines matching downloads.claude.ai case-insensitively
            matches = [
                line
                for line in content.splitlines()
                if line.strip().lower() == "downloads.claude.ai"
            ]
            self.assertEqual(
                len(matches),
                1,
                f"expected exactly 1 line matching downloads.claude.ai (case-insensitive), got {len(matches)}:\n{content}",
            )

    def test_append_commented_host_still_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "personal-allow-hosts"
            target.write_text("#downloads.claude.ai\n", encoding="utf-8")
            r = run_cli("--target", str(target), "--append")
            self.assertEqual(r.returncode, 0, r.stderr)
            content = target.read_text(encoding="utf-8")
            # An uncommented downloads.claude.ai line must be present
            uncommented = [
                line
                for line in content.splitlines()
                if line.strip().lower() == "downloads.claude.ai"
            ]
            self.assertGreater(
                len(uncommented),
                0,
                f"expected uncommented downloads.claude.ai to be added, got:\n{content}",
            )

    def test_append_both_present_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "personal-allow-hosts"
            already = (
                "# Claude Code runtime\n"
                "downloads.claude.ai\n"
                "mcp-proxy.anthropic.com\n"
            )
            target.write_text(already, encoding="utf-8")
            r = run_cli("--target", str(target), "--append")
            self.assertEqual(r.returncode, 0, r.stderr)
            content = target.read_text(encoding="utf-8")
            self.assertEqual(content, already, "content should be unchanged (no-op)")
            # No duplicate section comment
            self.assertEqual(
                content.count("# Claude Code runtime"),
                1,
                f"section comment duplicated:\n{content}",
            )

    def test_append_partial_section_no_duplicate_comment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "personal-allow-hosts"
            # Pre-create with section comment + ONE host present, other missing
            target.write_text(
                "# Claude Code runtime\ndownloads.claude.ai\n",
                encoding="utf-8",
            )
            r = run_cli("--target", str(target), "--append")
            self.assertEqual(r.returncode, 0, r.stderr)
            content = target.read_text(encoding="utf-8")
            # mcp-proxy.anthropic.com should now be present
            self.assertIn("mcp-proxy.anthropic.com", content)
            # Section comment must appear EXACTLY ONCE
            self.assertEqual(
                content.count("# Claude Code runtime"),
                1,
                f"section comment should appear exactly once, got:\n{content}",
            )

    def test_append_to_file_without_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "personal-allow-hosts"
            # Pre-create with NO trailing newline
            target.write_text("example.internal", encoding="utf-8")
            r = run_cli("--target", str(target), "--append")
            self.assertEqual(r.returncode, 0, r.stderr)
            content = target.read_text(encoding="utf-8")
            # Both hosts must be present
            self.assertIn("downloads.claude.ai", content)
            self.assertIn("mcp-proxy.anthropic.com", content)
            # Lines must not be mashed together
            self.assertNotIn("example.internaldownloads", content)
            # Verify they're on separate lines
            lines = content.splitlines()
            self.assertIn("example.internal", lines)
            self.assertIn("downloads.claude.ai", lines)


if __name__ == "__main__":
    unittest.main()
