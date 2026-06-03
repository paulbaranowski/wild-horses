#!/usr/bin/env python3
"""Stdlib unittest suite for render_config.py.

Tests invoke the CLI as a subprocess so exit codes, stdin/stdout/stderr
separation, and argparse behavior are exercised exactly as a dispatched
agent would see them.

Future enhancement (not implemented here): run `tsc --noEmit` against
rendered files against the real @clipboard-health/groundcrew type definitions
to confirm the output is type-valid TypeScript. Skipped because the package
is not installed globally and `tsc` may not be on PATH for unattended runs.

Run from anywhere:

    python3 plugins/groundcrew-setup/tests/test_render_config.py

Or via unittest discovery:

    python3 -m unittest discover -s plugins/groundcrew-setup/tests -p 'test_render_config.py'
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

CLI = Path(__file__).parent.parent / "scripts" / "render_config.py"


def run_cli(answers: dict[str, Any], target: str) -> subprocess.CompletedProcess:
    """Invoke render_config.py with the given answers dict and target path."""
    return subprocess.run(
        ["python3", str(CLI), "--target", target],
        input=json.dumps(answers),
        capture_output=True,
        text=True,
        timeout=15,
    )


def minimal_answers(**overrides: Any) -> dict[str, Any]:
    """Return the bare minimum valid Answers dict, with optional overrides."""
    base = {
        "workspaceProjectDir": "~/work",
        "knownRepositories": ["owner/repo"],
    }
    base.update(overrides)
    return base


class TestRenderConfig(unittest.TestCase):
    """Each test gets a fresh tmpdir for target paths."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def target(self, name: str = "config.ts") -> str:
        """Return a target path inside the tmpdir."""
        return str(self.tmpdir / name)

    # ------------------------------------------------------------------
    # Test 1: Minimal answers — no optional keys
    # ------------------------------------------------------------------
    def test_minimal_answers_no_optional_keys(self) -> None:
        answers = minimal_answers()
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertNotIn("prompts", content)
        self.assertNotIn("readFileSync", content)
        self.assertNotIn("models", content)
        self.assertNotIn("orchestrator", content)
        self.assertNotIn("logging", content)
        self.assertNotIn("workspaceKind", content)
        self.assertIn("projectDir", content)
        self.assertIn("knownRepositories", content)
        self.assertIn("satisfies Config;", content)

    # ------------------------------------------------------------------
    # Test 2: claudeBypassPermissions: true (no modelClaude.cmd) → claude cmd
    # ------------------------------------------------------------------
    def test_bypass_permissions_emits_claude_cmd(self) -> None:
        answers = minimal_answers(claudeBypassPermissions=True)
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertIn('claude: { cmd: "claude --permission-mode bypassPermissions" }', content)

    # ------------------------------------------------------------------
    # Test 3: claudeBypassPermissions: false, no modelClaude → no claude, no models
    # ------------------------------------------------------------------
    def test_bypass_permissions_off_omits_claude(self) -> None:
        answers = minimal_answers(claudeBypassPermissions=False)
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertNotIn("claude:", content)
        self.assertNotIn("models", content)

    # ------------------------------------------------------------------
    # Test 4: custom modelClaude.cmd overrides bypass default
    # ------------------------------------------------------------------
    def test_custom_claude_cmd_overrides_bypass(self) -> None:
        answers = minimal_answers(
            claudeBypassPermissions=True,
            modelClaude={"cmd": "custom claude command"},
        )
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertIn('"custom claude command"', content)
        self.assertNotIn("bypassPermissions", content)

    # ------------------------------------------------------------------
    # Test 5: modelCodex.cmd emits codex block
    # ------------------------------------------------------------------
    def test_custom_codex_cmd_emits_codex(self) -> None:
        answers = minimal_answers(modelCodex={"cmd": "codex --foo"})
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertIn('codex: { cmd: "codex --foo" }', content)

    # ------------------------------------------------------------------
    # Test 6: sessionLimitPercentage → orchestrator block
    # ------------------------------------------------------------------
    def test_session_limit_emits_orchestrator(self) -> None:
        answers = minimal_answers(sessionLimitPercentage=90)
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertIn("orchestrator: { sessionLimitPercentage: 90 }", content)

    # ------------------------------------------------------------------
    # Test 7: loggingFile → logging block
    # ------------------------------------------------------------------
    def test_logging_file_emits_logging(self) -> None:
        answers = minimal_answers(loggingFile="~/logs/foo.log")
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertIn('logging: { file: "~/logs/foo.log" }', content)

    # ------------------------------------------------------------------
    # Test 8: workspaceKind: "cmux" → top-level workspaceKind key
    # ------------------------------------------------------------------
    def test_workspace_kind_cmux_emits_top_level(self) -> None:
        answers = minimal_answers(workspaceKind="cmux")
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertIn('workspaceKind: "cmux"', content)

    # ------------------------------------------------------------------
    # Test 9: workspaceKind: "auto" → NOT emitted
    # ------------------------------------------------------------------
    def test_workspace_kind_auto_omitted(self) -> None:
        answers = minimal_answers(workspaceKind="auto")
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertNotIn("workspaceKind", content)

    # ------------------------------------------------------------------
    # Test 10: promptFeatures non-empty → readFileSync import + prompts block
    # ------------------------------------------------------------------
    def test_prompt_features_nonempty_emits_readfilesync_and_prompts(self) -> None:
        answers = minimal_answers(promptFeatures=["superpowers"])
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        # stdout should be the target path
        written_path = Path(r.stdout.strip())
        content = written_path.read_text()

        self.assertIn('import { readFileSync } from "node:fs";', content)
        self.assertIn("prompts:", content)
        self.assertIn("readFileSync(new URL", content)
        self.assertIn('"./initial-prompt.md"', content)
        self.assertIn('"utf8"', content)

    # ------------------------------------------------------------------
    # Test 11: promptFeatures empty → no readFileSync, no prompts
    # ------------------------------------------------------------------
    def test_prompt_features_empty_omits_prompts_and_readfilesync(self) -> None:
        answers = minimal_answers(promptFeatures=[])
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertNotIn("readFileSync", content)
        self.assertNotIn("prompts:", content)

    # ------------------------------------------------------------------
    # Test 12: promptFeatures absent (defaults to []) → no readFileSync, no prompts
    # ------------------------------------------------------------------
    def test_prompt_features_absent_omits_prompts_and_readfilesync(self) -> None:
        answers = minimal_answers()
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertNotIn("readFileSync", content)
        self.assertNotIn("prompts:", content)

    # ------------------------------------------------------------------
    # Test 13: invalid workspaceKind → exit 2
    # ------------------------------------------------------------------
    def test_invalid_workspace_kind_exits_2(self) -> None:
        answers = minimal_answers(workspaceKind="fish")
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 2)
        self.assertIn("workspaceKind", r.stderr)
        # Stderr should name the valid set
        self.assertIn("auto", r.stderr)

    # ------------------------------------------------------------------
    # Test 14: missing required field → exit 2
    # ------------------------------------------------------------------
    def test_missing_required_field_exits_2(self) -> None:
        answers = {
            "knownRepositories": ["owner/repo"],
            # workspaceProjectDir intentionally omitted
        }
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 2)
        self.assertIn("workspaceProjectDir", r.stderr)

    # ------------------------------------------------------------------
    # Test 15: sessionLimitPercentage out of range → exit 2
    # ------------------------------------------------------------------
    def test_invalid_session_limit_exits_2(self) -> None:
        answers = minimal_answers(sessionLimitPercentage=150)
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 2)
        self.assertIn("sessionLimitPercentage", r.stderr)
        self.assertIn("1..100", r.stderr)

    # ------------------------------------------------------------------
    # Test 16: atomic write creates parent directories
    # ------------------------------------------------------------------
    def test_atomic_write_creates_parent_dirs(self) -> None:
        nested_target = str(self.tmpdir / "nested" / "path" / "config.ts")
        answers = minimal_answers()
        r = run_cli(answers, nested_target)
        self.assertEqual(r.returncode, 0, r.stderr)

        written = Path(nested_target)
        self.assertTrue(written.exists(), "File should exist after atomic write")
        content = written.read_text()
        self.assertIn("satisfies Config;", content)

    # ------------------------------------------------------------------
    # Test 17: strings with embedded quotes are properly escaped
    # ------------------------------------------------------------------
    def test_strings_with_quotes_escaped(self) -> None:
        answers = minimal_answers(loggingFile='logs/file"with"quotes.log')
        r = run_cli(answers, self.target())
        self.assertEqual(r.returncode, 0, r.stderr)

        content = Path(self.target()).read_text()
        self.assertIn(r'"logs/file\"with\"quotes.log"', content)


if __name__ == "__main__":
    unittest.main()
