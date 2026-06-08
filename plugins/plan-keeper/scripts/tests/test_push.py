#!/usr/bin/env python3
"""The push subcommand backend, Linear and Jira (push.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from support import (
    _import_cli_module,
    storage,
)


class TestPushLinearCreate(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.plans_root = self.home / "plans"
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.cwd, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin",
             "https://github.com/herds-social/herds.git"],
            cwd=self.cwd, check=True,
        )
        self._home_patch = patch.object(self.cli.Path, "home", return_value=self.home)
        self._home_patch.start()
        self._cwd_patch = patch("os.getcwd", return_value=str(self.cwd))
        self._cwd_patch.start()
        # Relocate the plans root to the tempdir. PLAN_ROOT is frozen at import
        # from Path.home(), so patching Path.home alone leaves it pointing at
        # the real ~/plans/; patch the constant directly at its single source.
        self._root_patch = patch.object(storage, "PLAN_ROOT", self.home / "plans")
        self._root_patch.start()

    def tearDown(self) -> None:
        self._home_patch.stop()
        self._cwd_patch.stop()
        self._root_patch.stop()
        self._tmp.cleanup()

    def _seed_config(self):
        self.cli.save_config("herds", {"linear": {
            "apiKey": "lin_test",
            "defaults": {
                "teamId": "t1", "teamName": "Engineering",
                "projectId": "p1", "projectName": "Backend",
                "assigneeId": "u1", "assigneeName": "Paul",
                "labelIds": ["l1"], "labelNames": ["plan"],
            },
            "cache": {"refreshedAt": "2026-05-20T00:00:00Z"},
        }})

    def _seed_plan(self, frontmatter: str = "", h1: str = "# Multi-Event Design"):
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        path = repo_dir / "2026-05-20-multi-event-design.md"
        path.write_text(f"{frontmatter}{h1}\n\n## Context\n\nBody text.\n", encoding="utf-8")
        return path

    def _mock_create_response(self):
        body = {"data": {"issueCreate": {
            "success": True,
            "issue": {
                "id": "uuid-1",
                "identifier": "ENG-123",
                "url": "https://linear.app/herds/issue/ENG-123/multi-event-design",
                "title": "Multi-Event Design",
            },
        }}}
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_create_sends_expected_payload(self) -> None:
        self._seed_config()
        path = self._seed_plan()
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_create_response(),
        ) as mock_open:
            result = self.cli.push_subcommand(name="linear", file_path=str(path), force_new=False)
        self.assertEqual(result["action"], "create")
        self.assertEqual(result["id"], "ENG-123")
        # Inspect the GraphQL request payload.
        call = mock_open.call_args
        req = call[0][0]
        sent = json.loads(req.data.decode("utf-8"))
        self.assertIn("issueCreate", sent["query"])
        variables_input = sent["variables"]["input"]
        self.assertEqual(variables_input["title"], "Multi-Event Design")
        self.assertEqual(variables_input["teamId"], "t1")
        self.assertEqual(variables_input["projectId"], "p1")
        self.assertEqual(variables_input["assigneeId"], "u1")
        self.assertEqual(variables_input["labelIds"], ["l1"])
        # Description must start with "Repo: ..." line.
        self.assertTrue(variables_input["description"].startswith("Repo: herds-social/herds\n"))
        # And contain the plan body.
        self.assertIn("## Context", variables_input["description"])

    def test_create_strips_existing_frontmatter_from_description(self) -> None:
        self._seed_config()
        path = self._seed_plan(frontmatter="---\nTicket: \nTicket System: \n---\n\n")
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_create_response(),
        ) as mock_open:
            self.cli.push_subcommand(name="linear", file_path=str(path), force_new=False)
        sent = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        # The "---" lines must not appear in the description.
        self.assertNotIn("---", sent["variables"]["input"]["description"])

    def test_create_aborts_if_description_exceeds_limit(self) -> None:
        self._seed_config()
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        big_body = "x" * 70_000
        path = repo_dir / "2026-05-20-big.md"
        path.write_text(f"# Big\n\n{big_body}\n", encoding="utf-8")
        with self.assertRaises(self.cli.PlanKeeperCliError) as ctx:
            self.cli.push_subcommand(name="linear", file_path=str(path), force_new=False)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("65000", str(ctx.exception))

class TestPushLinearUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.plans_root = self.home / "plans"
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.cwd, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin",
             "https://github.com/herds-social/herds.git"],
            cwd=self.cwd, check=True,
        )
        self._home_patch = patch.object(self.cli.Path, "home", return_value=self.home)
        self._home_patch.start()
        self._cwd_patch = patch("os.getcwd", return_value=str(self.cwd))
        self._cwd_patch.start()
        # Relocate the plans root to the tempdir. PLAN_ROOT is frozen at import
        # from Path.home(), so patching Path.home alone leaves it pointing at
        # the real ~/plans/; patch the constant directly at its single source.
        self._root_patch = patch.object(storage, "PLAN_ROOT", self.home / "plans")
        self._root_patch.start()
        self.cli.save_config("herds", {"linear": {
            "apiKey": "lin_test",
            "defaults": {"teamId": "t1"},
            "cache": {"refreshedAt": "now"},
        }})

    def tearDown(self) -> None:
        self._home_patch.stop()
        self._cwd_patch.stop()
        self._root_patch.stop()
        self._tmp.cleanup()

    def _mock_update_response(self):
        body = {"data": {"issueUpdate": {
            "success": True,
            "issue": {
                "id": "uuid-1",
                "identifier": "ENG-123",
                "url": "https://linear.app/herds/issue/ENG-123/foo",
                "title": "Updated Title",
            },
        }}}
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_update_omits_team_project_assignee_labels(self) -> None:
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        path = repo_dir / "plan.md"
        path.write_text(
            "---\nLinear Ticket: ENG-123\n---\n\n"
            "# Updated Title\n\n## Body\n",
            encoding="utf-8",
        )
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_update_response(),
        ) as mock_open:
            result = self.cli.push_subcommand(name="linear", file_path=str(path), force_new=False)
        self.assertEqual(result["action"], "update")
        self.assertEqual(result["id"], "ENG-123")
        sent = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        self.assertIn("issueUpdate", sent["query"])
        input_dict = sent["variables"]["input"]
        self.assertEqual(set(input_dict.keys()), {"title", "description"})  # nothing else
        self.assertEqual(sent["variables"]["id"], "ENG-123")

    def test_update_detects_legacy_schema_via_migration(self) -> None:
        # A plan still on the old Ticket/Ticket System schema must be recognized
        # as an existing Linear ticket (migrated in-memory at parse) and updated.
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        path = repo_dir / "plan.md"
        path.write_text(
            "---\nTicket: ENG-123\nTicket System: linear\n---\n\n# T\n\n## Body\n",
            encoding="utf-8",
        )
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_update_response(),
        ):
            result = self.cli.push_subcommand(name="linear", file_path=str(path), force_new=False)
        self.assertEqual(result["action"], "update")
        self.assertEqual(result["id"], "ENG-123")

    def test_update_uses_force_new_when_set(self) -> None:
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        path = repo_dir / "plan.md"
        path.write_text(
            "---\nLinear Ticket: OLD-1\n---\n\n# T\n",
            encoding="utf-8",
        )
        # With force_new=True, this should call create, not update.
        body = {"data": {"issueCreate": {
            "success": True, "issue": {
                "id": "u2", "identifier": "ENG-200",
                "url": "https://x", "title": "T",
            },
        }}}
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=m) as mock_open:
            result = self.cli.push_subcommand(name="linear", file_path=str(path), force_new=True)
        self.assertEqual(result["action"], "create")
        sent = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        self.assertIn("issueCreate", sent["query"])

class TestPushJira(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.plans_root = self.home / "plans"
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.cwd, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin",
             "https://github.com/herds-social/herds.git"],
            cwd=self.cwd, check=True,
        )
        self._home_patch = patch.object(self.cli.Path, "home", return_value=self.home)
        self._home_patch.start()
        self._cwd_patch = patch("os.getcwd", return_value=str(self.cwd))
        self._cwd_patch.start()
        # Relocate the plans root to the tempdir. PLAN_ROOT is frozen at import
        # from Path.home(), so patching Path.home alone leaves it pointing at
        # the real ~/plans/; patch the constant directly at its single source.
        self._root_patch = patch.object(storage, "PLAN_ROOT", self.home / "plans")
        self._root_patch.start()
        self.cli.save_config("herds", {"jira": {
            "site": "herds.atlassian.net",
            "email": "p@x.com",
            "apiToken": "tok",
            "defaults": {
                "projectKey": "HERDS",
                "componentIds": ["10001"], "componentNames": ["Backend"],
                "assigneeAccountId": "5e8f", "assigneeName": "Paul",
                "issueType": "Task",
                "labels": ["plan"],
            },
        }})

    def tearDown(self) -> None:
        self._home_patch.stop()
        self._cwd_patch.stop()
        self._root_patch.stop()
        self._tmp.cleanup()

    def _mock_create_response(self):
        body = {"key": "HERDS-100", "id": "9999"}
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def _mock_update_response(self):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=b"")  # 204 No Content has empty body
        return m

    def test_create_wraps_body_in_adf_paragraph(self) -> None:
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        path = repo_dir / "plan.md"
        path.write_text("# Title\n\n## Body\n\nWords.\n", encoding="utf-8")
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_create_response(),
        ) as mock_open:
            result = self.cli.push_subcommand(name="jira", file_path=str(path), force_new=False)
        self.assertEqual(result["action"], "create")
        self.assertEqual(result["id"], "HERDS-100")
        self.assertEqual(result["url"], "https://herds.atlassian.net/browse/HERDS-100")
        sent = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
        # ADF is a JSON object with type=doc, content=[paragraph], text=our composed desc.
        adf = sent["fields"]["description"]
        self.assertEqual(adf["type"], "doc")
        self.assertEqual(adf["content"][0]["type"], "paragraph")
        adf_text = adf["content"][0]["content"][0]["text"]
        self.assertTrue(adf_text.startswith("Repo: herds-social/herds\n"))
        self.assertIn("## Body", adf_text)
        # Project + components + assignee + issue type + labels all sent.
        self.assertEqual(sent["fields"]["project"]["key"], "HERDS")
        self.assertEqual(sent["fields"]["components"], [{"id": "10001"}])
        self.assertEqual(sent["fields"]["assignee"]["accountId"], "5e8f")
        self.assertEqual(sent["fields"]["issuetype"]["name"], "Task")
        self.assertEqual(sent["fields"]["labels"], ["plan"])

    def test_update_omits_components_assignee_labels(self) -> None:
        repo_dir = self.plans_root / "herds"
        repo_dir.mkdir(parents=True, exist_ok=True)
        path = repo_dir / "plan.md"
        path.write_text(
            "---\nJira Ticket: HERDS-100\n---\n\n# T\n",
            encoding="utf-8",
        )
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_update_response(),
        ) as mock_open:
            result = self.cli.push_subcommand(name="jira", file_path=str(path), force_new=False)
        self.assertEqual(result["action"], "update")
        self.assertEqual(result["id"], "HERDS-100")
        # Request was a PUT to /rest/api/3/issue/HERDS-100
        req = mock_open.call_args[0][0]
        self.assertEqual(req.method, "PUT")
        self.assertIn("/rest/api/3/issue/HERDS-100", req.full_url)
        sent = json.loads(req.data.decode("utf-8"))
        # Only summary + description, nothing else.
        self.assertEqual(set(sent["fields"].keys()), {"summary", "description"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
