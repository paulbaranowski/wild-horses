#!/usr/bin/env python3
"""Linear GraphQL client + cache refresh (linear.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

from support import (
    _import_cli_module,
    storage,
)


class TestTicketApiLinearViewer(unittest.TestCase):
    """Network tests run in-process with urllib patched. No subprocess."""

    def setUp(self) -> None:
        self.cli = _import_cli_module()

    def _mock_urlopen_returning(self, status: int, body: dict):
        """Build a urlopen-style context-manager mock with a fixed JSON body."""
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.status = status
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_viewer_returns_identity_on_200(self) -> None:
        response_body = {
            "data": {"viewer": {"id": "u1", "name": "Paul", "email": "p@x.com"}}
        }
        mock_resp = self._mock_urlopen_returning(200, response_body)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            result = self.cli.linear_viewer(api_key="lin_test_key")
        self.assertEqual(result, {"id": "u1", "name": "Paul", "email": "p@x.com"})
        # Verify the request itself.
        call_args = mock_open.call_args
        req = call_args[0][0]  # first positional arg is the Request object
        self.assertEqual(req.full_url, "https://api.linear.app/graphql")
        self.assertEqual(req.get_header("Authorization"), "lin_test_key")
        body = json.loads(req.data.decode("utf-8"))
        self.assertIn("viewer", body["query"])

    def test_viewer_raises_on_401(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="https://api.linear.app/graphql",
                code=401, msg="Unauthorized", hdrs=Message(), fp=None,
            ),
        ):
            with self.assertRaises(self.cli.PlanKeeperCliError) as ctx:
                self.cli.linear_viewer(api_key="bad_key")
        self.assertEqual(ctx.exception.code, 3)

    def test_viewer_raises_on_network_error(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with self.assertRaises(self.cli.PlanKeeperCliError) as ctx:
                self.cli.linear_viewer(api_key="k")
        self.assertEqual(ctx.exception.code, 4)

class TestTicketApiLinearLists(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()

    def _mock_response(self, body: dict):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.status = 200
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_teams_returns_node_array(self) -> None:
        response = {"data": {"teams": {
            "nodes": [{"id": "t1", "name": "Engineering"}, {"id": "t2", "name": "Design"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch("urllib.request.urlopen", return_value=self._mock_response(response)):
            result = self.cli.linear_teams(api_key="k")
        self.assertEqual(result, [
            {"id": "t1", "name": "Engineering"},
            {"id": "t2", "name": "Design"},
        ])

    def test_teams_paginates_multiple_pages(self) -> None:
        page1 = {"data": {"teams": {
            "nodes": [{"id": "t1", "name": "Engineering"}],
            "pageInfo": {"endCursor": "cur1", "hasNextPage": True},
        }}}
        page2 = {"data": {"teams": {
            "nodes": [{"id": "t2", "name": "Design"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch(
            "urllib.request.urlopen",
            side_effect=[self._mock_response(page1), self._mock_response(page2)],
        ) as mock_open:
            result = self.cli.linear_teams(api_key="k")
        self.assertEqual(len(result), 2)
        self.assertEqual(mock_open.call_count, 2)
        # Second call should pass after=cur1 in its variables.
        second_call_body = json.loads(mock_open.call_args_list[1][0][0].data)
        self.assertEqual(second_call_body["variables"]["after"], "cur1")

    def test_projects_includes_team_ids(self) -> None:
        response = {"data": {"projects": {
            "nodes": [{
                "id": "p1",
                "name": "Backend",
                "teams": {"nodes": [{"id": "t1"}, {"id": "t2"}]},
            }],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch("urllib.request.urlopen", return_value=self._mock_response(response)):
            result = self.cli.linear_projects(api_key="k")
        self.assertEqual(result, [{"id": "p1", "name": "Backend", "teamIds": ["t1", "t2"]}])

    def test_labels_preserves_optional_team_scope(self) -> None:
        response = {"data": {"issueLabels": {
            "nodes": [
                {"id": "l1", "name": "plan", "team": None},  # workspace-wide
                {"id": "l2", "name": "bug",  "team": {"id": "t1"}},  # team-scoped
            ],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch("urllib.request.urlopen", return_value=self._mock_response(response)):
            result = self.cli.linear_labels(api_key="k")
        self.assertEqual(result, [
            {"id": "l1", "name": "plan", "teamId": None},
            {"id": "l2", "name": "bug", "teamId": "t1"},
        ])

    def test_users_returns_name_and_email(self) -> None:
        response = {"data": {"users": {
            "nodes": [{"id": "u1", "name": "Paul", "email": "p@x.com"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch("urllib.request.urlopen", return_value=self._mock_response(response)):
            result = self.cli.linear_users(api_key="k")
        self.assertEqual(result, [{"id": "u1", "name": "Paul", "email": "p@x.com"}])

class TestTicketSystemConfigRefreshLinear(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _import_cli_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.plans_root = self.home / "plans"
        self.cwd = self.home / "workdir"
        self.cwd.mkdir()
        # Tests in this class patch Path.home directly because they
        # call into module-level functions, not subprocess.
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

    def _mock_response(self, body: dict):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.status = 200
        m.read = MagicMock(return_value=json.dumps(body).encode("utf-8"))
        return m

    def test_refresh_writes_all_kinds_into_cache(self) -> None:
        # Seed existing config with credentials and defaults.
        self.cli.save_config("workdir", {"linear": {
            "apiKey": "k",
            "defaults": {"teamId": "t1"},
            "cache": {"refreshedAt": "2020-01-01T00:00:00Z"},
        }})
        teams = {"data": {"teams": {
            "nodes": [{"id": "t1", "name": "Engineering"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        projects = {"data": {"projects": {
            "nodes": [{"id": "p1", "name": "Backend",
                       "teams": {"nodes": [{"id": "t1"}]}}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        labels = {"data": {"issueLabels": {
            "nodes": [{"id": "l1", "name": "plan", "team": None}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        users = {"data": {"users": {
            "nodes": [{"id": "u1", "name": "Paul", "email": "p@x.com"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                self._mock_response(teams),
                self._mock_response(projects),
                self._mock_response(labels),
                self._mock_response(users),
            ],
        ):
            self.cli.refresh_linear_cache(api_key="k")
        config = self.cli.load_config("workdir")
        cache = config["linear"]["cache"]
        self.assertEqual(len(cache["teams"]), 1)
        self.assertEqual(cache["teams"][0]["name"], "Engineering")
        self.assertEqual(len(cache["projects"]), 1)
        self.assertEqual(len(cache["labels"]), 1)
        self.assertEqual(len(cache["users"]), 1)
        # refreshedAt updated to a recent ISO 8601 timestamp.
        self.assertNotEqual(cache["refreshedAt"], "2020-01-01T00:00:00Z")
        self.assertRegex(cache["refreshedAt"], r"\d{4}-\d{2}-\d{2}T")

    def test_refresh_warns_when_defaults_id_missing_from_cache(self) -> None:
        self.cli.save_config("workdir", {"linear": {
            "apiKey": "k",
            "defaults": {"teamId": "t-deleted", "teamName": "Gone"},
        }})
        teams = {"data": {"teams": {
            "nodes": [{"id": "t1", "name": "Engineering"}],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        empty_projects = {"data": {"projects": {
            "nodes": [], "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        labels_empty = {"data": {"issueLabels": {
            "nodes": [], "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        users_empty = {"data": {"users": {
            "nodes": [], "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}}
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                self._mock_response(teams),
                self._mock_response(empty_projects),
                self._mock_response(labels_empty),
                self._mock_response(users_empty),
            ],
        ):
            warnings = self.cli.refresh_linear_cache(api_key="k")
        self.assertTrue(any("t-deleted" in w for w in warnings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
