#!/usr/bin/env python3
"""Jira REST client + cache refresh (jira.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from support import (
    _import_cli_module,
    storage,
)


class TestTicketApiJiraViewer(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()

    def _mock_response(self, body: dict):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_viewer_calls_myself_and_returns_identity(self) -> None:
        response = {
            "accountId": "5e8f", "emailAddress": "p@x.com", "displayName": "Paul",
        }
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_response(response),
        ) as mock_open:
            result = self.cli.jira_viewer(
                site="herds.atlassian.net", email="p@x.com", api_token="tok",
            )
        self.assertEqual(result, response)
        req = mock_open.call_args[0][0]
        self.assertEqual(req.full_url, "https://herds.atlassian.net/rest/api/3/myself")
        # Basic auth header present.
        self.assertTrue(req.get_header("Authorization").startswith("Basic "))
        encoded = req.get_header("Authorization")[len("Basic "):]
        decoded = base64.b64decode(encoded).decode("utf-8")
        self.assertEqual(decoded, "p@x.com:tok")

class TestTicketApiJiraLists(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self.site, self.email, self.token = "herds.atlassian.net", "p@x.com", "tok"

    def _mock_response(self, body):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_projects_paginates(self) -> None:
        page1 = {
            "values": [{"key": "HERDS", "id": "1", "name": "Herds"}],
            "isLast": False,
            "startAt": 0,
            "maxResults": 1,
            "total": 2,
        }
        page2 = {
            "values": [{"key": "INT", "id": "2", "name": "Internal"}],
            "isLast": True,
            "startAt": 1,
            "maxResults": 1,
            "total": 2,
        }
        with patch(
            "urllib.request.urlopen",
            side_effect=[self._mock_response(page1), self._mock_response(page2)],
        ) as mock_open:
            result = self.cli.jira_projects(self.site, self.email, self.token)
        self.assertEqual(len(result), 2)
        self.assertEqual(mock_open.call_count, 2)
        # Second call should have startAt=50 (pagination uses page size 50 in helper)
        url2 = mock_open.call_args_list[1][0][0].full_url
        self.assertIn("startAt=50", url2)

    def test_components_per_project(self) -> None:
        response = [{"id": "10001", "name": "Backend"}]
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_response(response),
        ) as mock_open:
            result = self.cli.jira_components(self.site, self.email, self.token, "HERDS")
        self.assertEqual(result, [{"id": "10001", "name": "Backend", "projectKey": "HERDS"}])
        self.assertIn("/project/HERDS/components", mock_open.call_args[0][0].full_url)

    def test_users_per_project(self) -> None:
        response = [
            {"accountId": "5e8f", "displayName": "Paul", "emailAddress": "p@x.com"},
        ]
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_response(response),
        ) as mock_open:
            result = self.cli.jira_users(self.site, self.email, self.token, "HERDS")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["accountId"], "5e8f")
        self.assertIn(
            "/user/assignable/multiProjectSearch",
            mock_open.call_args[0][0].full_url,
        )
        self.assertIn("projectKeys=HERDS", mock_open.call_args[0][0].full_url)

    def test_issuetypes_per_project(self) -> None:
        response = [{"id": "10001", "name": "Task"}]
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_response(response),
        ) as mock_open:
            result = self.cli.jira_issuetypes(self.site, self.email, self.token, "1")
        self.assertEqual(result, [{"id": "10001", "name": "Task", "projectId": "1"}])
        self.assertIn("projectId=1", mock_open.call_args[0][0].full_url)

class TestTicketSystemConfigRefreshJira(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()
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

    def _mock_response(self, body):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_refresh_populates_jira_cache(self) -> None:
        self.cli.save_config("workdir", {"jira": {
            "site": "herds.atlassian.net",
            "email": "p@x.com",
            "apiToken": "tok",
            "defaults": {"projectKey": "HERDS"},
        }})
        # The refresh fetches: projects, then for each project: components, users, issuetypes.
        # Assume one project to keep the test tractable.
        projects = {
            "values": [{"key": "HERDS", "id": "1", "name": "Herds"}],
            "isLast": True, "startAt": 0, "maxResults": 1, "total": 1,
        }
        components = [{"id": "10001", "name": "Backend"}]
        users = []  # empty for simplicity
        issuetypes = [{"id": "20001", "name": "Task"}]
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                self._mock_response(projects),
                self._mock_response(components),
                self._mock_response(users),
                self._mock_response(issuetypes),
            ],
        ):
            self.cli.refresh_jira_cache(site="herds.atlassian.net", email="p@x.com", api_token="tok")
        config = self.cli.load_config("workdir")
        cache = config["jira"]["cache"]
        self.assertEqual(len(cache["projects"]), 1)
        self.assertEqual(cache["components"][0]["projectKey"], "HERDS")
        self.assertEqual(cache["issueTypes"][0]["name"], "Task")
        self.assertRegex(cache["refreshedAt"], r"\d{4}-\d{2}-\d{2}T")


if __name__ == "__main__":
    unittest.main(verbosity=2)
