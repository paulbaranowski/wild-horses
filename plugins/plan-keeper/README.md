# plan-keeper

plan-keeper is a local **task-management system** built around plans. A task _is_ a plan file in `~/plans/<repo>/`, and it takes one of two shapes: a **planning task**, whose output is more plans (an idea you brainstorm into a spec; a PRD you turn into an executable plan), or an **implementation task** — an executable plan that gets built into code. The same tool captures both, routes each to its next step, and archives it when done. Everything is tracked locally in markdown on your machine, never committed to any repo; filing a plan out to Linear or Jira is an occasional export, not the system of record.

Nine skills cover the lifecycle: list a repo's plans read-only (`plan-list`), capture from conversation (`plan-save`), pick up and route to the next step (`plan-do`), split one plan into dependency-wired slices (`plan-split`), archive with a completion stamp (`plan-done`), edit frontmatter (`plan-update`), manage the groundcrew dispatch queue (`plan-crew`), and file plans as Linear or Jira tickets (`plan-linear`, `plan-jira`). All share a bundled CLI and a `~/plans/<repo>/` tree that's local to your machine — nothing is committed to any repo.

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

**Plugin** — the nine skills plus the bundled CLI script, loaded into Claude Code:

```text
/plugin install plan-keeper@wild-horses
```

**Homebrew CLI** — the version-stable standalone `plan-keeper` binary on your `$PATH`:

```bash
brew install paulbaranowski/tap/plan-keeper
```

The binary is the same tool the plugin's bundled CLI script provides, just delivered as a version-locked executable from the `paulbaranowski/tap` tap (see [Command-line usage](#command-line-usage)). It exists because `plan-keeper crew install` wires it into your groundcrew config, and groundcrew then invokes it — outside Claude Code, where the in-plugin script isn't reachable — to dispatch plans straight from `~/plans/<repo>/*.md`. Details in [Groundcrew integration](#groundcrew-integration).

## Skills

| Skill                                    | Role     | What it does                                                                                                                                                                                                                                                                              |
| ---------------------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **[`plan-list`](skills/plan-list/)**     | Lists    | Read-only inventory of a repo's plans, grouped by `Status` (in-progress / in-review / todo / backlog), newest-first. Shows what's there and stops — no body read, no mutation. `--state done`/`deferred` for the archives.                                                                |
| **[`plan-save`](skills/plan-save/)**     | Captures | Writes the latest plan from the current conversation to `~/plans/<repo>/<YYYY-MM-DD>-<topic>.md`.                                                                                                                                                                                         |
| **[`plan-do`](skills/plan-do/)**         | Routes   | Lists not-yet-started plans for the current repo, classifies readiness (idea / spec / execution-ready), and routes to the matching next skill. Execution-ready plans get all three execution engines (autonomous / task-list-builder / executing-plans), recommended-first by plan shape. |
| **[`plan-split`](skills/plan-split/)**   | Splits   | Decomposes one plan into N independently-grabbable vertical-slice plans (tracer bullets), wired with native `Blocked-by:` dependencies and promoted to `todo` so groundcrew dispatches the wave in dependency order. Marks the source plan `done` when it was a saved file.               |
| **[`plan-done`](skills/plan-done/)**     | Archives | Moves a completed plan to `~/plans/<repo>/done/` and appends a `*Completed: YYYY-MM-DD*` stamp.                                                                                                                                                                                           |
| **[`plan-update`](skills/plan-update/)** | Edits    | Mutates frontmatter fields (`Agent`, `Status`, `Ticket`) for a single plan in the current repo.                                                                                                                                                                                           |
| **[`plan-crew`](skills/plan-crew/)**     | Queues   | Shows the groundcrew dispatch queue — the current repo by default, or every repo with `--all` ("all repos") — and bulk-promotes/dequeues plans (`Status todo/backlog`). Multi-select; the bulk counterpart to plan-update.                                                                |
| **[`plan-linear`](skills/plan-linear/)** | Files    | Files the plan as a Linear ticket and stamps `Linear Ticket:` in frontmatter.                                                                                                                                                                                                             |
| **[`plan-jira`](skills/plan-jira/)**     | Files    | Files the plan as a Jira ticket and stamps `Jira Ticket:` in frontmatter.                                                                                                                                                                                                                 |

All skills are model-invoked by description — no slash command is required. Trigger phrases like "save this plan", "do a plan from `<name>`", or "I'm done with the plan" route Claude into the right skill.

## How the pieces fit

```text
conversation ──► plan-save ──► ~/plans/<repo>/*.md ──► plan-do ──► (next skill)
                          idea            ─► superpowers:brainstorming
                          spec            ─► superpowers:writing-plans
                          execution-ready ─► menu (recommended first):
                                ├─► autonomous:autonomous                           (AFK ──► PR)
                                ├─► refactor:task-list-builder ──► task-list-runner  (dispatched tasks)
                                └─► superpowers:executing-plans                     (sequential, review-gated)

                                                       plan-done ──► ~/plans/<repo>/done/<file>.md
```

`plan-do` is the entry point that joins the [superpowers](https://github.com/obra/superpowers) brainstorming → writing-plans → executing-plans pipeline (plus the [autonomous](../autonomous/skills/autonomous/) and [task-list-builder](../refactor/skills/task-list-builder/) engines) at the right stage. It classifies in two tiers: **readiness** (idea / spec / execution-ready) picks the path; for execution-ready plans, **shape** (single-ticket vs. independent task list vs. sequential phases) picks which execution engine is recommended first — though all three are always offered.

## Groundcrew integration

The groundcrew integration is the **autonomous** counterpart to `plan-do`: groundcrew treats `~/plans/<repo>/*.md` as a ticket source and dispatches plans on its own, with no Linear/Jira round-trip. Run [`/plan-crew`](#managing-the-queue-with-plan-crew) to promote a plan to `Status: todo` and set its `Agent`; groundcrew then picks it up, flips it to `in-progress`, runs that agent against it, and — when the PR opens — flips it to `in-review`.

### Wiring it up

groundcrew runs _outside_ Claude Code, so it can't reach the in-plugin CLI script (which is located via `CLAUDE_PLUGIN_ROOT`). The bridge is the Homebrew binary: it sits at a stable `$PATH` location that `brew upgrade` relinks in place, so the wiring survives plan-keeper upgrades.

```bash
brew install paulbaranowski/tap/plan-keeper   # stable entrypoint on $PATH
plan-keeper crew install                       # wire it into crew.config.ts (idempotent)
```

`crew install` patches your groundcrew config (`~/.config/groundcrew/crew.config.ts` by default, or `$GROUNDCREW_CONFIG` / `--config`). It injects one **sentinel-wrapped** region — a `plans` shell source in `sources:` — backs the config up first, validates the patch by having `crew doctor` load it, and rolls back if the patch broke the TypeScript. Re-running replaces the managed region in place (idempotent). It does **not** touch `workspace.knownRepositories` — registering the repos groundcrew dispatches into is left to you. Full walkthrough — `--dry-run`, the manual-paste fallback, and exactly what gets injected — is in [`groundcrew/README.md`](groundcrew/README.md).

### How dispatch works

groundcrew talks to plan-keeper through four `crew` subcommands baked into the injected config:

- **`crew fetch`** — globs `~/plans/*/*.md` (one level deep, skipping `done/` and `deferred/`) and emits one issue per plan. `Status: todo` plans are dispatchable; `Status: backlog` plans are fetched but held out of the pool. Each issue's id is the plan's **`Plan-keeper Ticket`** (`plan-<digits>`), minted once and then frozen — so a renamed plan keeps its id.
- **`crew get ${id}`** — resolves one plan by its `Plan-keeper Ticket`, searching active, then `done/`, then `deferred/`.
- **`crew start ${id}`** — flips that plan's `Status` to `in-progress` so the next fetch drops it from the dispatch pool.
- **`crew review ${id}`** — flips it to `in-review` once its PR opens.

Because an `${id}` can only ever name a plan inside `~/plans/`, the resolver never globs anywhere else — there's no path to validate.

### Dependencies between plans

A plan can declare prerequisites with a `Blocked-by:` frontmatter line — a comma-separated list of prerequisite ticket IDs in the same repo (a `Plan-keeper Ticket`, `Linear Ticket`, or `Jira Ticket`), each with an optional `(filename)` hint that is ignored:

```text
Blocked-by: plan-849321 (auth-schema), ENG-456 (token-store)
```

On `fetch`, plan-keeper resolves each reference and embeds a `{id, title, status}` snapshot in the issue's `blockers` array. groundcrew holds any `todo` plan while **any** blocker isn't `done`, then auto-dispatches it on the next fetch once they all are. plan-keeper never masquerades the plan's real `Status` — the gate lives in groundcrew. Set it with `file-meta set --blocked-by`; details and cycle handling are in [`groundcrew/README.md`](groundcrew/README.md#dependencies-between-plans). To generate a whole wave of dependency-wired slices from one plan at once, use [`plan-split`](skills/plan-split/).

### Managing the queue with `plan-crew`

The `plan-crew` skill is the human-facing front end for the dispatch queue — triggered by phrases like "show the groundcrew queue" or "queue these plans for groundcrew." It does two things, both backed by the `crew queue` subcommands:

1. **Shows the queue** (`crew queue list`) — the current repo by default, every repo on "all repos" (`--all`), or one named repo (`--repo <name>`). Plans are grouped by `Status`: **Queued** (`todo` — the live dispatch pool), **Available** (`backlog` or unset — promote candidates), and read-only **In flight** (`in-progress`) / **In review** (`in-review`). A plan held by unfinished `Blocked-by:` prerequisites is flagged with its blockers and is never presented as ready-to-dispatch.
2. **Promotes and dequeues in bulk** (`crew queue set`) — `backlog → todo` to add to the queue, `todo → backlog` to pull out. Multi-select, and confirmation is required before any write. Promoting fills `Agent: claude` when no agent is set and mints the `Plan-keeper Ticket` if absent, so the dispatch id is visible the moment a plan is queued; dequeue never touches `Agent`.

plan-crew is the multi-select, cross-repo counterpart to `plan-update` (the single-plan frontmatter editor). It only ever writes `Status` `todo`/`backlog` (plus the promote-time `Agent`/`Plan-keeper Ticket` fills) — the system-managed states (`in-progress`, `in-review`, `done`) are written by groundcrew and `plan-done`, never here.

## Repo derivation

`<repo>` auto-derives from `git remote get-url origin` (or `basename $PWD` as a fallback). Each skill also accepts an explicit override phrased naturally — "save the plan to `herds`", "do a plan from `general`", "archive the plan in `scratch`". Full algorithm: [`repo-derivation.md`](repo-derivation.md).

The override and auto-derive paths normalize differently: auto-derived names are kept verbatim (so `herds_mobile_app` stays `herds_mobile_app`), but user-typed overrides are lowercased with whitespace-to-hyphen ("General Folder" → `general-folder`). The asymmetry is deliberate — a git remote name is already canonical, but a user-typed phrase usually isn't.

## Command-line usage

`scripts/plan_keeper_cli.py` is the canonical interface behind every skill — the skills never write to `~/plans/` directly. The same source file ships two ways: the plugin invokes `plan_keeper_cli.py` in place, while `brew install paulbaranowski/tap/plan-keeper` packages that exact source into the version-stable standalone `plan-keeper` binary (one source, two delivery vehicles — no second copy to drift). The skills call the in-tree script; groundcrew, which runs outside Claude Code, calls the brew binary.

Invoke it either way — they're the same tool:

```bash
plan-keeper <subcommand> …                                  # Homebrew binary
python3 "$CLAUDE_PLUGIN_ROOT/scripts/plan_keeper_cli.py" …  # in-plugin script
```

Every subcommand takes `--help`. Mutations are atomic (tmp file + `fsync` + `os.replace`), and collisions surface as a structured exit-2 signal the skills present to the user rather than treating as fatal.

### Subcommand reference

#### Repo resolution

- `repo name [--override NAME] [--full]` — print the resolved `<repo>` folder name (`--full` emits `owner/name` from the git remote). See [Repo derivation](#repo-derivation).
- `repo list` — list every repo under `~/plans/` with per-state counts (`active`/`done`/`deferred`).

#### Listing plans

- `list [--override NAME | --all-repos] [--state active|done|deferred] [--status <csv> | --group]` — list plans newest-first. `--status in-progress,todo` filters to those states and tiers the output; `--group` clusters by project along the `idea → exec-plan` Kind pipeline.

#### Saving plans

- `save --topic "…" [--kind <kind>] [--extension md] [--date YYYY-MM-DD] [--on-collision fail|suffix|overwrite]` — write the stdin body to `~/plans/<repo>/<date>-<slug>.<ext>` (markdown saves get default `Status`/`Created` frontmatter).
- `save --from-path /path/to/file` — move an existing on-disk file into the tree verbatim (the `.json`+`.md` task-list-builder pair uses this).

#### Frontmatter (`file-meta`)

Locate by `--file PATH` or `--ticket ID` (any of the plan's id fields, across repos):

- `file-meta get …` — print frontmatter as JSON.
- `file-meta set … [--status … --agent … --kind … --completed-on … --blocked-by … --plankeeper-ticket … --linear-ticket … --jira-ticket …]` — edit fields. Setting `--status done|deferred` relocates the plan into `done/`/`deferred/` (`done` also stamps `Completed on`).
- `file-meta strip …` — print the body with the frontmatter removed.

#### Ticket systems: `linear` / `jira`

Each provider owns `api`/`push`/`config`:

- `linear push (--file PATH | --ticket ID) [--force-new]` / `jira push …` — create or update a ticket from a plan, stamping the id back into frontmatter.
- `linear api <kind>` / `jira api <kind>` — low-level metadata calls (Linear: `viewer`/`teams`/`projects`/`labels`/`users`; Jira: `viewer`/`projects`/`components`/`issuetypes`/`users`).
- `linear config get|save|refresh` / `jira config …` — CRUD the provider's section in `~/plans/<repo>/.plankeeper.json`.

#### Groundcrew (`crew`)

See [Groundcrew integration](#groundcrew-integration):

- `crew install [--config PATH] [--dry-run]` — wire `~/plans/*` into a groundcrew config.
- `crew fetch` / `crew get ${id}` / `crew start ${id}` / `crew review ${id}` — the machine protocol groundcrew's config calls directly.
- `crew queue list [--all | --repo NAME]` — emit the queue as a JSON array of `{repo, file, status, agent, blocked, blockedBy}`.
- `crew queue set --status todo|backlog [--default-agent claude]` — bulk-set `Status` on newline-delimited plan paths read from stdin.

#### Maintenance

- `upgrade` — self-update the Homebrew binary in place (`brew update && brew upgrade plan-keeper`, then re-run `crew install`); refuses when plan-keeper isn't a brew install.

### Examples

```bash
# Queue two plans for groundcrew (paths on stdin via a quoted heredoc):
plan-keeper crew queue set --status todo --default-agent claude <<'EOF'
/Users/you/plans/herds/2026-05-22-refactor-db.md
/Users/you/plans/wild-horses/2026-05-21-readme-pass.md
EOF

# Mark a prerequisite dependency, then archive a finished plan:
plan-keeper file-meta set --file ~/plans/herds/2026-05-22-refactor-db.md --blocked-by "plan-849321 (auth-schema)"
plan-keeper file-meta set --file ~/plans/herds/2026-05-20-fix-auth.md --status done
```

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
| `skills/plan-split/SKILL.md`       | Instructions for the decompose-into-dependency-wired-slices flow                                                              |
| `skills/plan-done/SKILL.md`        | Instructions for the archive flow                                                                                             |
| `scripts/plan_keeper_cli.py`       | Bundled CLI entry shim — the only sanctioned mutator for `~/plans/`                                                           |
| `scripts/plan_keeper/`             | CLI implementation package (errors, naming, storage, frontmatter, config, http, linear, jira, push, groundcrew, upgrade, cli) |
| `scripts/tests/`                   | Stdlib `unittest` suite — one `test_<module>.py` per package module, shared harness in `support.py`                           |
| `scripts/plan-keeper-cli-allow.sh` | PreToolUse hook script — auto-approves CLI Bash invocations                                                                   |
| `hooks/hooks.json`                 | PreToolUse hook registration                                                                                                  |
| `repo-derivation.md`               | Shared algorithm — auto-derive + override normalization rules                                                                 |
| `groundcrew/README.md`             | Deep reference for the groundcrew connection — `crew install`, dispatch protocol, dependencies                                |

Run the CLI tests from the repo root (stdlib only — no pytest/uv needed):

```text
python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
```
