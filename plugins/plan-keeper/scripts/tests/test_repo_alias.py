#!/usr/bin/env python3
"""`plan-keeper repo name` alias resolution against ~/plans/.plankeeper-global.json.

Exercises the monorepo-subpath -> groundcrew-alias step the CLI inserts
between the git-remote step and the PWD-basename fallback. Part of the
plan_keeper test suite; shared harness lives in support.py.

Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import subprocess
import unittest

from support import (  # noqa: F401 — also inserts scripts/ onto sys.path
    IsolatedHomeTestCase,
    run_cli,
)


class TestRepoNameAliasResolution(IsolatedHomeTestCase):
    def _init_monorepo(self, remote_url: str = "git@github.com:acme/carrot.git") -> None:
        """Init a git repo at self.cwd with `origin` pointing at `remote_url`."""
        subprocess.run(["git", "init", "-q"], cwd=self.cwd, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=self.cwd,
            check=True,
        )

    def _subdir(self, *parts: str):
        """Create (mkdir -p) and return a subpath of self.cwd."""
        path = self.cwd.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_global_config(self, data: dict) -> None:
        """Write ~/plans/.plankeeper-global.json under the isolated $HOME."""
        self.plans_root.mkdir(parents=True, exist_ok=True)
        (self.plans_root / ".plankeeper-global.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def test_matching_alias_returns_alias_name(self) -> None:
        # cwd is carrot/catalog/flawless-inventory under the monorepo "carrot";
        # the global config maps that exact subpath to "maple", so the resolved
        # repo name is "maple" rather than "carrot".
        self._init_monorepo()
        deep = self._subdir("catalog", "flawless-inventory")
        self._write_global_config({"aliases": [
            {"remote": "carrot", "subpath": "catalog/flawless-inventory",
             "name": "maple"},
        ]})
        r = run_cli("repo", "name", home=self.home, cwd=deep)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "maple")

    def test_longest_prefix_wins(self) -> None:
        # Both `catalog` and `catalog/flawless-inventory` are aliased. From
        # carrot/catalog/flawless-inventory/sub, the longer prefix wins.
        self._init_monorepo()
        deep = self._subdir("catalog", "flawless-inventory", "sub")
        self._write_global_config({"aliases": [
            {"remote": "carrot", "subpath": "catalog", "name": "catalog-all"},
            {"remote": "carrot", "subpath": "catalog/flawless-inventory",
             "name": "maple"},
        ]})
        r = run_cli("repo", "name", home=self.home, cwd=deep)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "maple")

    def test_boundary_aligned_prefix_does_not_overreach(self) -> None:
        # The alias on `catalog/flawless-inventory` must NOT match a cwd of
        # `catalog/flawless-inventory-archive` (sibling, not a child).
        self._init_monorepo()
        sibling = self._subdir("catalog", "flawless-inventory-archive")
        self._write_global_config({"aliases": [
            {"remote": "carrot", "subpath": "catalog/flawless-inventory",
             "name": "maple"},
        ]})
        r = run_cli("repo", "name", home=self.home, cwd=sibling)
        self.assertEqual(r.returncode, 0, r.stderr)
        # No alias matched — falls back to the bare remote basename.
        self.assertEqual(r.stdout.strip(), "carrot")

    def test_empty_subpath_alias_matches_at_toplevel(self) -> None:
        # A repo-root alias has subpath "" — it matches when cwd is the git
        # toplevel itself.
        self._init_monorepo()
        self._write_global_config({"aliases": [
            {"remote": "carrot", "subpath": "", "name": "carrot-aliased"},
        ]})
        r = run_cli("repo", "name", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "carrot-aliased")

    def test_no_matching_alias_falls_back_to_bare_remote(self) -> None:
        # cwd is in a subpath the config doesn't mention -> bare remote.
        self._init_monorepo()
        elsewhere = self._subdir("services", "billing")
        self._write_global_config({"aliases": [
            {"remote": "carrot", "subpath": "catalog/flawless-inventory",
             "name": "maple"},
        ]})
        r = run_cli("repo", "name", home=self.home, cwd=elsewhere)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "carrot")

    def test_remote_mismatch_falls_back_to_bare_remote(self) -> None:
        # Subpath matches the alias's subpath but the remote does not -> no
        # match. The alias entry's (remote, subpath) tuple is the join key.
        self._init_monorepo("git@github.com:acme/other-monorepo.git")
        deep = self._subdir("catalog", "flawless-inventory")
        self._write_global_config({"aliases": [
            {"remote": "carrot", "subpath": "catalog/flawless-inventory",
             "name": "maple"},
        ]})
        r = run_cli("repo", "name", home=self.home, cwd=deep)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "other-monorepo")

    def test_missing_global_config_falls_back_to_bare_remote(self) -> None:
        # No ~/plans/.plankeeper-global.json at all -> behaves exactly like
        # today: bare-remote derivation, no error.
        self._init_monorepo()
        deep = self._subdir("catalog", "flawless-inventory")
        r = run_cli("repo", "name", home=self.home, cwd=deep)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "carrot")

    def test_empty_aliases_list_falls_back_to_bare_remote(self) -> None:
        # Same as missing file: no aliases to match against.
        self._init_monorepo()
        deep = self._subdir("catalog", "flawless-inventory")
        self._write_global_config({"aliases": []})
        r = run_cli("repo", "name", home=self.home, cwd=deep)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "carrot")

    def test_not_in_git_repo_falls_back_to_cwd_basename(self) -> None:
        # No git context -> alias step is skipped silently (no `remote` to
        # match); the algorithm falls all the way back to PWD basename.
        self._write_global_config({"aliases": [
            {"remote": "anything", "subpath": "anywhere", "name": "alias"},
        ]})
        r = run_cli("repo", "name", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "workdir")

    def test_override_short_circuits_alias_resolution(self) -> None:
        # The explicit --override is always step 1 of derive_repo — alias
        # resolution must not override the user's deliberate choice.
        self._init_monorepo()
        deep = self._subdir("catalog", "flawless-inventory")
        self._write_global_config({"aliases": [
            {"remote": "carrot", "subpath": "catalog/flawless-inventory",
             "name": "maple"},
        ]})
        r = run_cli("repo", "name", "--override", "general",
                    home=self.home, cwd=deep)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "general")

    def test_repo_root_alias_matches_deep_cwd_as_last_resort(self) -> None:
        # A user with ONLY a repo-root alias configured (`subpath=""`) deep
        # inside the monorepo: the empty-prefix is the final entry in the
        # prefix walk, so it absorbs deep paths when no longer prefix matches.
        # Locks in the deliberate "root alias is the catch-all" behavior so a
        # future change can't silently demote it to a toplevel-only match.
        self._init_monorepo()
        deep = self._subdir("catalog", "foo", "bar")
        self._write_global_config({"aliases": [
            {"remote": "carrot", "subpath": "", "name": "carrot-root"},
        ]})
        r = run_cli("repo", "name", home=self.home, cwd=deep)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "carrot-root")

    def test_malformed_global_config_warns_on_stderr_and_falls_back(self) -> None:
        # A corrupted global config (the documented "silent corruption across
        # 19 iterations" failure class from CLAUDE.md) must NOT silently route
        # plans to the wrong bucket. derive_repo's contract still requires
        # returning *some* name (so `plan-save` doesn't crash), but the user
        # has to see the warning the next time they run anything that resolves
        # the repo — otherwise the corruption survives undetected.
        self._init_monorepo()
        deep = self._subdir("catalog", "flawless-inventory")
        self.plans_root.mkdir(parents=True, exist_ok=True)
        (self.plans_root / ".plankeeper-global.json").write_text(
            "not json at all", encoding="utf-8"
        )
        r = run_cli("repo", "name", home=self.home, cwd=deep)
        # Fallback: bare remote, exit 0. derive_repo's contract is preserved.
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "carrot")
        # But the user is told about the corruption.
        self.assertIn("warning", r.stderr.lower())
        self.assertIn("global config", r.stderr.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
