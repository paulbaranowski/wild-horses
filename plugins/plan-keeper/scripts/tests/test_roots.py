#!/usr/bin/env python3
"""Multiple plan roots: registry (`root` subcommands), save-routing, cross-root
reads/labels, and `move` (roots.py + the CLI wiring on top of it).

Part of the plan_keeper test suite; shared harness lives in support.py.
Subprocess style (isolated $HOME) so the registry file at
$HOME/.config/plan-keeper/config.json is naturally isolated too.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import unittest
from pathlib import Path

from support import IsolatedHomeTestCase, run_cli

# Fixed save date so constructed filenames are deterministic regardless of when
# the suite runs (save without --date would use date.today()).
DATE = "2026-01-02"


class RootTestCase(IsolatedHomeTestCase):
    """Helpers for building a multi-root layout under the isolated $HOME."""

    def _roots(self) -> list[dict]:
        r = run_cli("root", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _add_root(self, name: str, path: str) -> None:
        r = run_cli("root", "add", name, path, home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _save(self, *args: str, body: str = "# T\n") -> str:
        r = run_cli("save", "--date", DATE, *args,
                    stdin=body, home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def _setup_personal_root(self) -> Path:
        """Register the standard second root and return its path (the shared
        fixture for every multi-root test class)."""
        personal = self.home / "personal" / "plans"
        self._add_root("personal", str(personal))
        return personal


class TestRegistry(RootTestCase):
    def test_implicit_default_when_no_config(self) -> None:
        roots = self._roots()
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]["name"], "default")
        self.assertTrue(roots[0]["default"])
        self.assertEqual(roots[0]["path"], str(self.plans_root))

    def test_add_materializes_default_and_creates_dir(self) -> None:
        personal = self.home / "personal" / "plans"
        self._add_root("personal", str(personal))
        roots = self._roots()
        names = {r["name"]: r for r in roots}
        self.assertEqual(set(names), {"default", "personal"})
        self.assertTrue(names["default"]["default"])
        self.assertFalse(names["personal"]["default"])
        # add creates the target dir.
        self.assertTrue(personal.is_dir())

    def test_add_rejects_duplicate_name(self) -> None:
        self._add_root("personal", str(self.home / "p1"))
        r = run_cli("root", "add", "personal", str(self.home / "p2"),
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)

    def test_add_rejects_nested_path_either_direction(self) -> None:
        # New path nested INSIDE the default root.
        r = run_cli("root", "add", "inner", str(self.plans_root / "sub"),
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2, r.stdout)
        # New path CONTAINS the default root.
        r = run_cli("root", "add", "outer", str(self.home),
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_remove_rejects_default_and_last(self) -> None:
        # The lone implicit default can't be removed (it's default AND last).
        r = run_cli("root", "remove", "default", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        self._add_root("personal", str(self.home / "personal"))
        # personal is removable (non-default); default still can't go.
        r = run_cli("root", "remove", "default", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)
        r = run_cli("root", "remove", "personal", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual({r["name"] for r in self._roots()}, {"default"})

    def test_set_default_flips_and_reroutes_saves(self) -> None:
        self._add_root("personal", str(self.home / "personal"))
        r = run_cli("root", "set-default", "personal", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        names = {r["name"]: r for r in self._roots()}
        self.assertTrue(names["personal"]["default"])
        self.assertFalse(names["default"]["default"])
        # A brand-new repo now routes to personal (the new default).
        path = self._save("--topic", "Fresh", "--override", "brandnew")
        self.assertIn("/personal/", path)

    def test_malformed_config_exits_5(self) -> None:
        cfg = self.home / ".config" / "plan-keeper" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("{ not json", encoding="utf-8")
        r = run_cli("root", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 5)


class TestSaveRouting(RootTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.personal = self._setup_personal_root()

    def test_zero_roots_routes_to_default(self) -> None:
        path = self._save("--topic", "New", "--override", "widget")
        self.assertIn(f"{self.plans_root}/widget/", path)

    def test_existing_repo_in_one_root_auto_routes(self) -> None:
        # Seed 'journal' in personal, then a plain save must follow it there.
        self._save("--topic", "First", "--override", "journal", "--root", "personal")
        path = self._save("--topic", "Second", "--override", "journal")
        self.assertIn("/personal/plans/journal/", path)

    def test_straddling_repo_routes_to_default(self) -> None:
        # Force 'shared' into BOTH roots, then a plain save falls back to default.
        self._save("--topic", "A", "--override", "shared", "--root", "personal")
        self._save("--topic", "B", "--override", "shared", "--root", "default")
        # Now shared straddles both roots; the next plain save routes to default.
        path = self._save("--topic", "C", "--override", "shared")
        self.assertIn(f"{self.plans_root}/shared/", path)

    def test_explicit_root_flag_wins(self) -> None:
        path = self._save("--topic", "P", "--override", "anything", "--root", "personal")
        self.assertIn("/personal/plans/anything/", path)

    def test_unknown_root_exits_2(self) -> None:
        r = run_cli("save", "--topic", "X", "--root", "nope",
                    stdin="# X\n", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)


class TestCrossRootReads(RootTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.personal = self._setup_personal_root()
        self._save("--topic", "Work item", "--override", "work-repo")
        self._save("--topic", "Home item", "--override", "home-repo", "--root", "personal")

    def test_list_all_labels_root_when_multiple(self) -> None:
        r = run_cli("list", "--all-repos", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = set(r.stdout.split())
        self.assertIn(f"default/work-repo/{DATE}-work-item.md", lines)
        self.assertIn(f"personal/home-repo/{DATE}-home-item.md", lines)

    def test_list_root_filter_drops_label_and_narrows(self) -> None:
        r = run_cli("list", "--all-repos", "--root", "personal",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.strip()
        # Only personal's plan, and no 'root/' label (single root shown).
        self.assertEqual(out, f"home-repo/{DATE}-home-item.md")

    def test_repo_list_unions_and_labels(self) -> None:
        r = run_cli("repo", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("default/work-repo: active=1", r.stdout)
        self.assertIn("personal/home-repo: active=1", r.stdout)

    def test_queue_list_carries_root_and_unions(self) -> None:
        r = run_cli("crew", "queue", "list", "--all", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        by_root = {row["root"] for row in json.loads(r.stdout)}
        self.assertEqual(by_root, {"default", "personal"})

    def test_queue_add_root_flag_targets_straddling_repos_copy(self) -> None:
        # Same-named plan in BOTH roots' copy of one repo. Without --root the
        # promote resolves via route_root (straddle -> default); with --root
        # personal it must mutate the personal file and leave default alone.
        self._save("--topic", "Same", "--override", "both-repo")
        self._save("--topic", "Same", "--override", "both-repo", "--root", "personal")
        r = run_cli(
            "crew", "queue", "add", f"{DATE}-same.md",
            "--repo", "both-repo", "--root", "personal",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("/personal/plans/both-repo/", r.stdout.strip())
        personal_text = (
            self.personal / "both-repo" / f"{DATE}-same.md"
        ).read_text(encoding="utf-8")
        default_text = (
            self.plans_root / "both-repo" / f"{DATE}-same.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Status: todo", personal_text)
        self.assertNotIn("Status: todo", default_text)

    def test_crew_fetch_unions_agent_tagged_plans(self) -> None:
        # Tag one plan in each root; fetch must surface both.
        run_cli("crew", "queue", "add", f"{DATE}-work-item.md",
                "--repo", "work-repo", home=self.home, cwd=self.cwd)
        run_cli("crew", "queue", "add", f"{DATE}-home-item.md",
                "--repo", "home-repo", home=self.home, cwd=self.cwd)
        r = run_cli("crew", "fetch", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        repos = {issue["repository"] for issue in json.loads(r.stdout)}
        self.assertEqual(repos, {"work-repo", "home-repo"})


class TestConfigUnionRead(RootTestCase):
    def test_load_config_falls_back_across_roots_after_straddle(self) -> None:
        # Creds written while the repo lived only in personal must stay
        # findable after the repo starts straddling (routing then points at
        # the default root, where no config exists).
        self._add_root("personal", str(self.home / "personal" / "plans"))
        # cwd-derived repo is 'workdir'; put it in personal only.
        self._save("--topic", "Seed", "--override", "workdir", "--root", "personal")
        r = run_cli(
            "linear", "config", "save",
            stdin='{"apiKey": "k"}', home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("/personal/plans/workdir/", r.stdout.strip())
        # Straddle: materialize the repo in the default root too.
        self._save("--topic", "Other", "--override", "workdir", "--root", "default")
        r = run_cli("linear", "config", "get", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["apiKey"], "***redacted***")


class TestCrewIdCrossRootCollision(RootTestCase):
    def test_crew_get_refuses_duplicate_id_across_roots(self) -> None:
        # The same (repo, stem) in two roots mints the same id; the resolver
        # must refuse loudly rather than pick whichever root scans first.
        self._add_root("personal", str(self.home / "personal" / "plans"))
        self._save("--topic", "Twin", "--override", "proj", "--root", "default")
        self._save("--topic", "Twin", "--override", "proj", "--root", "personal")
        r = run_cli(
            "file-meta", "get",
            "--file", str(self.plans_root / "proj" / f"{DATE}-twin.md"),
            home=self.home, cwd=self.cwd,
        )
        ticket = json.loads(r.stdout)["Plan-keeper Ticket"]
        r = run_cli("crew", "get", ticket, home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("refusing to pick one", r.stderr)


class TestMove(RootTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.personal = self._setup_personal_root()

    def _ticket(self, path: str) -> str:
        r = run_cli("file-meta", "get", "--file", path, home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["Plan-keeper Ticket"]

    def test_move_relocates_and_keeps_id(self) -> None:
        src = self._save("--topic", "Movable", "--override", "proj", "--root", "personal")
        ticket = self._ticket(src)
        r = run_cli("move", "--file", src, "--root", "default",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        dest = r.stdout.strip()
        self.assertIn(f"{self.plans_root}/proj/", dest)
        self.assertFalse((self.personal / "proj" / f"{DATE}-movable.md").exists())
        # Id is unchanged (root is not in the id seed).
        self.assertEqual(self._ticket(dest), ticket)

    def test_move_preserves_lifecycle_subdir(self) -> None:
        src = self._save("--topic", "Archived", "--override", "proj", "--root", "personal")
        # Send it to done/ (relocates within personal).
        r = run_cli("file-meta", "set", "--file", src, "--status", "done",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        done_src = r.stdout.strip()
        self.assertIn("/personal/plans/proj/done/", done_src)
        r = run_cli("move", "--file", done_src, "--root", "default",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"{self.plans_root}/proj/done/", r.stdout.strip())

    def test_move_takes_paired_sibling(self) -> None:
        # A .json sibling (task-list-builder pair) must travel with the .md.
        md = self._save("--topic", "Paired", "--override", "proj", "--root", "personal")
        json_sib = md[:-3] + ".json"
        # Write a same-base .json next to the .md.
        (self.personal / "proj").mkdir(parents=True, exist_ok=True)
        Path(json_sib).write_text('{"tasks": []}\n', encoding="utf-8")
        r = run_cli("move", "--file", md, "--root", "default",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        base = Path(r.stdout.strip()).stem
        moved_json = self.plans_root / "proj" / (base + ".json")
        self.assertTrue(moved_json.exists())
        self.assertFalse(Path(json_sib).exists())

    def test_move_last_plan_does_not_leave_phantom_straddle(self) -> None:
        # Repo lives in default only; move its only plan to personal. The
        # emptied default dir must not count as "the repo lives here" - the
        # next plain save has to follow the plan to personal, not fall back
        # to default via a phantom straddle.
        src = self._save("--topic", "Solo", "--override", "wander")
        r = run_cli("move", "--file", src, "--root", "personal",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        path = self._save("--topic", "Next", "--override", "wander")
        self.assertIn("/personal/plans/wander/", path)
        # And move's tidy-up removed the emptied source dir outright.
        self.assertFalse((self.plans_root / "wander").exists())

    def test_move_collision_fails_then_suffix(self) -> None:
        # Same-named plan already in the destination root.
        self._save("--topic", "Clash", "--override", "proj")  # default
        src = self._save("--topic", "Clash", "--override", "proj", "--root", "personal")
        r = run_cli("move", "--file", src, "--root", "default",
                    home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 2)  # fail (default policy)
        self.assertIn("collision", r.stderr)
        r = run_cli("move", "--file", src, "--root", "default",
                    "--on-collision", "suffix", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("-2.md", r.stdout.strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
