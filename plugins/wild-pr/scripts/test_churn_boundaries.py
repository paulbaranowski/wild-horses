#!/usr/bin/env python3
"""Tests for churn_boundaries.py.

Stdlib-only - no pytest needed. Run from anywhere:

    python3 plugins/wild-pr/scripts/test_churn_boundaries.py

Each test builds its repo tree in a temp directory. Checked-in fixture trees
would lose node_modules/, build/, dist/, and .env files to ignore rules.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent

sys.path.insert(0, str(HERE))
import churn_boundaries as cb  # noqa: E402


def write_tree(root, files):
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


class BoundaryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "work" / "app"
        self.repo.mkdir(parents=True)

    def checkout(self, path):
        """Make `path` look like a git checkout."""
        (path / ".git").mkdir(parents=True)

    def discover(self, files):
        write_tree(self.repo, files)
        return cb.discover(self.repo, home=self.home)

    def entry(self, entries, name):
        return next(e for e in entries if e["name"] == name)

    def names(self, entries, kind):
        return [e["name"] for e in entries if e["kind"] == kind]


CLAUDE_MD = """\
# App

## Related Repos

- **[`acme/api`](https://github.com/acme/api)**
  (`~/dev/api`): the backend. Source of truth for the endpoints this app calls.
- **[`acme/web`](https://github.com/acme/web.git)**: the frontend.
- `~/dev/cli`: a command-line tool.

## Links

See https://github.com/acme/tools/blob/main/README.md.
"""


class TestReposFromDocs(BoundaryCase):
    def setUp(self):
        super().setUp()
        self.checkout(self.home / "dev" / "api")
        self.checkout(self.home / "dev" / "cli")

    def test_links_and_paths_give_one_entry_per_repo(self):
        entries = self.discover({"CLAUDE.md": CLAUDE_MD})
        self.assertEqual(self.names(entries, "repo"),
                         ["acme/api", "acme/tools", "acme/web", "cli"])

    def test_link_and_path_in_one_list_item_merge(self):
        api = self.entry(self.discover({"CLAUDE.md": CLAUDE_MD}), "acme/api")
        self.assertEqual(api["gh_slug"], "acme/api")
        self.assertEqual(api["local_path"], str(self.home / "dev" / "api"))
        self.assertEqual(api["sources"], ["CLAUDE.md:5", "CLAUDE.md:6"])
        self.assertEqual(list(api), list(cb.FIELDS))

    def test_direction_comes_from_the_whole_list_item(self):
        entries = self.discover({"CLAUDE.md": CLAUDE_MD})
        self.assertEqual(self.entry(entries, "acme/api")["direction"], "provider")
        self.assertEqual(self.entry(entries, "acme/web")["direction"], "consumer")
        self.assertEqual(self.entry(entries, "acme/tools")["direction"], "unknown")
        self.assertEqual(self.entry(entries, "cli")["direction"], "unknown")

    def test_each_table_row_is_its_own_block(self):
        table = ("## Client Repos\n\n| Repo | What |\n| --- | --- |\n"
                 "| [a](https://github.com/acme/mobile) | The mobile app. |\n"
                 "| [b](https://github.com/acme/shell) | A command-line tool. |\n"
                 "| [c](https://github.com/acme/api) | The backend. |\n")
        entries = self.discover({"CLAUDE.md": table})
        self.assertEqual(self.entry(entries, "acme/mobile")["direction"], "consumer")
        self.assertEqual(self.entry(entries, "acme/shell")["direction"], "consumer")
        self.assertEqual(self.entry(entries, "acme/api")["direction"], "provider")

    def test_direction_words_do_not_cross_a_blank_line(self):
        entries = self.discover({"CLAUDE.md": "https://github.com/acme/lib\n\nThe backend is elsewhere.\n"})
        self.assertEqual(self.entry(entries, "acme/lib")["direction"], "unknown")

    def test_link_without_a_checkout_keeps_its_slug(self):
        web = self.entry(self.discover({"CLAUDE.md": CLAUDE_MD}), "acme/web")
        self.assertEqual((web["gh_slug"], web["local_path"]), ("acme/web", None))

    def test_sibling_checkout_is_found_for_a_link(self):
        self.checkout(self.repo.parent / "web")
        web = self.entry(self.discover({"CLAUDE.md": CLAUDE_MD}), "acme/web")
        self.assertEqual(web["local_path"], str(self.repo.parent / "web"))

    def test_missing_path_and_non_repo_directory_are_dropped(self):
        (self.home / "plans").mkdir()
        entries = self.discover({"AGENTS.md": "- `~/dev/gone`: old backend.\n- `~/plans`: notes.\n"})
        self.assertEqual(entries, [])

    def test_links_to_this_repo_and_to_github_site_pages_are_not_boundaries(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin",
                        "git@github.com:acme/app.git"], check=True)
        entries = self.discover({"CLAUDE.md": "Issues: https://github.com/acme/app/issues/1\n"
                                              "Board: https://github.com/orgs/acme/projects/2\n"})
        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()
