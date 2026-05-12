# harness

Make a codebase agent-friendly. Three analysis commands diagnose what AI agents will struggle with — reasoning gaps, feedback-loop blockers, and missing orientation docs — and a paired task-list pipeline drives the resulting remediation plans to completion.

Install:

```text
/plugin install harness@wild-horses
```

## Commands

| Command                                                       | Asks                                                         | When to use                                                                                                  |
| ------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| **[`/harness:setup`](docs/setup.md)**                         | _Does the repo have a map for the agent to read?_            | Once per project. Scaffolds `CLAUDE.md`, `ARCHITECTURE.md`, and `docs/` so agents can orient quickly.        |
| **[`/harness:reasoning-gaps`](docs/reasoning-gaps.md)**       | _If an AI agent read this code, what would it get wrong?_    | Comprehension review — types, implicit control flow, structure & docs. Best for dynamically typed languages. |
| **[`/harness:feedback-blockers`](docs/feedback-blockers.md)** | _Can an AI edit this code and know whether it got it right?_ | Correctness & observability — encapsulation, OOP design, testability, harness-friendliness.                  |

Both analysis commands end by producing a paired `.json` + `.md` plan and handing it to the task-list pipeline below.

## Skills — task-list pipeline

A three-skill pipeline that produces, executes, and inspects structured task lists matching the schema at [`task-list-schema.md`](task-list-schema.md). Every mutation goes through the bundled `task_list_cli.py`; a PreToolUse hook auto-approves invocations of that CLI so the agent loop runs without per-call prompts.

| Skill                                                | Role     | What it does                                                                                                        |
| ---------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------- |
| **[`task-list-builder`](docs/task-list-builder.md)** | Creates  | Builds a paired `.json` + `.md` task list from a report, free-form text, or conversation context. Schema-validated. |
| **[`task-list-runner`](docs/task-list-runner.md)**   | Executes | Drives a task list to completion via sequential foreground `Agent` calls. Has a corruption gate between iterations. |
| **[`task-list-viewer`](docs/task-list-viewer.md)**   | Inspects | Read-only summary or per-task detail. Auto-locates the active plan in `docs/exec-plans/active/`.                    |

The runner is a strict alternative to the [superpowers](https://github.com/obra/superpowers) `writing-plans` + `executing-plans` skills — see the comparison in [task-list-runner](docs/task-list-runner.md#when-to-use-this-loop-vs-superpowers-plans).

## Recommended order

```text
1. /harness:setup                  # only once per project
2. /pyright:run-and-fix            # Python only — resolves the typing axis
3. /harness:reasoning-gaps         # comprehension axis
4. /harness:feedback-blockers      # observability axis
```

Each step asks a harder question than the last. Steps 3 and 4 each end by handing the resulting plan to [task-list-runner](docs/task-list-runner.md) for unattended execution.
