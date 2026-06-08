#!/usr/bin/env python3
"""groundcrew shell-adapter glue + the queue subcommand (groundcrew.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from support import (
    IsolatedHomeTestCase,
    _import_cli_module,
    run_cli,
)


class TestGroundcrewFetch(IsolatedHomeTestCase):
    """Tests for the groundcrew-fetch subcommand."""

    def test_groundcrew_fetch_emits_array_with_translated_status(self):
        """Each active plan becomes one JSON issue with correct status mapping."""
        with tempfile.TemporaryDirectory() as home:
            plans = Path(home) / "plans"
            for repo, name, status in [
                ("groundcrew", "2026-01-01-a.md", "todo"),
                ("groundcrew", "2026-01-02-b.md", "backlog"),
                ("herds", "2026-01-03-c.md", "in-progress"),
            ]:
                d = plans / repo
                d.mkdir(parents=True, exist_ok=True)
                (d / name).write_text(
                    f"---\nAgent: claude\nStatus: {status}\n---\n# {name}\nDesc.\n"
                )
            # done/ subdir — must NOT appear in fetch output
            (plans / "groundcrew" / "done").mkdir()
            (plans / "groundcrew" / "done" / "2025-12-31-old.md").write_text(
                "---\nAgent: claude\nStatus: done\n---\n# Done\n"
            )

            result = run_cli("crew", "fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            issues = json.loads(result.stdout)
            self.assertEqual(len(issues), 3)  # done/ excluded

            # Ids are synthesized (plan-<digits>), not the filename stem, so
            # key the lookup on each plan's stem recovered from sourceRef.path.
            by_stem = {Path(i["sourceRef"]["path"]).stem: i for i in issues}
            self.assertEqual(by_stem["2026-01-01-a"]["status"], "todo")
            self.assertEqual(by_stem["2026-01-02-b"]["status"], "other")  # backlog → other
            self.assertEqual(by_stem["2026-01-03-c"]["status"], "in-progress")
            self.assertEqual(by_stem["2026-01-01-a"]["repository"], "groundcrew")
            self.assertEqual(by_stem["2026-01-03-c"]["repository"], "herds")
            self.assertEqual(by_stem["2026-01-01-a"]["model"], "claude")
            for issue in issues:
                self.assertRegex(issue["id"], r"^plan-\d+$")

    def test_groundcrew_fetch_uses_h1_as_title(self):
        """Title comes from the first H1 in the body, not the filename."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "groundcrew"
            d.mkdir(parents=True)
            (d / "2026-01-01-x.md").write_text(
                "---\nAgent: claude\nStatus: todo\n---\n# The Real Title\nbody\n"
            )
            result = run_cli("crew", "fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0)
            issues = json.loads(result.stdout)
            self.assertEqual(issues[0]["title"], "The Real Title")

    def test_groundcrew_fetch_sets_source_ref(self):
        """sourceRef.path is the absolute path to the plan file."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            plan = d / "2026-01-01-x.md"
            plan.write_text("---\nAgent: claude\nStatus: todo\n---\n# T\n")
            result = run_cli("crew", "fetch", home=Path(home), cwd=self.cwd)
            issues = json.loads(result.stdout)
            self.assertEqual(issues[0]["sourceRef"]["path"], str(plan.resolve()))

    def test_groundcrew_fetch_mints_id_into_frontmatter(self):
        """fetch mints the plan-keeper id into the Plan-keeper Ticket field
        (mint-once) so a human can see the mapping; no Ticket System line."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "herds"
            d.mkdir(parents=True)
            plan = d / "2026-04-30-typed-models.md"
            plan.write_text("---\nAgent: claude\nStatus: todo\n---\n# Typed\n")
            issues = json.loads(
                run_cli("crew", "fetch", home=Path(home), cwd=self.cwd).stdout
            )
            text = plan.read_text()
            self.assertIn(f"Plan-keeper Ticket: {issues[0]['id']}", text)
            self.assertNotIn("Ticket System", text)

    def test_groundcrew_fetch_stamp_is_idempotent(self):
        """Once stamped, repeated fetches don't rewrite the file."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            plan = d / "2026-01-01-x.md"
            plan.write_text("---\nAgent: claude\nStatus: todo\n---\n# T\n")
            run_cli("crew", "fetch", home=Path(home), cwd=self.cwd)
            after_first = plan.read_text()
            run_cli("crew", "fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(plan.read_text(), after_first)

    def test_groundcrew_fetch_stamp_preserves_foreign_fields(self):
        """Stamping the id keeps foreign frontmatter (Obsidian tags etc.)."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            plan = d / "2026-01-01-x.md"
            plan.write_text(
                "---\ntags: [infra]\nAgent: claude\nStatus: todo\n---\n# T\n"
            )
            issues = json.loads(
                run_cli("crew", "fetch", home=Path(home), cwd=self.cwd).stdout
            )
            text = plan.read_text()
            self.assertIn("tags: [infra]", text)
            self.assertIn(f"Plan-keeper Ticket: {issues[0]['id']}", text)

    def test_groundcrew_fetch_preserves_existing_id_no_heal(self):
        """A plan that already carries an id keeps it verbatim — mint-once, never
        recomputed or overwritten (the frozen-id contract). A legacy groundcrew
        Ticket pair is read (migrated in-memory) as that same id, so fetch never
        re-mints it, and `crew get` resolves the original id."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            plan = d / "2026-01-01-x.md"
            plan.write_text(
                "---\nTicket: plan-999999\nTicket System: groundcrew\n"
                "Agent: claude\nStatus: todo\n---\n# T\n"
            )
            issues = json.loads(
                run_cli("crew", "fetch", home=Path(home), cwd=self.cwd).stdout
            )
            # Frozen: the stored id is reported as-is, not re-hashed to a new one.
            self.assertEqual(issues[0]["id"], "plan-999999")
            self.assertIn("plan-999999", plan.read_text())
            # And it resolves by that id.
            r = run_cli("crew", "get", "plan-999999", home=Path(home), cwd=self.cwd)
            self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_groundcrew_fetch_persists_migration_on_next_write(self):
        """Once a real mutation touches a legacy plan (e.g. crew start), the new
        Plan-keeper Ticket field is persisted and the legacy pair is dropped."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            plan = d / "2026-01-01-x.md"
            plan.write_text(
                "---\nTicket: plan-999999\nTicket System: groundcrew\n"
                "Agent: claude\nStatus: todo\n---\n# T\n"
            )
            run_cli("crew", "start", "plan-999999", home=Path(home), cwd=self.cwd)
            text = plan.read_text()
            self.assertIn("Plan-keeper Ticket: plan-999999", text)
            self.assertNotIn("Ticket System", text)

    def test_groundcrew_fetch_does_not_clobber_external_ticket(self):
        """A plan already filed in Linear keeps its tracker reference; fetch
        mints a separate plan-keeper id without touching the Linear field."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            plan = d / "2026-01-01-x.md"
            plan.write_text(
                "---\nLinear Ticket: ENG-1\n"
                "Agent: claude\nStatus: todo\n---\n# T\n"
            )
            issues = json.loads(
                run_cli("crew", "fetch", home=Path(home), cwd=self.cwd).stdout
            )
            text = plan.read_text()
            self.assertIn("Linear Ticket: ENG-1", text)
            self.assertRegex(issues[0]["id"], r"^plan-\d+$")
            self.assertIn(f"Plan-keeper Ticket: {issues[0]['id']}", text)
            # The minted id resolves the plan.
            r = run_cli("crew", "get", issues[0]["id"],
                        home=Path(home), cwd=self.cwd)
            self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_groundcrew_fetch_skips_files_without_frontmatter(self):
        """A bare .md (no frontmatter) is skipped, not crashed on."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            (d / "good.md").write_text("---\nAgent: claude\nStatus: todo\n---\n# G\n")
            (d / "bare.md").write_text("# Just a body\n")
            result = run_cli("crew", "fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0)
            issues = json.loads(result.stdout)
            stems = {Path(i["sourceRef"]["path"]).stem for i in issues}
            self.assertIn("good", stems)
            self.assertNotIn("bare", stems)

    def test_groundcrew_fetch_includes_plan_with_foreign_frontmatter(self):
        """Regression: a plan with extra frontmatter (e.g. Obsidian tags) must
        not silently vanish from the queue — it parses and is dispatchable."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "herds"
            d.mkdir(parents=True)
            (d / "2026-01-01-tagged.md").write_text(
                "---\ntags: [infra]\nAgent: claude\nStatus: todo\n---\n# Tagged\n"
            )
            result = run_cli("crew", "fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            issues = json.loads(result.stdout)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["status"], "todo")
            self.assertRegex(issues[0]["id"], r"^plan-\d+$")

    def test_groundcrew_fetch_empty_when_no_plans(self):
        """`[]` (not error) when ~/plans/ is empty or missing."""
        with tempfile.TemporaryDirectory() as home:
            result = run_cli("crew", "fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout), [])

    def test_groundcrew_fetch_hides_locally_driven_in_progress_plan(self):
        """A plan a human is driving outside groundcrew — in-progress with no
        Agent (groundcrew claims the Agent at queue time, so an empty Agent on
        an in-progress plan means it was picked up locally, e.g. via plan-do) —
        must not appear in fetch. Otherwise groundcrew counts it against its
        in-progress slot cap and lists it under "In progress (no local
        worktree)", even though no crew worktree will ever exist for it.

        A queued in-progress plan keeps its Agent and stays visible — only the
        agent-less one is hidden."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            (d / "2026-01-01-local.md").write_text(
                "---\nStatus: in-progress\n---\n# Local\n"
            )
            (d / "2026-01-02-queued.md").write_text(
                "---\nAgent: claude\nStatus: in-progress\n---\n# Queued\n"
            )
            result = run_cli("crew", "fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            stems = {
                Path(i["sourceRef"]["path"]).stem
                for i in json.loads(result.stdout)
            }
            self.assertNotIn("2026-01-01-local", stems)
            self.assertIn("2026-01-02-queued", stems)

    def test_groundcrew_fetch_hides_locally_driven_in_review_plan(self):
        """A locally-driven plan that has advanced to in-review (still no Agent)
        stays hidden too — once a human owns the plan outside the crew, every
        active state it passes through is theirs, not groundcrew's."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            (d / "2026-01-01-local.md").write_text(
                "---\nStatus: in-review\n---\n# Local\n"
            )
            result = run_cli("crew", "fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            stems = {
                Path(i["sourceRef"]["path"]).stem
                for i in json.loads(result.stdout)
            }
            self.assertNotIn("2026-01-01-local", stems)

    def test_groundcrew_fetch_keeps_agent_less_plan_when_not_in_progress(self):
        """The hide rule is scoped to in-progress: a todo/backlog plan with no
        Agent is still dispatchable and must stay visible (the model defaults
        to claude downstream)."""
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / "plans" / "r"
            d.mkdir(parents=True)
            (d / "2026-01-01-todo.md").write_text(
                "---\nStatus: todo\n---\n# Todo\n"
            )
            result = run_cli("crew", "fetch", home=Path(home), cwd=self.cwd)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            stems = {
                Path(i["sourceRef"]["path"]).stem
                for i in json.loads(result.stdout)
            }
            self.assertIn("2026-01-01-todo", stems)

class TestGroundcrewId(IsolatedHomeTestCase):
    """Synthesized groundcrew ticket id (stateless deterministic hash)."""

    def setUp(self) -> None:
        super().setUp()
        self.cli = _import_cli_module()

    def test_id_matches_groundcrew_ticket_shape(self):
        # groundcrew enforces TICKET_RE = /^[a-z][\\da-z]*-\\d+$/.
        self.assertRegex(
            self.cli.plankeeper_id("herds", "2026-04-30-foo"),
            r"^[a-z][\da-z]*-\d+$",
        )

    def test_id_is_stable_across_calls(self):
        self.assertEqual(
            self.cli.plankeeper_id("herds", "2026-04-30-foo"),
            self.cli.plankeeper_id("herds", "2026-04-30-foo"),
        )

    def test_id_differs_by_repo(self):
        # Same stem in two repos must not collide: groundcrew uses the bare
        # id as a git branch and run-state filename, with no repo qualifier.
        self.assertNotEqual(
            self.cli.plankeeper_id("r1", "2026-01-01-x"),
            self.cli.plankeeper_id("r2", "2026-01-01-x"),
        )

    def test_id_differs_by_stem(self):
        self.assertNotEqual(
            self.cli.plankeeper_id("r", "2026-01-01-x"),
            self.cli.plankeeper_id("r", "2026-01-02-y"),
        )

    def test_collision_guard_raises_with_both_paths(self):
        issues = [
            {"id": "plan-1", "sourceRef": {"path": "/a.md"}},
            {"id": "plan-1", "sourceRef": {"path": "/b.md"}},
        ]
        with self.assertRaises(self.cli.PlanKeeperCliError) as ctx:
            self.cli._assert_no_plankeeper_id_collisions(issues)
        self.assertIn("/a.md", str(ctx.exception))
        self.assertIn("/b.md", str(ctx.exception))

    def test_collision_guard_passes_distinct_ids(self):
        issues = [
            {"id": "plan-1", "sourceRef": {"path": "/a.md"}},
            {"id": "plan-2", "sourceRef": {"path": "/b.md"}},
        ]
        self.cli._assert_no_plankeeper_id_collisions(issues)  # no raise

    def test_collision_guard_skips_empty_ids(self):
        # Unminted plans (empty id, before fetch mints) must not collide.
        issues = [
            {"id": "", "sourceRef": {"path": "/a.md"}},
            {"id": "", "sourceRef": {"path": "/b.md"}},
        ]
        self.cli._assert_no_plankeeper_id_collisions(issues)  # no raise

class TestGroundcrewResolveOne(IsolatedHomeTestCase):
    """Tests for the groundcrew-resolve-one subcommand."""

    def setUp(self) -> None:
        super().setUp()
        # A real caller passes resolve-one an id it got from a prior fetch.
        # Tests reproduce that by computing the same id via the module fn.
        self.cli = _import_cli_module()

    def test_groundcrew_resolve_one_finds_active_plan(self):
        d = self.home / "plans" / "r"
        d.mkdir(parents=True)
        plan = d / "2026-01-01-x.md"
        ticket = self.cli.plankeeper_id("r", "2026-01-01-x")
        plan.write_text(
            f"---\nPlan-keeper Ticket: {ticket}\nAgent: claude\nStatus: todo\n---\n# Title\n"
        )
        result = run_cli("crew", "get", ticket,
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        issue = json.loads(result.stdout)
        self.assertEqual(issue["id"], ticket)
        self.assertEqual(issue["status"], "todo")
        self.assertEqual(issue["title"], "Title")
        self.assertEqual(issue["sourceRef"]["path"], str(plan.resolve()))

    def test_groundcrew_resolve_one_finds_done_plan(self):
        d = self.home / "plans" / "r" / "done"
        d.mkdir(parents=True)
        # Archived plan's repo is the grandparent dir ("r"), so its id is
        # keyed on ("r", stem) — same as when it was active. It was minted while
        # active, so the frozen id is stored in frontmatter.
        ticket = self.cli.plankeeper_id("r", "2025-12-31-old")
        (d / "2025-12-31-old.md").write_text(
            f"---\nPlan-keeper Ticket: {ticket}\nAgent: claude\nStatus: done\n---\n# Old\n"
        )
        result = run_cli("crew", "get", ticket,
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0)
        issue = json.loads(result.stdout)
        self.assertEqual(issue["status"], "done")

    def test_groundcrew_resolve_one_still_finds_locally_driven_plan(self):
        """Boundary: a locally-driven plan (in-progress, no Agent) is hidden
        from `crew fetch` but stays resolvable by `crew get`/`crew start`. The
        hide rule lives at the fetch/collection layer, not in the resolver, so
        a human can still inspect the plan by id."""
        d = self.home / "plans" / "r"
        d.mkdir(parents=True)
        plan = d / "2026-01-01-local.md"
        ticket = self.cli.plankeeper_id("r", "2026-01-01-local")
        plan.write_text(
            f"---\nPlan-keeper Ticket: {ticket}\nStatus: in-progress\n---\n# Local\n"
        )
        result = run_cli("crew", "get", ticket, home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(json.loads(result.stdout)["id"], ticket)

    def test_groundcrew_resolve_one_missing_returns_exit_3(self):
        """Spec: 'prints nothing for "not found", or exits 3.' We pick exit 3."""
        (self.home / "plans" / "r").mkdir(parents=True)
        result = run_cli("crew", "get", "does-not-exist",
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")  # nothing on stdout

    def test_groundcrew_resolve_one_rejects_path_separator(self):
        """ID can't contain '/' — defends against ../../etc/passwd-style inputs."""
        result = run_cli("crew", "get", "../escape",
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid id", result.stderr)

    def test_groundcrew_resolve_one_done_plan_has_correct_repository(self):
        """Regression: archived plans must report their repo name, not 'done'."""
        d = self.home / "plans" / "myrepo" / "done"
        d.mkdir(parents=True)
        ticket = self.cli.plankeeper_id("myrepo", "2025-12-31-old")
        (d / "2025-12-31-old.md").write_text(
            f"---\nPlan-keeper Ticket: {ticket}\nAgent: claude\nStatus: done\n---\n# Old\n"
        )
        result = run_cli("crew", "get", ticket,
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        issue = json.loads(result.stdout)
        self.assertEqual(issue["repository"], "myrepo")

    def test_groundcrew_resolve_one_deferred_plan_has_correct_repository(self):
        """Regression: paused plans must report their repo name, not 'deferred'."""
        d = self.home / "plans" / "myrepo" / "deferred"
        d.mkdir(parents=True)
        ticket = self.cli.plankeeper_id("myrepo", "2025-06-15-paused")
        (d / "2025-06-15-paused.md").write_text(
            f"---\nPlan-keeper Ticket: {ticket}\nAgent: claude\nStatus: backlog\n---\n# Paused\n"
        )
        result = run_cli("crew", "get", ticket,
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        issue = json.loads(result.stdout)
        self.assertEqual(issue["repository"], "myrepo")

    def test_groundcrew_resolve_one_round_trips_fetched_id(self):
        """The id fetch emits resolves back to the exact same plan file."""
        d = self.home / "plans" / "herds"
        d.mkdir(parents=True)
        plan = d / "2026-04-30-notification-service-typed-models.md"
        plan.write_text("---\nAgent: claude\nStatus: todo\n---\n# Typed models\n")

        issues = json.loads(
            run_cli("crew", "fetch", home=self.home, cwd=self.cwd).stdout
        )
        ticket = issues[0]["id"]
        result = run_cli("crew", "get", ticket,
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        issue = json.loads(result.stdout)
        self.assertEqual(issue["id"], ticket)
        self.assertEqual(issue["sourceRef"]["path"], str(plan.resolve()))

class TestCrewStart(IsolatedHomeTestCase):
    """`crew start ${id}` resolves the synthesized id (reusing `crew get`'s
    resolver) and flips that plan's Status to in-progress.

    The interface is an `${id}` positional, not the old stdin `{path}` JSON:
    an id can only ever name a plan inside PLAN_ROOT (resolution globs only
    PLAN_ROOT), so the path-validation guard the JSON form needed is gone."""

    def setUp(self) -> None:
        super().setUp()
        # A real caller passes start an id it got from a prior fetch. Tests
        # reproduce that by computing the same id via the module fn.
        self.cli = _import_cli_module()

    def test_crew_start_flips_status_for_resolved_id(self):
        d = self.home / "plans" / "r"
        d.mkdir(parents=True)
        plan = d / "2026-01-01-x.md"
        ticket = self.cli.plankeeper_id("r", "2026-01-01-x")
        plan.write_text(
            f"---\nPlan-keeper Ticket: {ticket}\nAgent: claude\nStatus: todo\n---\n# Title\n"
        )
        result = run_cli("crew", "start", ticket, home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Status: in-progress", plan.read_text())
        self.assertNotIn("Status: todo", plan.read_text())
        # Echoes the resolved path (mirrors the old interface's stdout).
        self.assertEqual(result.stdout.strip(), str(plan.resolve()))

    def test_crew_start_round_trips_fetched_id(self):
        """The id fetch emits is the id start consumes — same plan, both ends."""
        d = self.home / "plans" / "herds"
        d.mkdir(parents=True)
        plan = d / "2026-04-30-typed-models.md"
        plan.write_text("---\nAgent: claude\nStatus: todo\n---\n# Typed\n")
        issues = json.loads(
            run_cli("crew", "fetch", home=self.home, cwd=self.cwd).stdout
        )
        ticket = issues[0]["id"]
        result = run_cli("crew", "start", ticket, home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Status: in-progress", plan.read_text())

    def test_crew_start_missing_id_exits_3(self):
        """An id no plan maps to → exit 3 (mirrors `crew get`)."""
        (self.home / "plans" / "r").mkdir(parents=True)
        result = run_cli("crew", "start", "plan-999999",
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")  # error goes to stderr

    def test_crew_start_rejects_path_separator(self):
        """An id can't contain '/' — defends against ../../-style inputs."""
        result = run_cli("crew", "start", "../escape",
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid id", result.stderr)

    def test_crew_start_active_wins_over_archived_same_stem(self):
        """Active and done/ share a stem → same synthesized id. start flips the
        active plan (resolver's first-match: active before done) and leaves the
        archived one untouched."""
        repo = self.home / "plans" / "r"
        done = repo / "done" / "2026-01-01-x.md"
        done.parent.mkdir(parents=True)
        active = repo / "2026-01-01-x.md"
        ticket = self.cli.plankeeper_id("r", "2026-01-01-x")
        # Active and archived share a stem, so they were minted to the same id.
        active.write_text(
            f"---\nPlan-keeper Ticket: {ticket}\nStatus: todo\n---\n# X\n"
        )
        done.write_text(
            f"---\nPlan-keeper Ticket: {ticket}\nStatus: done\n---\n# X\n"
        )
        result = run_cli("crew", "start", ticket, home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Status: in-progress", active.read_text())
        self.assertIn("Status: done", done.read_text())  # archived untouched

class TestCrewReview(IsolatedHomeTestCase):
    """`crew review ${id}` resolves the synthesized id (reusing `crew get`'s
    resolver) and flips that plan's Status to in-review — the markInReview leg
    of the groundcrew TicketSource adapter (auto-advance on PR open).

    Same id-based interface and resolver as `crew start`: an id can only ever
    name a plan inside PLAN_ROOT, so no path-traversal guard is needed."""

    def setUp(self) -> None:
        super().setUp()
        self.cli = _import_cli_module()

    def test_crew_review_flips_status_for_resolved_id(self):
        d = self.home / "plans" / "r"
        d.mkdir(parents=True)
        plan = d / "2026-01-01-x.md"
        ticket = self.cli.plankeeper_id("r", "2026-01-01-x")
        plan.write_text(
            f"---\nPlan-keeper Ticket: {ticket}\nAgent: claude\nStatus: in-progress\n---\n# Title\n"
        )
        result = run_cli("crew", "review", ticket, home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Status: in-review", plan.read_text())
        self.assertNotIn("Status: in-progress", plan.read_text())
        # Echoes the resolved path (mirrors `crew start`).
        self.assertEqual(result.stdout.strip(), str(plan.resolve()))

    def test_crew_review_round_trips_fetched_id(self):
        """The id fetch emits is the id review consumes — same plan, both ends."""
        d = self.home / "plans" / "herds"
        d.mkdir(parents=True)
        plan = d / "2026-04-30-typed-models.md"
        plan.write_text("---\nAgent: claude\nStatus: in-progress\n---\n# Typed\n")
        issues = json.loads(
            run_cli("crew", "fetch", home=self.home, cwd=self.cwd).stdout
        )
        ticket = issues[0]["id"]
        result = run_cli("crew", "review", ticket, home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Status: in-review", plan.read_text())

    def test_crew_review_missing_id_exits_3(self):
        """An id no plan maps to → exit 3 (mirrors `crew get`/`crew start`)."""
        (self.home / "plans" / "r").mkdir(parents=True)
        result = run_cli("crew", "review", "plan-999999",
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")  # error goes to stderr

    def test_crew_review_rejects_path_separator(self):
        """An id can't contain '/' — defends against ../../-style inputs."""
        result = run_cli("crew", "review", "../escape",
                         home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid id", result.stderr)

    def test_crew_review_resolves_archived_plan_like_start(self):
        """The shared resolver finds done/ plans too: `crew review` on a plan
        that fetch would skip still flips it (resolution globs done/ + active),
        matching `crew start`'s resolve-anything behavior."""
        repo = self.home / "plans" / "r"
        done = repo / "done" / "2026-01-01-x.md"
        done.parent.mkdir(parents=True)
        ticket = self.cli.plankeeper_id("r", "2026-01-01-x")
        done.write_text(
            f"---\nPlan-keeper Ticket: {ticket}\nStatus: done\n---\n# X\n"
        )
        result = run_cli("crew", "review", ticket, home=self.home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Status: in-review", done.read_text())

class TestQueue(IsolatedHomeTestCase):
    """Cross-repo `queue list` / `queue set` for the plan-crew skill."""

    def _make_plan(
        self,
        repo: str,
        name: str,
        status: str = "",
        agent: str = "",
        created: str = "",
    ) -> Path:
        """Create ~/<home>/plans/<repo>/<name> with optional Status/Agent/Created."""
        d = self.plans_root / repo
        d.mkdir(parents=True, exist_ok=True)
        fm = ["---"]
        if agent:
            fm.append(f"Agent: {agent}")
        if status:
            fm.append(f"Status: {status}")
        if created:
            fm.append(f"Created: {created}")
        fm.append("---")
        p = d / name
        p.write_text("\n".join(fm) + f"\n\n# {name}\n", encoding="utf-8")
        return p

    def test_queue_list_empty_when_no_plans(self) -> None:
        r = run_cli("crew", "queue", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [])

    def test_queue_list_reports_status_and_agent_across_repos(self) -> None:
        self._make_plan("alpha", "2026-05-01-a.md", status="todo", agent="codex")
        self._make_plan("beta", "2026-05-02-b.md", status="backlog")
        r = run_cli("crew", "queue", "list", "--all", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)
        by_file = {row["file"]: row for row in rows}
        self.assertEqual(
            by_file["2026-05-01-a.md"],
            {"repo": "alpha", "file": "2026-05-01-a.md", "status": "todo", "agent": "codex"},
        )
        self.assertEqual(
            by_file["2026-05-02-b.md"],
            {"repo": "beta", "file": "2026-05-02-b.md", "status": "backlog", "agent": ""},
        )

    def test_queue_list_groups_repos_and_orders_newest_first_within_each(self) -> None:
        # Repos stay grouped in their outer alphabetical order (alpha before
        # beta); within a repo, plans come back newest-first by the leading
        # YYYY-MM-DD — even when beta's plan is globally the newest.
        self._make_plan("alpha", "2026-05-01-oldest.md", status="backlog")
        self._make_plan("alpha", "2026-05-10-middle.md", status="backlog")
        self._make_plan("beta", "2026-05-20-newest.md", status="todo")
        r = run_cli("crew", "queue", "list", "--all", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)
        self.assertEqual(
            [(row["repo"], row["file"]) for row in rows],
            [
                ("alpha", "2026-05-10-middle.md"),
                ("alpha", "2026-05-01-oldest.md"),
                ("beta", "2026-05-20-newest.md"),
            ],
        )

    def test_queue_list_orders_same_day_by_created_stamp(self) -> None:
        # Same filename date → the Created stamp's time component breaks the
        # tie, still newest-first.
        self._make_plan(
            "alpha", "2026-05-01-morning.md", status="backlog",
            created="2026-05-01T09:00:00Z",
        )
        self._make_plan(
            "alpha", "2026-05-01-evening.md", status="backlog",
            created="2026-05-01T21:00:00Z",
        )
        r = run_cli(
            "crew", "queue", "list", "--repo", "alpha",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        files = [row["file"] for row in json.loads(r.stdout)]
        self.assertEqual(
            files, ["2026-05-01-evening.md", "2026-05-01-morning.md"]
        )

    def test_queue_list_skips_done_and_deferred_and_no_frontmatter(self) -> None:
        self._make_plan("alpha", "2026-05-01-active.md", status="backlog")
        # archived/paused subdirs must be ignored
        done = self.plans_root / "alpha" / "done"
        done.mkdir(parents=True, exist_ok=True)
        (done / "2026-04-01-old.md").write_text(
            "---\nStatus: done\n---\n\n# old\n", encoding="utf-8"
        )
        deferred = self.plans_root / "alpha" / "deferred"
        deferred.mkdir(parents=True, exist_ok=True)
        (deferred / "2026-04-02-paused.md").write_text(
            "---\nStatus: backlog\n---\n\n# paused\n", encoding="utf-8"
        )
        # a non-plan .md with no frontmatter must be skipped
        (self.plans_root / "alpha" / "README.md").write_text(
            "# not a plan\n", encoding="utf-8"
        )
        r = run_cli("crew", "queue", "list", "--all", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        files = sorted(row["file"] for row in json.loads(r.stdout))
        self.assertEqual(files, ["2026-05-01-active.md"])

    def test_queue_list_surfaces_in_progress_and_in_review(self) -> None:
        self._make_plan("alpha", "2026-05-01-a.md", status="in-progress", agent="claude")
        self._make_plan("alpha", "2026-05-02-b.md", status="in-review")
        r = run_cli("crew", "queue", "list", "--all", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        by_file = {row["file"]: row["status"] for row in json.loads(r.stdout)}
        self.assertEqual(by_file["2026-05-01-a.md"], "in-progress")
        self.assertEqual(by_file["2026-05-02-b.md"], "in-review")

    def test_queue_list_empty_status_plan(self) -> None:
        self._make_plan("alpha", "2026-05-01-a.md", agent="codex")  # no Status line
        r = run_cli("crew", "queue", "list", "--all", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "")
        self.assertEqual(rows[0]["agent"], "codex")

    def test_queue_list_defaults_to_current_repo(self) -> None:
        # cwd basename is "workdir" (see IsolatedHomeTestCase), so the bare
        # `list` scopes to the ~/plans/workdir/ folder and ignores other repos.
        self._make_plan("workdir", "2026-05-01-here.md", status="backlog")
        self._make_plan("elsewhere", "2026-05-02-there.md", status="backlog")
        r = run_cli("crew", "queue", "list", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)
        self.assertEqual([row["file"] for row in rows], ["2026-05-01-here.md"])
        self.assertEqual(rows[0]["repo"], "workdir")

    def test_queue_list_all_overrides_current_repo_default(self) -> None:
        self._make_plan("workdir", "2026-05-01-here.md", status="backlog")
        self._make_plan("elsewhere", "2026-05-02-there.md", status="backlog")
        r = run_cli("crew", "queue", "list", "--all", home=self.home, cwd=self.cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        repos = sorted(row["repo"] for row in json.loads(r.stdout))
        self.assertEqual(repos, ["elsewhere", "workdir"])

    def test_queue_list_repo_flag_scopes_to_named_repo(self) -> None:
        self._make_plan("workdir", "2026-05-01-here.md", status="backlog")
        self._make_plan("elsewhere", "2026-05-02-there.md", status="backlog")
        r = run_cli(
            "crew", "queue", "list", "--repo", "elsewhere",
            home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)
        self.assertEqual([row["repo"] for row in rows], ["elsewhere"])

    def test_queue_list_all_and_repo_are_mutually_exclusive(self) -> None:
        r = run_cli(
            "crew", "queue", "list", "--all", "--repo", "x",
            home=self.home, cwd=self.cwd,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not allowed with", r.stderr)

    def test_queue_set_promotes_backlog_to_todo(self) -> None:
        p = self._make_plan("alpha", "2026-05-01-a.md", status="backlog", agent="codex")
        r = run_cli(
            "crew", "queue", "set", "--status", "todo",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertIn("Status: todo", text)
        self.assertNotIn("Status: backlog", text)
        self.assertIn("Agent: codex", text)  # existing Agent untouched

    def test_queue_set_promote_mints_plankeeper_ticket(self) -> None:
        # Promoting a plan mints its plan-keeper id so it's visible the moment
        # it's queued (mint-once; no Ticket System line).
        p = self._make_plan("alpha", "2026-05-01-a.md", status="backlog")
        r = run_cli(
            "crew", "queue", "set", "--status", "todo",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertRegex(text, r"Plan-keeper Ticket: plan-\d+")
        self.assertNotIn("Ticket System", text)

    def test_queue_set_promote_does_not_clobber_external_ticket(self) -> None:
        # A plan already filed in Linear keeps its tracker reference on promote,
        # and additionally gets a minted plan-keeper id.
        d = self.plans_root / "alpha"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "2026-05-01-a.md"
        p.write_text(
            "---\nLinear Ticket: ENG-1\nStatus: backlog\n---\n\n# a\n",
            encoding="utf-8",
        )
        r = run_cli(
            "crew", "queue", "set", "--status", "todo",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertIn("Linear Ticket: ENG-1", text)
        self.assertRegex(text, r"Plan-keeper Ticket: plan-\d+")
        self.assertNotIn("Ticket System", text)

    def test_queue_set_dequeue_does_not_stamp_groundcrew(self) -> None:
        p = self._make_plan("alpha", "2026-05-01-a.md", status="todo")
        r = run_cli(
            "crew", "queue", "set", "--status", "backlog",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("groundcrew", p.read_text())

    def test_queue_set_promote_fills_missing_agent_with_default(self) -> None:
        p = self._make_plan("alpha", "2026-05-01-a.md", status="backlog")  # no Agent
        r = run_cli(
            "crew", "queue", "set", "--status", "todo", "--default-agent", "claude",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertIn("Status: todo", text)
        self.assertIn("Agent: claude", text)

    def test_queue_set_promote_keeps_existing_agent_over_default(self) -> None:
        p = self._make_plan("alpha", "2026-05-01-a.md", status="backlog", agent="codex")
        r = run_cli(
            "crew", "queue", "set", "--status", "todo", "--default-agent", "claude",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertIn("Agent: codex", text)
        self.assertNotIn("Agent: claude", text)

    def test_queue_set_dequeues_todo_to_backlog_without_touching_agent(self) -> None:
        p = self._make_plan("alpha", "2026-05-01-a.md", status="todo")  # no Agent
        r = run_cli(
            "crew", "queue", "set", "--status", "backlog", "--default-agent", "claude",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = p.read_text()
        self.assertIn("Status: backlog", text)
        self.assertNotIn("Agent:", text)  # dequeue never writes a default Agent

    def test_queue_set_promotes_multiple_plans_in_one_call(self) -> None:
        p1 = self._make_plan("alpha", "2026-05-01-a.md", status="backlog")
        p2 = self._make_plan("beta", "2026-05-02-b.md", status="backlog")
        r = run_cli(
            "crew", "queue", "set", "--status", "todo", "--default-agent", "claude",
            stdin=f"{p1}\n{p2}\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Status: todo", p1.read_text())
        self.assertIn("Status: todo", p2.read_text())

    def test_queue_set_rejects_path_outside_plan_root(self) -> None:
        outside = self.home / "evil.md"
        outside.write_text("---\nStatus: backlog\n---\n\n# evil\n", encoding="utf-8")
        r = run_cli(
            "crew", "queue", "set", "--status", "todo",
            stdin=str(outside) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("outside PLAN_ROOT", r.stderr)
        self.assertIn("Status: backlog", outside.read_text())  # untouched

    def test_queue_set_rejects_plan_without_frontmatter(self) -> None:
        d = self.plans_root / "alpha"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "2026-05-01-a.md"
        p.write_text("# no frontmatter\n", encoding="utf-8")
        r = run_cli(
            "crew", "queue", "set", "--status", "todo",
            stdin=str(p) + "\n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("no frontmatter", r.stderr)

    def test_queue_set_errors_on_empty_stdin(self) -> None:
        r = run_cli(
            "crew", "queue", "set", "--status", "todo",
            stdin="\n  \n", home=self.home, cwd=self.cwd,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("no plan paths", r.stderr)

    def test_queue_set_is_all_or_nothing_on_bad_path(self) -> None:
        good = self._make_plan("alpha", "2026-05-01-a.md", status="backlog")
        bad = self.plans_root / "alpha" / "missing.md"  # does not exist
        r = run_cli(
            "crew", "queue", "set", "--status", "todo",
            stdin=f"{good}\n{bad}\n", home=self.home, cwd=self.cwd,
        )
        self.assertNotEqual(r.returncode, 0)
        # good plan must be untouched because validation fails before any write
        self.assertIn("Status: backlog", good.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
