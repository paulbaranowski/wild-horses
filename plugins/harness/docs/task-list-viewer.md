# task-list-viewer

**Role:** read-only inspection of a harness task list. Doesn't run, mutate, or build it. Pairs with [task-list-builder](task-list-builder.md) (creates plans) and [task-list-runner](task-list-runner.md) (executes them).

## Usage

```text
/task-list-viewer                                  # auto-locate active task file, show summary
/task-list-viewer 7                                # full detail on task 7
/task-list-viewer --file docs/exec-plans/active/foo.json
/task-list-viewer --file docs/exec-plans/active/foo.md
```

When no `--file` is given, the viewer auto-locates the active JSON in `docs/exec-plans/active/` the same way [task-list-runner](task-list-runner.md) does.

## What it shows

**Summary view (no task ID):**

- Task file path
- Counts: total, complete, in-progress, drafted, pending, failed
- The paired markdown plan path
- The in-progress task ID (if any)
- Pending task titles

**Detail view (with task ID):**

- That single task as pretty-printed JSON — title, prompt, effort, `verifySteps`, `agentValidations`, status.

## How it works

Uses the same CLI as [task-list-runner](task-list-runner.md) (`task_list_cli.py`), but only the **read-only verbs**: `status`, `list [--status pending|in-progress]`, `get --id <N>`. A single CLI keeps the viewer's interpretation of the file in lockstep with the runner's.

The viewer never calls mutation verbs (`next`, `start`, `draft`, `publish`, `set-status`, `verify`), even though they're available — viewing must not change the file. If a user asks _"what should I work on next?"_ or _"mark task 3 done"_ mid-session, point them at [task-list-runner](task-list-runner.md) instead.
