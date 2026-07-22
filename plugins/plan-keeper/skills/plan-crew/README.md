# plan-crew

Manage the groundcrew dispatch queue: view it, queue plans for pickup, promote plans to todo in bulk, or dequeue them.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo. See [`../../groundcrew/README.md`](../../groundcrew/README.md) for what actually makes a plan dispatchable.

## Invoke

```text
/plan-crew                 # the current repo's queue
```

Also model-invoked - trigger phrases include "see or manage the groundcrew queue", "queue a plan for groundcrew", "promote plans to todo". Say "all repos" for the whole `~/plans/` tree, or name a specific repo, to change scope.

## What it does

1. **Shows the queue**, scoped to the current repo by default (`--all` for every repo, `--repo <name>` for one other), grouped into Queued (`todo` with an `Agent`, ready to dispatch), Needs an Agent (`todo` but groundcrew will skip it until it has one), In flight/In review (read-only context), and Available (`backlog`, promote candidates) - numbered so the user can act by number.
2. **Parses the user's reply** (`promote <numbers>` / `dequeue <numbers>`, either or both) and maps each number back to its `{repo, file}`.
3. **Confirms** exactly what will change - including "will set Agent: claude" for a promote with no existing Agent - before writing anything.
4. **Applies** one CLI call per repo per direction: `crew queue add` (promote, stamps a missing `Agent`) or `crew queue drop` (dequeue, never touches `Agent`).
5. **Re-shows the queue** so the user sees the result.

The `Agent` tag is the gate this skill actually controls - `todo` alone doesn't make a plan dispatchable; groundcrew also needs an `Agent` and a registered repo.

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
