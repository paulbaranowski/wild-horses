# plan-keeper → groundcrew shell adapter

These scripts let groundcrew dispatch tickets from `~/plans/<repo>/*.md`. They are thin bash wrappers around `plan_keeper_cli.py` subcommands.

## Install

The scripts run from this plugin directory without copying — but the path changes when the plugin version bumps. For a stable path, copy them once to `~/.config/groundcrew/plan-source/`:

```bash
mkdir -p ~/.config/groundcrew/plan-source
cp -p ./fetch.sh ./resolveOne.sh ./markInProgress.sh ~/.config/groundcrew/plan-source/
```

The scripts use `$(dirname "$0")/../scripts/plan_keeper_cli.py` to find the CLI — adjust if you copy to a different location.

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

- **fetch.sh** — globs `~/plans/*/*.md` (one level deep, skipping `done/` and `deferred/`). Each plan with valid frontmatter becomes one issue. `Status: backlog` translates to adapter status `other` (visible to `crew doctor`, never dispatched). `Status: todo` translates to `todo` (dispatchable).
- **resolveOne.sh** — given an `${id}` (filename stem), searches active, then `done/`, then `deferred/`. Exits 3 if the file doesn't exist.
- **markInProgress.sh** — reads `{"path": "..."}` from stdin, atomic-write flips that plan's `Status` to `in-progress` so the next `fetch` tick sees it as out of the dispatch pool.

## Promoting a plan

```bash
# Save a plan via plan-save (defaults to Status: backlog).
# Then promote to todo via plan-update or directly:
python3 /path/to/plan_keeper_cli.py file-meta update \
  --file ~/plans/<repo>/<file>.md \
  --field Status=todo
```

After promotion, the next `crew run` (or `crew doctor`) will see the plan as eligible.
