# task-list-viewer

Read-only viewer for harness task-list JSON files. Auto-locates the active task file in `docs/exec-plans/active/`.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo. The JSON schema it reads is defined once in [`../../task-list-schema.md`](../../task-list-schema.md). Pairs with [`task-list-builder`](../task-list-builder/) (creates plans) and [`task-list-runner`](../task-list-runner/) (executes them) - this skill only inspects.

## Invoke

```text
/task-list-viewer                 # summary: counts, plan path, in-progress task, pending titles
/task-list-viewer 4                # full detail for task 4
/task-list-viewer --file path.json # target a specific file instead of auto-locating
```

Also model-invoked - trigger phrases include "show me the task list", "view the plan", "what tasks are left".

## What it does

1. **Parses arguments** for an optional task id and an optional `--file` override.
2. **Auto-locates the task file** (unless `--file` was given): globs `docs/exec-plans/active/*.json` (falling back to `*.md` frontmatter pointers), validating each candidate via `task_list_cli.py status`. Asks the user to pick when more than one match is found - never guesses by recency.
3. **Displays** either a single task's full detail (given an id) or a summary: file path, status counts, the paired markdown plan's path, the in-progress task (if any), and every pending task's id/title/effort.

Strictly read-only: only calls the CLI's `status`, `list`, and `get` verbs, never `next`/`start`/`draft`/`publish`/`set-status`/`verify`. Pointing the user at `/task-list-runner` (to advance the plan) or `/task-list-builder` (to revise it) is this skill's job when they ask for either.

## Install

The skill ships with the `harness` plugin:

```text
/plugin install harness@wild-horses
```
