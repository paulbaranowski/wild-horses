#!/usr/bin/env python3
"""Smoke tests for compose_initial_prompt.py.

Stdlib-only — no pytest needed. Run from anywhere:

    python3 plugins/groundcrew-setup/tests/test_compose_initial_prompt.py

Or via unittest discovery:

    python3 -m unittest discover -s plugins/groundcrew-setup/tests -p 'test_compose_initial_prompt.py'

Tests invoke the CLI as a subprocess so exit codes, argparse behavior,
and stdout/stderr separation are exercised exactly as a dispatched
agent would see them.

Mirrors the precedent at plugins/wrangle/scripts/test_update_repos_cli.py.
"""

import subprocess
import sys
import unittest
from pathlib import Path

# Module-local import: requires sys.path.insert below.
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from compose_initial_prompt import BASE_CONTEXT_BLOCK, PROMPT_FEATURES

CLI = Path(__file__).parent.parent / "scripts" / "compose_initial_prompt.py"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the CLI as a subprocess."""
    return subprocess.run(
        ["python3", str(CLI), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestComposeInitialPrompt(unittest.TestCase):
    """Test cases for compose_initial_prompt.py."""

    def test_empty_features_returns_baseline_only(self) -> None:
        """--features "" should output exactly BASE_CONTEXT_BLOCK."""
        # With explicit empty string.
        r = run_cli("--features", "")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertEqual(r.stdout, BASE_CONTEXT_BLOCK)

        # With no --features flag at all.
        r = run_cli()
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertEqual(r.stdout, BASE_CONTEXT_BLOCK)

    def test_all_features_includes_each_snippet(self) -> None:
        """All features should produce baseline + every snippet in PROMPT_FEATURES order, separator '\\n\\n'."""
        r = run_cli("--features", "superpowers,babysitPr,codeStylePointer")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")

        # Expected output: baseline + "\n\n" + each snippet in stable order.
        expected = BASE_CONTEXT_BLOCK + "\n\n" + "\n\n".join(
            PROMPT_FEATURES[key] for key in ("superpowers", "babysitPr", "codeStylePointer")
        )
        self.assertEqual(r.stdout, expected)

    def test_argument_order_is_stable(self) -> None:
        """CSV order does not affect output; stable order from PROMPT_FEATURES is used."""
        # First order: superpowers, babysitPr, codeStylePointer.
        r1 = run_cli("--features", "superpowers,babysitPr,codeStylePointer")
        self.assertEqual(r1.returncode, 0, f"stderr: {r1.stderr}")

        # Reversed order: codeStylePointer, babysitPr, superpowers.
        r2 = run_cli("--features", "codeStylePointer,babysitPr,superpowers")
        self.assertEqual(r2.returncode, 0, f"stderr: {r2.stderr}")

        # Outputs must be byte-identical.
        self.assertEqual(r1.stdout, r2.stdout)

    def test_unknown_feature_exits_2(self) -> None:
        """Unknown feature key should exit 2 with helpful error message."""
        r = run_cli("--features", "bogus")
        self.assertEqual(r.returncode, 2)
        self.assertIn("bogus", r.stderr)
        # Stderr should list valid keys.
        for key in PROMPT_FEATURES.keys():
            self.assertIn(key, r.stderr)


if __name__ == "__main__":
    unittest.main()
