# Task List Schema

The harness loop produces and consumes a `.json` task file in `docs/exec-plans/active/`. **This document is the source of truth for that file's shape.**

Referenced by:

- `loop-protocol.md` — the Phase 4 options menu shared by `/harness:feedback-blockers` and `/harness:reasoning-gaps` delegates file production to `task-list-builder`, which writes files in this shape.
- `skills/task-list-builder/SKILL.md` — produces files matching this schema.
- `skills/task-list-runner/SKILL.md` — consumes files matching this schema.
- `skills/task-list-runner/task_list_cli.py` — does runtime validation in `load_and_validate`. The CLI deliberately validates only the subset it touches, so it doesn't need to be co-updated when fields below grow. This document is the broader human-readable contract.

---

## Top-level shape

Illustrative example (actual `tasks` come from whichever process generated the file):

```json
{
  "plan": "docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.md",
  "verifySteps": [
    { "name": "typecheck", "command": "<typecheck command, if applicable>" },
    { "name": "tests", "command": "<test command>" }
  ],
  "scope": ["<repo-relative file paths>"],
  "tasks": [
    {
      "id": 1,
      "title": "<intervention title>",
      "what": "<specific change — files to modify, structures to create, patterns to fix>",
      "resolves": ["<file:line>", "<file:line>"],
      "effort": "low | medium | high",
      "createsNewCode": true,
      "status": "pending",
      "agentValidations": [
        "<inspection-verifiable statement about post-change code — NOT 'Tests pass' or 'No type errors'>",
        "<another inspection-verifiable statement>"
      ],
      "log": null
    },
    {
      "id": 2,
      "title": "Write tests for <thing created in task 1>",
      "what": "<what to test, where to put tests>",
      "resolves": [],
      "effort": "low",
      "createsNewCode": false,
      "status": "pending",
      "agentValidations": [
        "Test file follows project test conventions",
        "At least N test cases covering happy path, errors, and edge cases"
      ],
      "log": null
    }
  ]
}
```

A minimal valid file lives at `skills/task-list-builder/example.json`.

---

## Top-level fields

- `plan` — repo-relative path to the paired `.md` file (the human-readable report). The runner does not modify this file; it is for humans.
- `verifySteps` — array of `{name, command}` objects, each describing one verification step the per-task Agent runs after implementing a task. Steps run in order; on first failure the Agent stops and reports which step (`name`) failed. **At least one step is required.** Conventional names: `typecheck`, `tests`, `lint` — but any non-empty string is valid. Discovered once during plan creation and reused every iteration. Individual tasks may declare their own `verifySteps` to override this default; see "Per-task `verifySteps` override" below.
- `scope` — repo-relative file paths preserved for potential re-analysis. Use paths relative to the repository root to avoid leaking local machine structure if the file is committed.
- `tasks` — array of task objects (see below).

## Task fields

- `id` — integer, unique within the file. Conventionally sequential `1, 2, 3, ...` with no gaps.
- `title` — short human-readable name.
- `what` — specific change: files to modify, structures to create, patterns to fix.
- `resolves` — array of `file:line` strings linking the task back to the findings it addresses. Repo-relative paths only.
- `effort` — `"low" | "medium" | "high"`.
- `createsNewCode` — `true` if the intervention creates new callable code (functions, classes, methods, services, models, protocols), `false` if it only restructures, annotates, or documents existing code. **Determines whether a paired test task is generated** (see "Paired test tasks" below).
- `agentValidations` — input array for the per-task validation prompt. After the runner executes `verifySteps` (the test / lint / typecheck commands), it dispatches a fresh-context validation subagent and passes this array as the list of statements for the subagent to evaluate by reading code. Each entry is one factual statement about the post-change code state; the subagent confirms it PASS or FAIL with `file:line` evidence. The schema-level rule for what belongs here is structural, not stylistic: **if you can write a shell command that answers the question, it belongs in `verifySteps`, not here**. The validation subagent has no way to evaluate command-answerable conditions except by re-running the commands `verifySteps` already ran (the duplicate-work pattern this design exists to prevent) or by rubber-stamping the result, so entries like `"Tests pass"`, `"No type errors"`, `"No lint errors"`, or `"Compiles"` are forbidden. Use this for facts only inspection can confirm: structural facts (`"validate_session is defined at module scope in src/auth/middleware.py"`), behavioral facts visible in code (``"`AuthMiddleware.__call__` delegates token validation to `validate_session`"``), or documentation facts (`"module docstring lists validate_session under the public API"`). Avoid vague entries like `"looks good"` or `"code is clean"` — the subagent reports `file:line` evidence, so each entry must have an inspectable target.
- `status` — `"pending" | "in-progress" | "drafted" | "complete" | "failed"`. New tasks always start as `"pending"`. See "State transitions" below.
- `log` — `null` when pending; a string describing what was done (or what went wrong) when in-progress / drafted / complete / failed.
- `verifySteps` (optional) — array of `{name, command}` objects in the same shape as the top-level array. When present, **replaces** the top-level `verifySteps` for this task's `verify --id <N>` call (the runner does not merge the two arrays). At least one step is required when the field is present; an empty array is rejected by the validator. Omit the field entirely to inherit the top-level default.

## State transitions

Status moves through a fixed state machine, enforced by `task_list_cli.py`:

```text
pending ──start/next──▶ in-progress ──draft──▶ drafted ──publish──▶ complete
                            │                      │
                            └──set-status failed───┴──▶ failed
```

| From          | To            | Verb                                  | Side effects                                                                                                                                                                                                |
| ------------- | ------------- | ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pending`     | `in-progress` | `start --id N` or `next`              | Atomic claim. `next` flips the first pending task or returns an already-in-progress one.                                                                                                                    |
| `in-progress` | `drafted`     | `draft --id N --commit-msg ...`       | Writes the log into the task; writes the commit subject to a per-task staging file at `/tmp/harness-stage-<hash>-<N>.json`. Does not touch git.                                                             |
| `drafted`     | `complete`    | `publish --id N`                      | Reads the staging file, runs `git commit -m "<staged subject>"` against the already-staged git index, then sets status. Atomic-ish: commit first, status second.                                            |
| `in-progress` | `complete`    | `set-status --id N --status complete` | No-commit completion (e.g., investigation tasks that produced no code change). The runner doesn't enforce "must publish to complete" — `set-status complete` is allowed from `in-progress` for these cases. |
| `in-progress` | `failed`      | `set-status --id N --status failed`   | Implementation gave up; no commit, no staging.                                                                                                                                                              |
| `drafted`     | `failed`      | `set-status --id N --status failed`   | Validation rejected the draft after retries. **Staging file is intentionally NOT removed** — leaves the implementer evidence to inspect.                                                                    |

`drafted` is **non-terminal**: it counts toward `status.remaining` and appears in `remaining` listings, so a `drafted` task across a runner restart is automatically picked up by the next iteration's "is anything mid-flight?" check.

The `pending → drafted` and `drafted → in-progress` transitions are not allowed. A drafted task that needs more code changes goes back through `set-status failed` (then re-planning) — the schema doesn't model "un-draft".

## Per-task `verifySteps` override

A task may declare its own `verifySteps` array to override the top-level default for that task's verification only. The top-level array remains required and serves every task that does **not** declare an override.

Resolution rule: `verify --id <N>` runs `task.verifySteps` if the task declares one, else falls back to the top-level `data.verifySteps`. **Total replacement, not a merge** — if a task overrides, the top-level steps do not run for that task.

Use this when a task's verification needs to differ from the project-wide default — e.g., a docs-only task that shouldn't pay for the test suite, or a task scoped to one file where running a project-wide static check would force every other task to fix its own files first (the verify gate is hard pass/fail, so a project-wide step blocks task 1 until tasks 2..N also pass).

Example: task 3 below replaces the top-level `tests` step with a single linkchecker run; tasks 1 and 2 (omitted, no `verifySteps` field) inherit the top-level default unchanged.

```json
{
  "id": 3,
  "title": "Update README typos",
  "what": "Fix the three broken doc links in README.md",
  "resolves": ["README.md:42"],
  "effort": "low",
  "createsNewCode": false,
  "status": "pending",
  "agentValidations": [
    "README.md no longer contains the broken URLs flagged at README.md:42"
  ],
  "log": null,
  "verifySteps": [
    { "name": "linkcheck", "command": "uv run linkchecker README.md" }
  ]
}
```

## Paired test tasks (rule)

For every task with `createsNewCode: true`, the **next** task in the array must be a test task:

- `title` starts with `"Write tests for "`.
- `createsNewCode: false`.
- `resolves: []` (it supports the preceding implementation task, not a finding).
- `effort: "low"`.
- `agentValidations` includes something like `"Test file follows project test conventions"` and `"At least N test cases covering …"` — inspection-verifiable structural facts about the test file the validation subagent confirms by reading the test file. **Don't include "Tests pass"** — the `tests` verifyStep covers that; duplicating it would tempt the validation subagent to run the suite itself, which is the duplicate-work pattern the design prevents.

Tasks with `createsNewCode: false` (annotation-only or restructuring-only) do **not** get a paired test task — they are verified by their own `agentValidations` entries.

## Path conventions

All paths inside the file (`plan`, `scope` entries, `resolves` entries) are **repo-relative**. Strip any local-machine prefix (`/Users/...`, `C:\...`) so the file is portable across machines and safe to commit.
