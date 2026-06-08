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
then `~/.config/groundcrew/crew.config.ts`. If you don't have a config yet, run
`crew init` first.

What it does:

1. Resolves the absolute path to your `plan-keeper` binary (via `which`) and bakes
   it into the injected command strings, so dispatch never depends on groundcrew's
   runtime `$PATH`.
2. Backs up your config to `crew.config.ts.bak`.
3. Injects two **sentinel-wrapped** regions — one in `sources:`, one in
   `workspace.knownRepositories:` — each delimited by
   `/* plan-keeper:managed:start */ … /* plan-keeper:managed:end */`. Re-running
   replaces these regions in place (no duplication; the repo set refreshes), so
   it's fully idempotent. The default `crew init` config ships `sources:`
   commented out (the Linear adapter is implicit); when there's no active
   `sources:` array, `crew install` adds one.
4. Validates the patched config with `crew doctor`. The gate is whether doctor
   can **load** the config — a patch that broke the TS is rolled back from the
   backup. Doctor failures unrelated to the plans source (a missing Linear API
   key, an absent `projectDir`) do **not** roll back: the plans wiring stays in
   place and `crew install` prints a note pointing you at `crew doctor` to
   review the rest.
5. Reports how many plans are visible to `fetch`.

`plan-keeper crew install --dry-run` prints the diff it would apply and writes
nothing. If your config has no active `sources:` array and no `export default`
object to add one to — or no `knownRepositories:` array — `crew install` writes
nothing and prints the exact blocks for you to paste manually.

## What gets injected

Into `sources:`:

```ts
/* plan-keeper:managed:start */
      { kind: "shell", name: "plans",
        commands: {
          verify: "/opt/homebrew/bin/plan-keeper crew fetch >/dev/null",
          fetch: "/opt/homebrew/bin/plan-keeper crew fetch",
          resolveOne: "/opt/homebrew/bin/plan-keeper crew get ${id}",
          markInProgress: "/opt/homebrew/bin/plan-keeper crew start ${id}" } },
/* plan-keeper:managed:end */
```

Into `workspace.knownRepositories:`: the repo directory names discovered one
level under `~/plans/`, alongside whatever entries are already there.

## How dispatch works

- **fetch** — globs `~/plans/*/*.md` (one level deep, skipping `done/` and
  `deferred/`). Each plan with valid frontmatter becomes one issue. `Status:
backlog` maps to adapter status `other` (fetched but not dispatched); `Status:
todo` maps to `todo` (dispatchable). Each issue's `id` is the plan's
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

## Promoting a plan

```bash
# Save a plan via plan-save (defaults to Status: backlog).
# Then promote to todo:
plan-keeper file-meta set --file ~/plans/<repo>/<file>.md --status todo
```

Or promote/dequeue across all repos interactively with the `plan-crew` skill,
which wraps the `crew queue list` / `crew queue set` subcommands.

After promotion, the next `crew run` dispatches the plan and it shows up in the
`crew status` Queue. (`crew doctor` only checks host prerequisites — it doesn't
list plans.)
