# Task List Schema

The harness loop produces and consumes a `.json` task file in `docs/exec-plans/active/`. **This document is the source of truth for that file's shape.**

Referenced by:

- `loop-protocol.md` — the Phase 4 options menu shared by `/harness:feedback-blockers` and `/harness:reasoning-gaps` writes files in this shape.
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
      "acceptanceCriteria": [
        "<concrete, verifiable criterion>",
        "<another criterion>",
        "Tests pass"
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
      "acceptanceCriteria": [
        "Test file follows project test conventions",
        "At least N test cases covering happy path, errors, and edge cases",
        "Tests pass"
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
- `verifySteps` — array of `{name, command}` objects, each describing one verification step the per-task Agent runs after implementing a task. Steps run in order; on first failure the Agent stops and reports which step (`name`) failed. **At least one step is required.** Conventional names: `typecheck`, `tests`, `lint` — but any non-empty string is valid. Discovered once during plan creation and reused every iteration.
- `scope` — repo-relative file paths preserved for potential re-analysis. Use paths relative to the repository root to avoid leaking local machine structure if the file is committed.
- `tasks` — array of task objects (see below).

## Task fields

- `id` — integer, unique within the file. Conventionally sequential `1, 2, 3, ...` with no gaps.
- `title` — short human-readable name.
- `what` — specific change: files to modify, structures to create, patterns to fix.
- `resolves` — array of `file:line` strings linking the task back to the findings it addresses. Repo-relative paths only.
- `effort` — `"low" | "medium" | "high"`.
- `createsNewCode` — `true` if the intervention creates new callable code (functions, classes, methods, services, models, protocols), `false` if it only restructures, annotates, or documents existing code. **Determines whether a paired test task is generated** (see "Paired test tasks" below).
- `acceptanceCriteria` — array of concrete, verifiable criteria derived from the task's `what` and `resolves`. Most tasks should include `"Tests pass"`. Avoid vague criteria like "looks good" or "code is clean".
- `status` — `"pending" | "in-progress" | "complete" | "failed"`. New tasks always start as `"pending"`.
- `log` — `null` when pending; a string describing what was done (or what went wrong) when in-progress / complete / failed.

## Paired test tasks (rule)

For every task with `createsNewCode: true`, the **next** task in the array must be a test task:

- `title` starts with `"Write tests for "`.
- `createsNewCode: false`.
- `resolves: []` (it supports the preceding implementation task, not a finding).
- `effort: "low"`.
- `acceptanceCriteria` includes something like `"Test file follows project test conventions"` and `"Tests pass"`.

Tasks with `createsNewCode: false` (annotation-only or restructuring-only) do **not** get a paired test task — they are verified by their own acceptance criteria.

## Path conventions

All paths inside the file (`plan`, `scope` entries, `resolves` entries) are **repo-relative**. Strip any local-machine prefix (`/Users/...`, `C:\...`) so the file is portable across machines and safe to commit.
