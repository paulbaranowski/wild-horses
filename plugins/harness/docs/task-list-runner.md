# task-list-runner

**Role:** drive a structured task list (JSON file matching [`task-list-schema.md`](../task-list-schema.md)) to completion by dispatching each task to a foreground `Agent` call, one at a time. Pairs with [task-list-builder](task-list-builder.md), which produces the JSON.

## Usage

```text
/task-list-runner                                  # auto-locate in-progress JSON
/task-list-runner docs/exec-plans/active/foo.json  # run a specific JSON
/task-list-runner docs/exec-plans/active/foo.md    # follow the .md frontmatter to its JSON
/task-list-runner --all                            # run every remaining task non-interactively
/task-list-runner --next                           # run exactly one task and stop
```

When no path is given, the runner globs `docs/exec-plans/active/*.json` and accepts any file whose `cli status` reports `remaining > 0`. Multiple matches → it lists them and asks; zero matches → it stops (it does not build a new task list — that's [task-list-builder](task-list-builder.md)'s job).

## How it works

For each pending task, the runner dispatches a fresh foreground `Agent` with the task's prompt. Tasks run **strictly sequentially** — they may depend on prior tasks' edits, so parallelism is forbidden.

Acceptance has two stages:

1. **`verifySteps`** — top-level array (or per-task override). The CLI runs typecheck → tests in order, fail-fast, with per-step log files at `/tmp/verify-<id>-step<i>-<slug>.log`.
2. **`agentValidations`** — a fresh-context, read-only `Explore` subagent evaluates an array of inspection-only facts. The runtime denies `Write`/`Edit` to it, so it cannot "fix" the code into passing.

## Corruption gate

Between every iteration the runner re-runs `cli status`. Any non-zero exit halts the loop on a malformed file. This exists because in the prior iteration of this work, an in-place edit silently corrupted a 37-task session and the corruption (a missing structural comma) went undetected for 19 subsequent iterations.

## CLI

The bundled CLI at `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py` is the canonical interface to the task file. **Every mutation goes through this CLI** — no exceptions. Subcommands:

| Subcommand                                              | What it does                                                                               |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `next`                                                  | Atomically claim and print the next task. Exits 14 if no tasks remain.                     |
| `start --id <N>`                                        | Flip task N from pending → in-progress.                                                    |
| `finish --id <N> --status complete\|failed --log-file` | Flip in-progress task N to terminal status; log read from file (or `-` for stdin heredoc). |
| `get --id <N>`                                          | Print one task as pretty JSON.                                                             |
| `list [--status <s>]`                                   | Print all tasks (or filtered) as a JSON array.                                             |
| `status`                                                | File-level metadata: counts + `remaining` integer + `plan` path. Doubles as the halt-gate. |
| `remaining`                                             | Compact non-terminal task array (`id`, `title`, `effort`, `status`) for display.           |
| `verify --id <N>`                                       | Execute the resolved `verifySteps` for task N in order.                                    |

A PreToolUse hook auto-approves invocations of this CLI, anchored on the plugin-specific path so a stray `task_list_cli.py` elsewhere doesn't get auto-approved. **Don't invent verbs** — argparse rejects anything outside the eight listed.

## Failure handling

Failing tasks move to `failed` status with a log; the loop continues to the next task. Recorded, not conversational — kick off `--all` and read the final report.

## When to use this loop vs. superpowers plans

| Aspect            | `superpowers:writing-plans` + `executing-plans`                | `task-list-builder` + `task-list-runner`                                                                   |
| ----------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Task type         | Exploratory — requirements ambiguous, plan shifts as you learn | Autonomous batch execution — uniform-shape tasks defined up front                                          |
| Human involvement | Review-gated — human inspects each step before the next runs   | Unattended — kick off `--all` and read the final report                                                    |
| Resume            | Manual — re-read the plan, find your place                     | First-class — `cli status` auto-locates in-progress files; `cli next` claims the next task atomically      |
| Plan stability    | Plan can be revised mid-execution at review checkpoints        | Plan is fixed up front; structural revisions go back through `task-list-builder` rewrite mode              |
| Failure handling  | Conversational — agent pauses at the checkpoint                | Recorded — failing tasks move to `failed` status with a log; the loop continues to the next task           |
| Test discipline   | Optional — author choice                                       | Mandatory paired `"Write tests for X"` task after every task with `createsNewCode: true`                   |
| Plan artifact     | Free-form Markdown                                             | Schema-validated JSON paired with a readable Markdown summary                                              |
| Verification      | Author writes verification steps in prose                      | Top-level `verifySteps` array; the CLI runs typecheck → tests in order, fail-fast, with per-step log files |
| Acceptance        | Read-and-judge by the executing agent                          | Fresh-context read-only `Explore` subagent evaluates `agentValidations` (runtime denies `Write`/`Edit`)    |
| Concurrency       | Subagent-driven supports parallel subagents                    | Strictly sequential foreground `Agent` calls — tasks may depend on prior edits                             |
| Typical scale     | A handful of well-scoped tasks                                 | 10–50 uniform tasks (the typical `/harness:reasoning-gaps` or `/harness:feedback-blockers` output)         |

Pick superpowers when the plan itself is a deliverable and a human will review each step. Pick the harness loop when the plan is a means to an end and you want strict verification and unattended execution across a homogeneous batch of tasks.
