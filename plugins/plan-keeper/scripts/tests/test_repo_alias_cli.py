#!/usr/bin/env python3
"""`plan-keeper repo alias add/list/remove` CRUD subcommands.

Exercises the user-facing CLI for editing ~/plans/.plankeeper-global.json's
`aliases` list. Part of the plan_keeper test suite; shared harness lives in
support.py.

Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import unittest

from support import (  # noqa: F401 — also inserts scripts/ onto sys.path
    IsolatedHomeTestCase,
    run_cli,
)


class TestRepoAliasAdd(IsolatedHomeTestCase):
    def _read_global(self) -> dict:
        path = self.plans_root / ".plankeeper-global.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_add_creates_global_config_on_first_use(self) -> None:
        # No ~/plans/ tree yet; add must create the file and the parent dir.
        self.assertFalse(self.plans_root.exists())
        r = run_cli(
            "repo", "alias", "add", "carrot/catalog/flawless-inventory", "maple",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._read_global(), {"aliases": [
            {"remote": "carrot", "subpath": "catalog/flawless-inventory",
             "name": "maple"}
        ]})

    def test_add_splits_first_segment_as_remote_rest_as_subpath(self) -> None:
        # The positional uses the user's slash-separated `<remote>/<subpath>`
        # mental model — first segment is the remote, the rest is the subpath.
        r = run_cli(
            "repo", "alias", "add", "carrot/frontend/web-app", "frontend-web",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._read_global()["aliases"][0], {
            "remote": "carrot",
            "subpath": "frontend/web-app",
            "name": "frontend-web",
        })

    def test_add_repo_root_alias_via_bare_remote(self) -> None:
        # A bare `<remote>` with no subpath registers a repo-root alias
        # (subpath="") — matches at the toplevel.
        r = run_cli(
            "repo", "alias", "add", "carrot", "carrot-aliased",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._read_global()["aliases"][0], {
            "remote": "carrot", "subpath": "", "name": "carrot-aliased",
        })

    def test_add_replaces_existing_remote_subpath_in_place(self) -> None:
        # Idempotent re-run: same (remote, subpath) updates the existing entry
        # rather than appending a duplicate.
        run_cli("repo", "alias", "add", "carrot/catalog/flawless-inventory",
                "old-name", home=self.home, cwd=self.cwd)
        r = run_cli("repo", "alias", "add", "carrot/catalog/flawless-inventory",
                    "new-name", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        aliases = self._read_global()["aliases"]
        self.assertEqual(len(aliases), 1)
        self.assertEqual(aliases[0]["name"], "new-name")

    def test_add_warns_on_duplicate_name_but_succeeds(self) -> None:
        # A different (remote, subpath) mapping to the same name is allowed
        # (user might want two subpaths routed to the same bucket) but the CLI
        # warns on stderr so the user notices.
        run_cli("repo", "alias", "add", "carrot/a", "shared",
                home=self.home, cwd=self.cwd)
        r = run_cli("repo", "alias", "add", "carrot/b", "shared",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning", r.stderr.lower())
        self.assertIn("shared", r.stderr)
        self.assertEqual(len(self._read_global()["aliases"]), 2)

    def test_add_rejects_empty_remote(self) -> None:
        # A leading slash leaves the remote empty after the first split.
        r = run_cli("repo", "alias", "add", "/catalog", "maple",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("remote", r.stderr.lower())

    def test_add_rejects_empty_name(self) -> None:
        r = run_cli("repo", "alias", "add", "carrot/catalog", "",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("name", r.stderr.lower())

    def test_add_rejects_alias_name_with_slash(self) -> None:
        # A name containing `/` would compose a multi-segment path under
        # ~/plans/ — exactly what validate_repo_name guards against on
        # --override / git-remote / cwd. The same fence applies here:
        # malformed-at-write beats fail-loud-at-resolve.
        r = run_cli("repo", "alias", "add", "carrot/catalog", "foo/bar",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("name", r.stderr.lower())

    def test_add_rejects_dot_alias_name(self) -> None:
        r = run_cli("repo", "alias", "add", "carrot/catalog", ".",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("name", r.stderr.lower())

    def test_add_rejects_dotdot_alias_name(self) -> None:
        # `..` would resolve outside ~/plans/<repo>/ and is the canonical
        # path-traversal attack vector. validate_repo_name already rejects it
        # everywhere else; the alias `add` boundary must too.
        r = run_cli("repo", "alias", "add", "carrot/catalog", "..",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("name", r.stderr.lower())

    def test_add_rejects_subpath_with_trailing_slash(self) -> None:
        # `_subpath_from_toplevel` never produces a trailing slash, so a stored
        # `subpath=catalog/` would silently never match. Reject at write rather
        # than letting the alias sit dead in the config.
        r = run_cli("repo", "alias", "add", "carrot/catalog/", "maple",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("subpath", r.stderr.lower())

    def test_add_rejects_subpath_with_empty_segment(self) -> None:
        # `carrot//catalog` splits to remote=carrot, subpath=/catalog — leading
        # slash will never match `_subpath_from_toplevel`'s output.
        r = run_cli("repo", "alias", "add", "carrot//catalog", "maple",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("subpath", r.stderr.lower())

    def test_add_rejects_subpath_with_dotdot_segment(self) -> None:
        # A `..` segment in subpath escapes the intended directory; even though
        # `_subpath_from_toplevel` doesn't produce `..`, allowing it in storage
        # makes the config a path-traversal vector when consumers compose paths.
        r = run_cli("repo", "alias", "add", "carrot/../etc", "maple",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("subpath", r.stderr.lower())

    def test_add_rejects_target_with_only_trailing_slash(self) -> None:
        # `"carrot/"` (target ending in `/` with no subpath after) is a typo:
        # the user typed a slash thinking it was meaningful but the subpath
        # ends up empty. Silently treating it as a repo-root alias hides the
        # mistake. Reject with the same exit-2 contract as the other slash-
        # malformed inputs.
        r = run_cli("repo", "alias", "add", "carrot/", "maple",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("subpath", r.stderr.lower())

    def test_add_rejects_alias_name_with_tab(self) -> None:
        # A tab inside the alias name would corrupt `repo alias list`'s
        # `remote<TAB>subpath<TAB>name` TSV contract — downstream `cut -f3`
        # would see two fields instead of one and silently pick up a
        # mangled bucket identifier. The alias-add boundary must reject
        # control whitespace before it ever reaches disk.
        r = run_cli("repo", "alias", "add", "carrot/catalog", "maple\twest",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("name", r.stderr.lower())

    def test_add_rejects_alias_name_with_newline(self) -> None:
        # `\n` in the name splits into two output rows under `repo alias list`,
        # silently duplicating the entry from a line-based consumer's view.
        r = run_cli("repo", "alias", "add", "carrot/catalog", "maple\nwest",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("name", r.stderr.lower())

    def test_add_dup_warning_for_repo_root_alias_has_no_trailing_slash(self) -> None:
        # Format for the duplicate-name stderr warning when the existing entry
        # is a repo-root alias (subpath=""): the printed identifier should be
        # the bare `<remote>`, NOT `<remote>/` — the trailing slash is a
        # confusing artifact of naive `f"{remote}/{subpath}"` formatting.
        run_cli("repo", "alias", "add", "carrot", "shared",
                home=self.home, cwd=self.cwd)
        r = run_cli("repo", "alias", "add", "carrot/deep", "shared",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning", r.stderr.lower())
        # The bare remote with NO trailing slash should appear; the trailing
        # `carrot/` form is the bug.
        self.assertNotIn("carrot/\n", r.stderr)
        self.assertNotIn("'carrot/'", r.stderr)
        self.assertNotIn("carrot/ ", r.stderr)
        # And the formatted identifier should mention `carrot`.
        self.assertIn("carrot", r.stderr)


class TestRepoAliasList(IsolatedHomeTestCase):
    def _seed(self, aliases: list[dict]) -> None:
        self.plans_root.mkdir(parents=True, exist_ok=True)
        (self.plans_root / ".plankeeper-global.json").write_text(
            json.dumps({"aliases": aliases}), encoding="utf-8"
        )

    def test_list_empty_config_prints_nothing(self) -> None:
        # No file at all -> empty output, exit 0 (matches the rest of the
        # CLI's machine-readable style — no header noise on empty results).
        r = run_cli("repo", "alias", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_list_empty_aliases_list_prints_nothing(self) -> None:
        # Config exists but `aliases` is empty -> same as missing file.
        self._seed([])
        r = run_cli("repo", "alias", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_list_prints_tab_separated_sorted_by_remote_then_subpath(self) -> None:
        # Multiple aliases: sorted by (remote, subpath), one tab-separated row
        # per alias. Sorting is stable for downstream piping into `cut` / `awk`.
        self._seed([
            {"remote": "carrot", "subpath": "frontend/web-app",
             "name": "frontend-web"},
            {"remote": "apple", "subpath": "", "name": "apple-root"},
            {"remote": "carrot", "subpath": "catalog/flawless-inventory",
             "name": "maple"},
        ])
        r = run_cli("repo", "alias", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = r.stdout.splitlines()
        self.assertEqual(rows, [
            "apple\t\tapple-root",
            "carrot\tcatalog/flawless-inventory\tmaple",
            "carrot\tfrontend/web-app\tfrontend-web",
        ])


class TestRepoAliasRemove(IsolatedHomeTestCase):
    def _seed(self, aliases: list[dict]) -> None:
        self.plans_root.mkdir(parents=True, exist_ok=True)
        (self.plans_root / ".plankeeper-global.json").write_text(
            json.dumps({"aliases": aliases}), encoding="utf-8"
        )

    def _read_global(self) -> dict:
        path = self.plans_root / ".plankeeper-global.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_remove_by_name_deletes_entry(self) -> None:
        self._seed([
            {"remote": "carrot", "subpath": "catalog/flawless-inventory",
             "name": "maple"},
            {"remote": "carrot", "subpath": "frontend/web-app",
             "name": "frontend-web"},
        ])
        r = run_cli("repo", "alias", "remove", "maple",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        aliases = self._read_global()["aliases"]
        self.assertEqual(len(aliases), 1)
        self.assertEqual(aliases[0]["name"], "frontend-web")

    def test_remove_missing_name_exits_3(self) -> None:
        # Exit 3 is the "not found" code used elsewhere in the CLI.
        self._seed([
            {"remote": "carrot", "subpath": "catalog/flawless-inventory",
             "name": "maple"},
        ])
        r = run_cli("repo", "alias", "remove", "nonexistent",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 3)
        # State untouched on miss.
        self.assertEqual(len(self._read_global()["aliases"]), 1)

    def test_remove_against_missing_config_exits_3(self) -> None:
        # No global config at all -> nothing to remove -> exit 3.
        r = run_cli("repo", "alias", "remove", "maple",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 3)

    def test_remove_removes_all_matching_when_duplicates_exist(self) -> None:
        # `add` warns but allows duplicate names; remove deletes every entry
        # whose name matches so the same name can't survive a remove.
        self._seed([
            {"remote": "carrot", "subpath": "a", "name": "shared"},
            {"remote": "carrot", "subpath": "b", "name": "shared"},
            {"remote": "carrot", "subpath": "c", "name": "other"},
        ])
        r = run_cli("repo", "alias", "remove", "shared",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        names = [a["name"] for a in self._read_global()["aliases"]]
        self.assertEqual(names, ["other"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
