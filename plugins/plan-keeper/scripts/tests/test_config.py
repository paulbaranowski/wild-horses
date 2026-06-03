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
    def test_list_no_config(self) -> None:
        result = run_cli(
            "ticket-system-config", "list",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), [])

    def test_save_then_get_redacts_secrets_by_default(self) -> None:
        # Linear: apiKey masked.
        linear_payload = (
            '{"apiKey": "k", "defaults": {"teamId": "t"}, '
            '"cache": {"teams": [{"id": "t", "name": "Eng"}]}}'
        )
        result = run_cli(
            "ticket-system-config", "save", "--name", "linear",
            stdin=linear_payload, home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(
            "ticket-system-config", "get", "--name", "linear",
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
            "ticket-system-config", "save", "--name", "jira",
            stdin=jira_payload, home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(
            "ticket-system-config", "get", "--name", "jira",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiToken"], "***redacted***")
        self.assertEqual(data["defaults"]["projectKey"], "HERDS")

    def test_get_show_secrets_reveals_credentials(self) -> None:
        # Linear: apiKey visible with --show-secrets.
        run_cli(
            "ticket-system-config", "save", "--name", "linear",
            stdin='{"apiKey": "k", "defaults": {"teamId": "t"}}',
            home=self.home, cwd=self.cwd,
        )
        result = run_cli(
            "ticket-system-config", "get", "--name", "linear", "--show-secrets",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiKey"], "k")

        # Jira: apiToken visible with --show-secrets.
        run_cli(
            "ticket-system-config", "save", "--name", "jira",
            stdin='{"site": "x.atlassian.net", "email": "p@x.com", "apiToken": "j"}',
            home=self.home, cwd=self.cwd,
        )
        result = run_cli(
            "ticket-system-config", "get", "--name", "jira", "--show-secrets",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["apiToken"], "j")

    def test_get_missing_system_exits_3(self) -> None:
        result = run_cli(
            "ticket-system-config", "get", "--name", "linear",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 3)

    def test_list_after_save_returns_configured(self) -> None:
        run_cli(
            "ticket-system-config", "save", "--name", "linear",
            stdin='{"apiKey": "k"}',
            home=self.home, cwd=self.cwd,
        )
        result = run_cli(
            "ticket-system-config", "list",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), ["linear"])

    def test_save_then_list_two_systems(self) -> None:
        run_cli("ticket-system-config", "save", "--name", "linear",
                stdin='{"apiKey": "k1"}', home=self.home, cwd=self.cwd)
        run_cli("ticket-system-config", "save", "--name", "jira",
                stdin='{"apiToken": "t"}', home=self.home, cwd=self.cwd)
        result = run_cli(
            "ticket-system-config", "list",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(sorted(json.loads(result.stdout)), ["jira", "linear"])

    def test_save_sets_chmod_600(self) -> None:
        run_cli("ticket-system-config", "save", "--name", "linear",
                stdin='{"apiKey": "k"}', home=self.home, cwd=self.cwd)
        repo_dir = self.plans_root / "workdir"
        config = repo_dir / ".plankeeper.json"
        self.assertTrue(config.exists())
        mode = config.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, oct(mode))

    def test_save_rejects_invalid_json(self) -> None:
        result = run_cli(
            "ticket-system-config", "save", "--name", "linear",
            stdin="not json",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
