# plan-keeper → groundcrew shell adapter

These scripts let groundcrew dispatch tickets from `~/plans/<repo>/*.md`. They are thin bash wrappers around `plan_keeper_cli.py` subcommands.

## Install

You have two options:

### Option A — reference the plugin path directly (simplest)

Set `crew.config.ts` to point at the scripts inside the installed plugin tree. The scripts auto-resolve `plan_keeper_cli.py` next door, so no env var is needed. Trade-off: the path embeds the plugin version (e.g., `~/.claude/plugins/cache/wild-horses/plan-keeper/1.4.1/groundcrew/...`) and will need to be updated when the plugin version bumps.

### Option B — copy to a stable location and set `$PLAN_KEEPER_CLI`

Copy the scripts to `~/.config/groundcrew/plan-source/`, then point `$PLAN_KEEPER_CLI` at the bundled CLI. The scripts honor that env var and fall back to a sibling relative path only when it's unset:

```bash
mkdir -p ~/.config/groundcrew/plan-source
cp -p ./fetch.sh ./resolveOne.sh ./markInProgress.sh ~/.config/groundcrew/plan-source/

# Add to ~/.zshrc, ~/.bashrc, or wherever your shell rc lives:
export PLAN_KEEPER_CLI="$HOME/.claude/plugins/cache/wild-horses/plan-keeper/<version>/scripts/plan_keeper_cli.py"
```

Replace `<version>` with the installed plugin version (currently `1.4.1`). When you upgrade the plugin, only `$PLAN_KEEPER_CLI` needs to change — your `crew.config.ts` paths stay stable.

## crew.config.ts entry

```ts
sources: [
  {
    kind: "shell",
    name: "plans",
    commands: {
      fetch: "/Users/<you>/.config/groundcrew/plan-source/fetch.sh",
      resolveOne: "/Users/<you>/.config/groundcrew/plan-source/resolveOne.sh ${id}",
      markInProgress: "/Users/<you>/.config/groundcrew/plan-source/markInProgress.sh",
    },
  },
],
```

## How it works

- **fetch.sh** — globs `~/plans/*/*.md` (one level deep, skipping `done/` and `deferred/`). Each plan with valid frontmatter becomes one issue. `Status: backlog` translates to adapter status `other` (fetched but never dispatched — `crew status` hides non-`todo` plans from its Queue, so confirm a specific one with `crew status <id>`). `Status: todo` translates to `todo` (dispatchable). Each issue's `id` is a synthesized `plan-<digits>` (a stable hash of repo + filename, so it satisfies groundcrew's ticket-id shape — plan filenames don't). fetch also mirrors that id into the plan's `Ticket` / `Ticket System` frontmatter (`Ticket System: groundcrew`) so a human can see the mapping. It's display-only and self-healing — the hash stays canonical — and it never overwrites a `linear`/`jira` reference left by plan-push, so a pushed plan keeps its real tracker ticket and still dispatches via the recomputed id.
- **resolveOne.sh** — given a synthesized `${id}` (`plan-<digits>`), recomputes each plan's id across active, then `done/`, then `deferred/`, and returns the match. Exits 3 if no plan maps to that id.
- **markInProgress.sh** — reads `{"path": "..."}` from stdin, atomic-write flips that plan's `Status` to `in-progress` so the next `fetch` tick sees it as out of the dispatch pool.

## Promoting a plan

```bash
# Save a plan via plan-save (defaults to Status: backlog).
# Then promote to todo via plan-update or directly:
python3 /path/to/plan_keeper_cli.py file-meta update \
  --file ~/plans/<repo>/<file>.md \
  --field Status=todo
```

Or, to promote (and dequeue) plans across all repos interactively, use the `plan-crew` skill, which wraps the `queue list` / `queue set` CLI subcommands.

After promotion, the next `crew run` will dispatch the plan, and it shows up in the `crew status` Queue. (`crew doctor` only checks host prerequisites — it doesn't list plans.)
