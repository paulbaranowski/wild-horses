#!/usr/bin/env python3
"""The PreToolUse allow-hook shell script (plan-keeper-cli-allow.sh).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import unittest

from support import (
    run_allow,
)


class TestAllowScript(unittest.TestCase):
    """Black-box tests for the PreToolUse allow-script's regex."""

    def assert_match(self, cmd: str) -> None:
        out = run_allow(cmd)
        self.assertIn("permissionDecision", out, f"expected match for: {cmd}")

    def assert_no_match(self, cmd: str) -> None:
        out = run_allow(cmd)
        self.assertEqual(out, "", f"unexpected match for: {cmd}")

    # --- Match cases ---

    def test_dev_path_unquoted(self) -> None:
        self.assert_match(
            "python3 /repo/plugins/plan-keeper/scripts/plan_keeper_cli.py list"
        )

    def test_dev_path_double_quoted(self) -> None:
        self.assert_match(
            'python3 "/repo/plugins/plan-keeper/scripts/plan_keeper_cli.py" save'
        )

    def test_dev_path_single_quoted(self) -> None:
        self.assert_match(
            "python3 '/repo/plugins/plan-keeper/scripts/plan_keeper_cli.py' file-meta set --status done --file foo.md"
        )

    def test_installed_cache_path(self) -> None:
        self.assert_match(
            "python3 /home/u/.claude/plugins/cache/wh/plan-keeper/1.1.0/scripts/plan_keeper_cli.py list"
        )

    # --- Non-match cases (the new tighter regex must reject these) ---

    def test_rejects_python_c_exploit(self) -> None:
        """The original substring check would have approved this — the
        tightened regex must not."""
        self.assert_no_match(
            'python3 -c "import os; os.system(\'evil\')" /any/plan-keeper/scripts/plan_keeper_cli.py'
        )

    def test_rejects_python_m_unrelated(self) -> None:
        self.assert_no_match(
            "python3 -m unrelated_module /any/plan-keeper/scripts/plan_keeper_cli.py"
        )

    def test_rejects_other_script_with_token_in_args(self) -> None:
        self.assert_no_match(
            "python3 /a/b/other_script.py /plan-keeper/scripts/plan_keeper_cli.py"
        )

    def test_rejects_python3_version(self) -> None:
        self.assert_no_match("python3 --version")

    def test_rejects_missing_scripts_segment(self) -> None:
        self.assert_no_match(
            "python3 /a/b/plan-keeper/plan_keeper_cli.py list"
        )

    def test_rejects_missing_plan_keeper_segment(self) -> None:
        self.assert_no_match(
            "python3 /a/b/scripts/plan_keeper_cli.py list"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
