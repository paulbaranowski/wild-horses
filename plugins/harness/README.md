# harness

Make a codebase agent-friendly. Two analysis commands diagnose what AI agents will struggle with — reasoning gaps and feedback-loop blockers — and a paired task-list pipeline drives the resulting remediation plans to completion.

Install:

```text
/plugin install harness@wild-horses
```

## Commands

| Command                                                       | Asks                                                         | When to use                                                                                                  |
| ------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
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
1. /harness:reasoning-gaps         # comprehension axis — design types & flow
2. /pyright:run-and-fix            # Python only — enforces type design at every call site
3. /harness:feedback-blockers      # observability axis
```

Each step asks a harder question than the last, and each ends by handing the resulting plan to [task-list-runner](docs/task-list-runner.md) for unattended execution.

> **Why reasoning-gaps before pyright (counter-intuitive).** Pyright is a consistency checker, not a design tool — run it first on weakly-typed code and the easy fix is `: Any` and `# type: ignore`, which silences errors without improving the types. Reasoning-gaps redesigns the types first (e.g. `str` → `Literal[...]`, `dict[str, Any]` → `TypedDict`); pyright then propagates that design across every call site. Full rationale: [pyright README — Relationship to /harness:reasoning-gaps](../pyright/README.md#relationship-to-harnessreasoning-gaps).
