# plan-keeper → groundcrew connection

groundcrew can dispatch tickets straight from your `~/plans/<repo>/*.md` plans.
One command wires it up, and the connection survives plan-keeper upgrades.

## Why a command instead of shell wrappers

groundcrew runs _outside_ Claude Code, so it never sees `CLAUDE_PLUGIN_ROOT` (the
env var in-plugin scripts use to find the current plugin version). The old setup
worked around this two ways — pinning a version-stamped plugin-cache path, or
copying shell wrappers out of the tree and setting `$PLAN_KEEPER_CLI`. Both rotted
on upgrade (the cache path moved; the wrappers drifted on subcommand renames).

Homebrew supplies the missing stable entrypoint. `brew install` puts `plan-keeper`
on `$PATH` at a brew-managed symlink (e.g. `/opt/homebrew/bin/plan-keeper`) that
`brew upgrade` relinks in place. The brew binary is version-locked in lockstep
with the plugin (see `../RELEASING.md`), so calling it directly eliminates both
rot modes.

## Install

Two steps:

```bash
# 1. Put the version-stable `plan-keeper` binary on your PATH.
brew install paulbaranowski/tap/plan-keeper

# 2. Wire it into your groundcrew config (idempotent — safe to re-run).
plan-keeper crew install
```

`crew install` resolves your config path from `--config`, then `$GROUNDCREW_CONFIG`,
then the first `crew.config.*` it finds in `~/.config/groundcrew/` — searching the
same names groundcrew does (`crew.config.ts`, `.mjs`, `.js`, `.json`, …), so a
`crew.config.json` is found just like a `crew.config.ts`. If you don't have a
config yet, run `crew init` first.

Both config shapes are supported, decided by content (a TS config never parses as
JSON; a JSON one always does):

- **TS/JS** (`crew.config.ts`) is patched by string surgery with a
  **sentinel-wrapped** managed region (see [What gets injected](#what-gets-injected)).
- **JSON** (`crew.config.json`, `.crewrc`) has no comments, so it's parsed and the
  `plankeeper` entry is upserted into the `sources` array **by name** — the
  matching entry is replaced in place, foreign entries (e.g. `{ "kind": "linear" }`)
  are left untouched, and the file is re-serialized.

What it does:

1. Resolves the absolute path to your `plan-keeper` binary (via `which`) and bakes
   it into the injected command strings, so dispatch never depends on groundcrew's
   runtime `$PATH`.
2. Backs up your config alongside it (`<config>.bak`).
3. Injects the `plankeeper` shell source into `sources:`. Re-running replaces it in
   place (no duplication), so it's fully idempotent — for a TS config via the
   sentinel region, for a JSON config via the by-name upsert. The default `crew
init` (TS) config ships `sources:` commented out (the Linear adapter is
   implicit); when there's no active `sources:` array, `crew install` adds one. A
   JSON config with no `sources` key gets one created. `crew install` does **not**
   touch `workspace.knownRepositories` — registering the repos groundcrew may
   dispatch into is left to you.
4. Validates the patched config with `crew doctor`. The gate is whether doctor
   can **load** the config — a patch that broke it is rolled back from the
   backup. Doctor failures unrelated to the plans source (a missing Linear API
   key, an absent `projectDir`) do **not** roll back: the plans wiring stays in
   place and `crew install` prints a note pointing you at `crew doctor` to
   review the rest.
5. Reports how many plans are visible to `fetch`.

`plan-keeper crew install --dry-run` prints the diff it would apply and writes
nothing. If a TS config has no active `sources:` array and no `export default`
object to add one to (or a JSON config isn't an object with a patchable `sources`
array), `crew install` writes nothing and prints the exact block — in the config's
own format — for you to paste manually.

## What gets injected

A `plankeeper` shell source, into `sources:`. For a TS config, as a
sentinel-wrapped region:

```ts
/* plan-keeper:managed:start */
      { kind: "shell", name: "plankeeper",
        commands: {
          verify: "/opt/homebrew/bin/plan-keeper crew fetch >/dev/null",
          fetch: "/opt/homebrew/bin/plan-keeper crew fetch",
          resolveOne: "/opt/homebrew/bin/plan-keeper crew get ${id}",
          markInProgress: "/opt/homebrew/bin/plan-keeper crew start ${id}",
          markInReview: "/opt/homebrew/bin/plan-keeper crew review ${id}",
          markDone: "/opt/homebrew/bin/plan-keeper file-meta set --ticket ${id} --status done --on-collision suffix" } },
/* plan-keeper:managed:end */
```

For a JSON config, the same source as an object in the `sources` array:

```json
{
  "kind": "shell",
  "name": "plankeeper",
  "commands": {
    "verify": "/opt/homebrew/bin/plan-keeper crew fetch >/dev/null",
    "fetch": "/opt/homebrew/bin/plan-keeper crew fetch",
    "resolveOne": "/opt/homebrew/bin/plan-keeper crew get ${id}",
    "markInProgress": "/opt/homebrew/bin/plan-keeper crew start ${id}",
    "markInReview": "/opt/homebrew/bin/plan-keeper crew review ${id}",
    "markDone": "/opt/homebrew/bin/plan-keeper file-meta set --ticket ${id} --status done --on-collision suffix"
  }
}
```

`crew install` does not modify `workspace.knownRepositories` — register the
repos groundcrew may dispatch into yourself.

## What makes a plan dispatchable

groundcrew dispatches a plan only when **all** of these gates pass at once — no
single one is sufficient:

1. **It's an active plan in a repo bucket.** The file lives one level deep under
   `~/plans/<repo>/` (not in `done/` or `deferred/`, which `fetch` skips).
2. **Its repo is registered with groundcrew.** The `<repo>` must be listed in
   your `workspace.knownRepositories` — `crew install` does **not** add it (see
   the install notes above). An unregistered repo's plans can't be dispatched
   into, even when everything else is in order.
3. **`Status: todo`.** `backlog` (and any other value) is fetched but held.
4. **It carries an `Agent:` tag.** `fetch` **skips every agent-less plan in every
   status, including `todo`** — there is no implicit "default to claude." The
   `Agent` tag is the signal that a plan was explicitly handed to groundcrew (a
   human driving a plan locally via `plan-do` clears it). The `plan-crew` skill
   (`crew queue add`) is what writes `Agent: claude` when promoting.
5. **It isn't blocked.** Every `Blocked-by` prerequisite must be `done` (see
   [Dependencies between plans](#dependencies-between-plans)).

## How dispatch works

- **fetch** — globs `~/plans/*/*.md` (one level deep, skipping `done/` and
  `deferred/`). Each plan with valid frontmatter **and an `Agent:` tag** becomes
  one issue (agent-less plans are skipped in every status — gate 4 above).
  `Status: backlog` maps to adapter status `other` (fetched but not dispatched);
  `Status: todo` maps to `todo` (dispatched once its repo is registered and it's
  unblocked). Each issue's `id` is the plan's
  **`Plan-keeper Ticket`** — a `plan-<digits>` id minted once (at `plan-save`,
  or here on first fetch if a legacy plan lacks one) and then frozen. fetch mints
  it only when absent and **never overwrites** an existing one, so a renamed plan
  keeps its id. The minted value is a hash of repo + filename (plan filenames
  don't fit groundcrew's ticket-id shape) used purely as a one-time seed. A plan
  can independently carry `Linear Ticket` / `Jira Ticket` values, left untouched.
- **resolveOne** (`crew get ${id}`) — reads each plan's stored `Plan-keeper
Ticket` across active, then `done/`, then `deferred/`, and returns the match.
  Exits 3 if no plan carries that id.
- **markInProgress** (`crew start ${id}`) — resolves `${id}` with the _same_
  resolver as `crew get`, then atomic-write flips that plan's `Status` to
  `in-progress` so the next `fetch` sees it out of the dispatch pool. Because the
  id can only ever name a plan inside `~/plans/`, there's no path to validate —
  the resolver never globs anywhere else.
- **markInReview** (`crew review ${id}`) — same resolver, atomic-write flips the
  plan's `Status` to `in-review` when its PR opens.
- **markDone** (`file-meta set --ticket ${id} --status done --on-collision
suffix`) — once the PR merges. Unlike the in-place `start`/`review` flips,
  `done` is **terminal**: it relocates the plan into `done/` and stamps
  `Completed on`. So this leg reuses the same `file-meta set` engine `plan-done`
  uses (addressed by the plan's `Plan-keeper Ticket`, which _is_ `${id}`) rather
  than a bespoke `crew done`. `--on-collision suffix` keeps the unattended
  archive safe: a same-name plan already in `done/` is suffixed (`-N`), never
  overwritten, and the leg never fails the dispatch.

## Promoting a plan

Use the `plan-crew` skill (or `crew queue add` directly) — it both flips
`Status: todo` **and** stamps `Agent: claude` when the plan has none, so the plan
clears gates 3 and 4 in one step:

```bash
plan-keeper crew queue add --repo <repo> <file>.md
```

`plan-crew` wraps `crew queue list` / `crew queue add` / `crew queue drop` and
lets you promote/dequeue across repos interactively.

`file-meta set --status todo` flips the status **but does not write an `Agent:`
tag**, so a plan promoted that way stays agent-less and is **not** dispatched
(gate 4). Either add the tag yourself (`file-meta set --agent claude`) or, more
simply, promote through `crew queue add`, which does both.

Once a plan passes every gate in [What makes a plan
dispatchable](#what-makes-a-plan-dispatchable), the next `crew run` dispatches it
and it shows up in the `crew status` Queue. (`crew doctor` only checks host
prerequisites — it doesn't list plans.)

## Dependencies between plans

A plan can declare prerequisites with a `Blocked-by:` frontmatter line — a
comma-separated list of prerequisite **ticket IDs** in the same repo (a
`Plan-keeper Ticket`, a `Linear Ticket`, or a `Jira Ticket`), each with an
optional `(filename)` hint that is ignored:

```text
Blocked-by: plan-849321 (auth-schema), ENG-456 (token-store)
```

On `fetch`, plan-keeper resolves each reference to its in-repo plan and embeds a
`{id, title, status}` snapshot in the issue's `blockers` array. groundcrew's own
eligibility check holds any `todo` plan while **any** embedded blocker's status
is not `done` — so a dependent plan is not dispatched until every prerequisite is
finished, and it auto-dispatches on the next `fetch` once they are. plan-keeper
keeps reporting the plan's real `Status` (no masquerade); the gate lives in
groundcrew. `crew get` carries the same snapshot, so the resolveOne path can't
slip a held plan through.

Set it with:

```bash
plan-keeper file-meta set --file ~/plans/<repo>/<file>.md \
  --blocked-by "plan-849321 (auth-schema), ENG-456"
```

A reference that matches no plan, or points at a `deferred/` plan, holds the
dependent and prints a `note:` on stderr. Dependency cycles (A↔B) are detected
and warned on stderr; they stay held (neither can reach `done` first).
`crew queue list` also reports `blocked` / `blockedBy` per plan for the
`plan-crew` skill to render.
