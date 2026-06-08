# plan-keeper

plan-keeper is a local **task-management system** built around plans. A task _is_ a plan file in `~/plans/<repo>/`, and it takes one of two shapes: a **planning task**, whose output is more plans (an idea you brainstorm into a spec; a PRD you turn into an executable plan), or an **implementation task** — an executable plan that gets built into code. The same tool captures both, routes each to its next step, and archives it when done. Everything is tracked locally in markdown on your machine, never committed to any repo; filing a plan out to Linear or Jira is an occasional export, not the system of record.

Eight skills cover the lifecycle: list a repo's plans read-only (`plan-list`), capture from conversation (`plan-save`), pick up and route to the next step (`plan-do`), archive with a completion stamp (`plan-done`), edit frontmatter (`plan-update`), manage the groundcrew dispatch queue (`plan-crew`), and file plans as Linear or Jira tickets (`plan-linear`, `plan-jira`). All share a bundled CLI and a `~/plans/<repo>/` tree that's local to your machine — nothing is committed to any repo.

## The model

The unit of work is a **task**, and every task is a plan file. Tasks come in two shapes:

- **Planning tasks produce more plans.** An `idea` brainstormed into a `spec`, a `prd` turned into an `exec-plan` — the deliverable of the task is the next, more-developed plan. `plan-do` routes these into the planning skills.
- **Implementation tasks produce code.** An `exec-plan` dispatched to `autonomous`, `task-list-runner`, or `executing-plans` — the deliverable is a PR. `plan-do` routes these into the execution menu.

Two orthogonal frontmatter fields track where a task sits:

- **`Kind`** (`idea → prd → design → spec → exec-plan`) — the document type: how far the _thinking_ has progressed. A planning task advances a plan along this axis, each step's output being a higher `Kind`. See [`plan-kinds.md`](plan-kinds.md).
- **`Status`** (`backlog → todo → in-progress → in-review → done`) — the lifecycle: how far the _work_ has progressed.

plan-keeper is the system of record for these tasks — they live in `~/plans/<repo>/` on your machine and are never committed to any repo. Filing a plan to **Linear or Jira** (`plan-linear` / `plan-jira`) is an optional export for the occasions a task needs a shared tracker; by default the task is tracked here.

## Install

Two install paths — pick by what you need:

**Plugin** — the eight skills plus the bundled CLI script, loaded into Claude Code:

```text
/plugin install plan-keeper@wild-horses
```

**Homebrew CLI** — the version-stable standalone `plan-keeper` binary on your `$PATH`:

```bash
brew install paulbaranowski/tap/plan-keeper
```

The binary is the same tool the plugin's bundled CLI script provides, just delivered as a version-locked executable from the `paulbaranowski/tap` tap (see [The bundled CLI](#the-bundled-cli)). It exists because `plan-keeper crew install` wires it into your groundcrew config, and groundcrew then invokes it — outside Claude Code, where the in-plugin script isn't reachable — to dispatch plans straight from `~/plans/<repo>/*.md`. Details in [the groundcrew connection](groundcrew/README.md).

## Skills

| Skill                                    | Role     | What it does                                                                                                                                                                                                                                                                              |
| ---------------------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **[`plan-list`](skills/plan-list/)**     | Lists    | Read-only inventory of a repo's plans, grouped by `Status` (in-progress / in-review / todo / backlog), newest-first. Shows what's there and stops — no body read, no mutation. `--state done`/`deferred` for the archives.                                                                |
| **[`plan-save`](skills/plan-save/)**     | Captures | Writes the latest plan from the current conversation to `~/plans/<repo>/<YYYY-MM-DD>-<topic>.md`.                                                                                                                                                                                         |
| **[`plan-do`](skills/plan-do/)**         | Routes   | Lists not-yet-started plans for the current repo, classifies readiness (idea / spec / execution-ready), and routes to the matching next skill. Execution-ready plans get all three execution engines (autonomous / task-list-builder / executing-plans), recommended-first by plan shape. |
| **[`plan-done`](skills/plan-done/)**     | Archives | Moves a completed plan to `~/plans/<repo>/done/` and appends a `*Completed: YYYY-MM-DD*` stamp.                                                                                                                                                                                           |
| **[`plan-update`](skills/plan-update/)** | Edits    | Mutates frontmatter fields (`Agent`, `Status`, `Ticket`) for a single plan in the current repo.                                                                                                                                                                                           |
| **[`plan-crew`](skills/plan-crew/)**     | Queues   | Shows the groundcrew dispatch queue — the current repo by default, or every repo with `--all` ("all repos") — and bulk-promotes/dequeues plans (`Status todo/backlog`). Multi-select; the bulk counterpart to plan-update.                                                                |
| **[`plan-linear`](skills/plan-linear/)** | Files    | Files the plan as a Linear ticket and stamps `Ticket:` in frontmatter.                                                                                                                                                                                                                    |
| **[`plan-jira`](skills/plan-jira/)**     | Files    | Files the plan as a Jira ticket and stamps `Ticket:` in frontmatter.                                                                                                                                                                                                                      |

All skills are model-invoked by description — no slash command is required. Trigger phrases like "save this plan", "do a plan from `<name>`", or "I'm done with the plan" route Claude into the right skill.

## How the pieces fit

```text
conversation ──► plan-save ──► ~/plans/<repo>/*.md ──► plan-do ──► (next skill)
                          idea            ─► superpowers:brainstorming
                          spec            ─► superpowers:writing-plans
                          execution-ready ─► menu (recommended first):
                                ├─► autonomous:autonomous                           (AFK ──► PR)
                                ├─► harness:task-list-builder ──► task-list-runner  (dispatched tasks)
                                └─► superpowers:executing-plans                     (sequential, review-gated)

                                                       plan-done ──► ~/plans/<repo>/done/<file>.md
```

`plan-do` is the entry point that joins the [superpowers](https://github.com/obra/superpowers) brainstorming → writing-plans → executing-plans pipeline (plus the [autonomous](../autonomous/skills/autonomous/) and [task-list-builder](../harness/skills/task-list-builder/) engines) at the right stage. It classifies in two tiers: **readiness** (idea / spec / execution-ready) picks the path; for execution-ready plans, **shape** (single-ticket vs. independent task list vs. sequential phases) picks which execution engine is recommended first — though all three are always offered.

## Repo derivation

`<repo>` auto-derives from `git remote get-url origin` (or `basename $PWD` as a fallback). Each skill also accepts an explicit override phrased naturally — "save the plan to `herds`", "do a plan from `general`", "archive the plan in `scratch`". Full algorithm: [`repo-derivation.md`](repo-derivation.md).

The override and auto-derive paths normalize differently: auto-derived names are kept verbatim (so `herds_mobile_app` stays `herds_mobile_app`), but user-typed overrides are lowercased with whitespace-to-hyphen ("General Folder" → `general-folder`). The asymmetry is deliberate — a git remote name is already canonical, but a user-typed phrase usually isn't.

## The bundled CLI

`scripts/plan_keeper_cli.py` is the canonical interface for all the skills — the skills never write to `~/plans/` directly. The same source file ships two ways: the plugin invokes `plan_keeper_cli.py` in place, while `brew install paulbaranowski/tap/plan-keeper` packages that exact source into the version-stable standalone `plan-keeper` binary (one source, two delivery vehicles — no second copy to drift). The skills call the in-tree script; groundcrew, which runs outside Claude Code and so can't reach it, calls the brew binary instead. Key subcommands include `repo` (`name`/`list`), `list`, `save`, `file-meta` (get/set/strip), `crew`, the per-provider `linear`/`jira` subtrees (`api`/`push`/`config`), and `upgrade` (self-update the Homebrew binary in place — `brew update && brew upgrade plan-keeper`, then re-run `crew install`; refuses when plan-keeper isn't a brew install) (run `--help` for the full set). Completing a plan is `file-meta set --status done`, which relocates it into `done/` and stamps `Completed on`. Mutations are atomic (tmp file + `fsync` + `os.replace`), and collisions surface as a structured exit-2 signal that the skills present to the user rather than treating as a fatal error.

A PreToolUse hook (`hooks/hooks.json`) auto-approves `python3 .../plan_keeper_cli.py` Bash invocations so each skill's flow runs without per-call permission prompts. The allow script anchors on the plugin-specific path so a stray `plan_keeper_cli.py` elsewhere in the workspace won't be auto-approved.

## Guardrails

- **Local-only.** `~/plans/` lives on your machine. Nothing is staged, committed, or pushed to any repo.
- **No silent overwrites.** Collisions on save or archive surface as a structured exit-2 error; the skill asks whether to overwrite, suffix `-2`, or pick a new name.
- **`plan-list` is read-only**, and `plan-do` only ever flips a started plan's `Status` to `in-progress`. The tree is mutated by `plan-save` (creates), `plan-done` (moves), and the frontmatter editors (`plan-update`, `plan-crew`).
- **Confirmation before mutating.** `plan-done` asks before invoking the CLI only when it had to _infer_ which plan you meant; when you name the plan (filename or ticket id) or pick it from the listing, it archives directly.
- **Empty-repo isolation.** When the current repo has no active plans, `plan-do` and `plan-done` say so and stop — they do not silently fall back to a different repo's folder.

## Files in this plugin

| Path                               | Purpose                                                                                                                       |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `skills/plan-list/SKILL.md`        | Instructions for the read-only listing flow                                                                                   |
| `skills/plan-save/SKILL.md`        | Instructions for the save flow                                                                                                |
| `skills/plan-do/SKILL.md`          | Instructions for the list-and-route flow                                                                                      |
| `skills/plan-done/SKILL.md`        | Instructions for the archive flow                                                                                             |
| `scripts/plan_keeper_cli.py`       | Bundled CLI entry shim — the only sanctioned mutator for `~/plans/`                                                           |
| `scripts/plan_keeper/`             | CLI implementation package (errors, naming, storage, frontmatter, config, http, linear, jira, push, groundcrew, upgrade, cli) |
| `scripts/tests/`                   | Stdlib `unittest` suite — one `test_<module>.py` per package module, shared harness in `support.py`                           |
| `scripts/plan-keeper-cli-allow.sh` | PreToolUse hook script — auto-approves CLI Bash invocations                                                                   |
| `hooks/hooks.json`                 | PreToolUse hook registration                                                                                                  |
| `repo-derivation.md`               | Shared algorithm — auto-derive + override normalization rules                                                                 |

Run the CLI tests from the repo root (stdlib only — no pytest/uv needed):

```text
python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
```
