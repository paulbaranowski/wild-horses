#!/usr/bin/env python3
"""Per-repo .plankeeper.json CRUD + redaction (config.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import unittest

from support import (
    IsolatedHomeTestCase,
    run_cli,
)


class TestTicketSystemConfig(IsolatedHomeTestCase):
    def test_save_then_get_redacts_secrets_by_default(self) -> None:
        # Linear: apiKey masked.
        linear_payload = (
            '{"apiKey": "k", "defaults": {"teamId": "t"}, '
            '"cache": {"teams": [{"id": "t", "name": "Eng"}]}}'
        )
        result = run_cli(
            "linear", "config", "save",
            stdin=linear_payload, home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(
            "linear", "config", "get",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiKey"], "***redacted***")
        self.assertEqual(data["defaults"]["teamId"], "t")

        # Jira: apiToken masked.
        jira_payload = (
            '{"site": "x.atlassian.net", "email": "p@x.com", "apiToken": "j", '
            '"defaults": {"projectKey": "HERDS"}}'
        )
        result = run_cli(
            "jira", "config", "save",
            stdin=jira_payload, home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(
            "jira", "config", "get",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiToken"], "***redacted***")
        self.assertEqual(data["defaults"]["projectKey"], "HERDS")

    def test_get_show_secrets_reveals_credentials(self) -> None:
        # Linear: apiKey visible with --show-secrets.
        run_cli(
            "linear", "config", "save",
            stdin='{"apiKey": "k", "defaults": {"teamId": "t"}}',
            home=self.home, cwd=self.cwd,
        )
        result = run_cli(
            "linear", "config", "get", "--show-secrets",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiKey"], "k")

        # Jira: apiToken visible with --show-secrets.
        run_cli(
            "jira", "config", "save",
            stdin='{"site": "x.atlassian.net", "email": "p@x.com", "apiToken": "j"}',
            home=self.home, cwd=self.cwd,
        )
        result = run_cli(
            "jira", "config", "get", "--show-secrets",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiToken"], "j")

    def test_get_missing_system_exits_3(self) -> None:
        result = run_cli(
            "linear", "config", "get",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 3)

    def test_save_sets_chmod_600(self) -> None:
        run_cli("linear", "config", "save",
                stdin='{"apiKey": "k"}', home=self.home, cwd=self.cwd)
        repo_dir = self.plans_root / "workdir"
        config = repo_dir / ".plankeeper.json"
        self.assertTrue(config.exists())
        mode = config.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, oct(mode))

    def test_save_rejects_invalid_json(self) -> None:
        result = run_cli(
            "linear", "config", "save",
            stdin="not json",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
