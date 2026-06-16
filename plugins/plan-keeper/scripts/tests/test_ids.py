#!/usr/bin/env python3
"""Tests for plan_keeper.ids — the centralized plan-identity module.

Covers the seed-derivation chokepoint (`id_for_path`) and the mint-once helpers
(`ensure_id`, `mint_into_path_if_absent`). The pure algorithm + shape/determinism
contract is exercised via `cli.plankeeper_id` in test_groundcrew.TestGroundcrewId;
here we pin the consolidation invariants the refactor introduced.
"""
from pathlib import Path

from support import IsolatedHomeTestCase, run_cli

from plan_keeper import ids
from plan_keeper.frontmatter import parse_frontmatter


class TestIdForPath(IsolatedHomeTestCase):
    """`id_for_path` is the single seed-derivation chokepoint."""

    def test_equals_primitive_composition(self):
        # The chokepoint must be exactly plankeeper_id(repo_for_plan(p), p.stem),
        # so routing every site through it is provably behavior-preserving.
        p = self.plans_root / "myrepo" / "2026-06-08-foo.md"
        self.assertEqual(
            ids.id_for_path(p),
            ids.plankeeper_id(ids.repo_for_plan(p), p.stem),
        )

    def test_stable_across_archive_move(self):
        # A plan keeps its id when moved into done/ or deferred/: repo_for_plan
        # resolves the repo from the grandparent, so the seed is unchanged.
        active = self.plans_root / "myrepo" / "2026-06-08-foo.md"
        archived = self.plans_root / "myrepo" / "done" / "2026-06-08-foo.md"
        deferred = self.plans_root / "myrepo" / "deferred" / "2026-06-08-foo.md"
        self.assertEqual(ids.id_for_path(active), ids.id_for_path(archived))
        self.assertEqual(ids.id_for_path(active), ids.id_for_path(deferred))

    def test_differs_by_repo(self):
        a = self.plans_root / "r1" / "2026-06-08-x.md"
        b = self.plans_root / "r2" / "2026-06-08-x.md"
        self.assertNotEqual(ids.id_for_path(a), ids.id_for_path(b))


class TestEnsureId(IsolatedHomeTestCase):
    """`ensure_id` mints once into an in-memory meta dict; caller persists."""

    def test_mints_when_absent(self):
        p = self.plans_root / "r" / "2026-06-08-x.md"
        meta = {"Plan-keeper Ticket": ""}
        minted = ids.ensure_id(meta, p)
        self.assertEqual(minted, ids.id_for_path(p))
        self.assertEqual(meta["Plan-keeper Ticket"], minted)

    def test_mints_when_field_missing_entirely(self):
        p = self.plans_root / "r" / "2026-06-08-x.md"
        meta: dict = {}
        minted = ids.ensure_id(meta, p)
        self.assertEqual(meta["Plan-keeper Ticket"], minted)

    def test_preserves_existing(self):
        # A frozen id is authoritative — never recomputed or overwritten, even
        # if it differs from what this path would mint to (e.g. a renamed plan).
        p = self.plans_root / "r" / "2026-06-08-x.md"
        meta = {"Plan-keeper Ticket": "plan-99999"}
        returned = ids.ensure_id(meta, p)
        self.assertEqual(returned, "plan-99999")
        self.assertEqual(meta["Plan-keeper Ticket"], "plan-99999")

    def test_remints_over_tbd_placeholder(self):
        # The legacy `Ticket: TBD` pattern survives `_migrate_legacy_ticket_fields`
        # as `Plan-keeper Ticket: TBD`. Two such plans would otherwise collide at
        # `_assert_no_plankeeper_id_collisions` (both ids = "TBD"); the validator
        # treats TBD as not-an-id so ensure_id mints a real one in its place.
        p = self.plans_root / "r" / "2026-06-08-x.md"
        meta = {"Plan-keeper Ticket": "TBD"}
        returned = ids.ensure_id(meta, p)
        self.assertEqual(returned, ids.id_for_path(p))
        self.assertEqual(meta["Plan-keeper Ticket"], returned)

    def test_remints_over_malformed_value(self):
        # Anything that doesn't match `plan-<digits>` — typos, a Linear id pasted
        # into the wrong field, an in-progress sentinel — is treated as absent.
        p = self.plans_root / "r" / "2026-06-08-x.md"
        for bogus in ("ENG-123", "plan-abc", "PLAN-99", "tbd", "?", "  "):
            meta = {"Plan-keeper Ticket": bogus}
            returned = ids.ensure_id(meta, p)
            self.assertEqual(returned, ids.id_for_path(p),
                             f"failed to re-mint over {bogus!r}")
            self.assertEqual(meta["Plan-keeper Ticket"], returned)


class TestMintIntoPathIfAbsent(IsolatedHomeTestCase):
    """`mint_into_path_if_absent` is the file-reading wrapper around ensure_id."""

    def _write(self, rel: str, text: str) -> Path:
        p = self.plans_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_mints_and_persists(self):
        p = self._write("r/2026-06-08-x.md", "---\nStatus: todo\n---\n# X\n")
        minted = ids.mint_into_path_if_absent(p)
        self.assertEqual(minted, ids.id_for_path(p))
        meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(meta["Plan-keeper Ticket"], minted)

    def test_preserves_existing_without_rewriting(self):
        p = self._write(
            "r/2026-06-08-x.md",
            "---\nPlan-keeper Ticket: plan-77\nStatus: todo\n---\n# X\n",
        )
        before = p.read_text(encoding="utf-8")
        returned = ids.mint_into_path_if_absent(p)
        self.assertEqual(returned, "plan-77")
        self.assertEqual(p.read_text(encoding="utf-8"), before)  # untouched

    def test_remints_and_persists_over_tbd(self):
        # The on-disk twin of TestEnsureId.test_remints_over_tbd_placeholder:
        # mint_into_path_if_absent must also rewrite the file when the stored
        # value is the TBD placeholder, not skip the write because the field
        # is non-empty. Without this, two TBD plans break crew fetch with an
        # id-collision error and the only recovery is hand-editing.
        p = self._write(
            "r/2026-06-08-x.md",
            "---\nPlan-keeper Ticket: TBD\nStatus: todo\n---\n# X\n",
        )
        minted = ids.mint_into_path_if_absent(p)
        self.assertEqual(minted, ids.id_for_path(p))
        meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(meta["Plan-keeper Ticket"], minted)

    def test_remints_and_persists_over_legacy_ticket_tbd(self):
        # End-to-end of the user's bug: a plan written with the legacy
        # `Ticket: TBD` + `Ticket System: groundcrew` pair must end up with a
        # real id on disk after a single mint pass. parse_frontmatter migrates
        # the legacy fields to `Plan-keeper Ticket: TBD`, and the validator
        # then re-mints; the write persists both the new id and the migration.
        p = self._write(
            "r/2026-06-08-x.md",
            "---\nTicket: TBD\nTicket System: groundcrew\nStatus: todo\n---\n# X\n",
        )
        minted = ids.mint_into_path_if_absent(p)
        self.assertEqual(minted, ids.id_for_path(p))
        text = p.read_text(encoding="utf-8")
        self.assertIn(f"Plan-keeper Ticket: {minted}", text)
        self.assertNotIn("Ticket: TBD", text)
        self.assertNotIn("Ticket System:", text)

    def test_skips_non_frontmatter_file(self):
        # A stray .md with no frontmatter is not a plan — never grow one onto it.
        p = self._write("r/README.md", "# just a readme\n")
        self.assertIsNone(ids.mint_into_path_if_absent(p))
        self.assertEqual(p.read_text(encoding="utf-8"), "# just a readme\n")

    def test_returns_none_on_non_utf8_file(self):
        # Best-effort: a non-UTF8 .md must be skipped (return None), not crash
        # the whole fetch. read_text raises UnicodeDecodeError (a ValueError,
        # not an OSError), so it needs its own guard.
        p = self.plans_root / "r" / "2026-06-08-bad.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"---\nStatus: todo\n---\n# bad\xff\n")
        self.assertIsNone(ids.mint_into_path_if_absent(p))


class TestMintSitesAgreeWithChokepoint(IsolatedHomeTestCase):
    """End-to-end: the id a mint site stores equals id_for_path(plan), proving
    every site routes through the one chokepoint rather than its own seed math."""

    def test_save_stores_id_for_path(self):
        r = run_cli(
            "save", "--override", "myrepo", "--topic", "centralized ids",
            stdin="# Centralized ids\n\nbody\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        saved = Path(r.stdout.strip())
        meta, _ = parse_frontmatter(saved.read_text(encoding="utf-8"))
        # The CLI ran in a subprocess against the real default PLAN_ROOT under
        # the isolated $HOME, so resolve the id against that same path.
        self.assertEqual(meta["Plan-keeper Ticket"], ids.id_for_path(saved))
