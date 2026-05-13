# task-list-runner

Drive a harness task list (a JSON file in the task-list-schema format) to completion by dispatching each task to a foreground `Agent` call, one at a time.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

```text
/harness:task-list-runner [path to .json or .md task file] [--all | --next]
```

With no path, the runner auto-locates an in-progress task file under `docs/exec-plans/active/`.

Pairs with [`task-list-builder`](../task-list-builder/), which produces the JSON the runner consumes.

## What it does

For each `pending` / `in-progress` task in the file, the runner dispatches **two foreground `Agent` calls** at depth-1 (never nested — the runtime forbids depth-2 dispatch):

1. **Implementation agent.** Claims the task (`task_list_cli.py next`), implements the change, runs `verifySteps` via `task_list_cli.py verify`, stages source files via `git add`, and calls `task_list_cli.py draft` (parks the commit subject in a `/tmp` staging file without touching git). Returns.
2. **Validation agent.** Fresh-context `Explore` subagent (read-only — runtime denies `Write`/`Edit`) that evaluates `agentValidations` against the staged code. Returns `RESULT: PASS` or `RESULT: FAIL`.

The runner then resolves the draft:

- **PASS** → `task_list_cli.py publish` (runs `git commit` against the staged index using the parked subject, flips status to `complete`).
- **FAIL** → `task_list_cli.py set-status --status failed` (no commit, staging file preserved for inspection).

The runner re-runs `task_list_cli.py status` as a corruption gate between every iteration.

Modes: `--all` (run every remaining task non-interactively), `--next` (one task then stop), no flag (interactive menu).

## How it works

```mermaid
flowchart TD
    Start([User invokes<br/>/harness:task-list-runner])
    Start --> P1["Phase 1 — Parse args<br/>path? mode --all/--next/none?"]
    P1 --> P2{Path given?}
    P2 -->|".md"| MdResolve["Read frontmatter task_file →<br/>resolves to .json"]
    P2 -->|".json"| P3
    P2 -->|no| AutoLoc["Phase 2 — Auto-locate<br/>scan docs/exec-plans/active/*.json,<br/>then *.md fallback;<br/>cli status validates each candidate"]
    MdResolve --> P3
    AutoLoc -->|exactly one valid| P3
    AutoLoc -->|multiple| Ask[Ask user to pick]
    AutoLoc -->|none| Halt([Stop — no in-progress<br/>task files found])
    Ask --> P3

    P3["Phase 3 — Show summary<br/>cli status + cli remaining"]
    P3 --> Mode{Mode?}
    Mode -->|--all| InitAll["Compute MAX_ITER =<br/>remaining × 1.5 + 1"]
    Mode -->|--next| Dispatch
    Mode -->|interactive| Menu["Menu:<br/>1. Run all remaining<br/>2. Run next task only"]
    Menu --> Mode
    InitAll --> Iter

    Iter["cli status<br/>(corruption gate +<br/>prev_remaining)"]
    Iter --> Done{remaining == 0?}
    Done -->|yes| Final
    Done -->|no| MaxCheck{"MAX_ITER<br/>reached?"}
    MaxCheck -->|yes| Final
    MaxCheck -->|no| Dispatch

    Dispatch["Dispatch ONE foreground Agent<br/>with the Task Implementation Prompt<br/>(sequential, never parallel)"]
    Dispatch --> ImplAgent

    subgraph ImplAgent["Implementation Agent (depth-1, fresh context)"]
      direction TB
      A1["Step 1 — cli next<br/>atomically claim →<br/>flips first pending to in-progress"]
      A1 --> A2["Implement the change"]
      A2 --> A3["Step 2 — cli verify --id N<br/>runs verifySteps in order,<br/>fail-fast, per-step log files"]
      A3 -->|exit non-zero| A2
      A3 -->|"cannot fix"| AFail["cli set-status<br/>--status failed"]
      A3 -->|exit 0| A4["Step 3 — git add source files<br/>NEVER stage the task file"]
      A4 --> A5["Step 4 — cli draft --id N<br/>--commit-msg subject<br/>--log-file - via heredoc<br/>(parks subject; does not touch git)"]
    end

    ImplAgent -->|"task drafted"| ValidationDispatch["Runner inspects status:<br/>drafted task present?"]
    ImplAgent -->|"task failed"| PostCheck
    ValidationDispatch -->|yes| ValAgent
    ValidationDispatch -->|no, no progress| PostCheck

    subgraph ValAgent["Validation Agent (depth-1, fresh context, read-only)"]
      direction TB
      V1["Dispatched by runner with:<br/>what + agentValidations + changedFiles<br/>(git diff --cached --name-only)"]
      V1 --> V2["Evaluate each agentValidations entry<br/>by reading code (Read/Grep)<br/>subagent_type: Explore<br/>(runtime denies Write/Edit/NotebookEdit)"]
      V2 --> V3["Print per-entry PASS/FAIL with<br/>file:line evidence"]
      V3 --> V4["Final line:<br/>RESULT: PASS or RESULT: FAIL"]
    end

    ValAgent -->|RESULT: PASS| Publish["cli publish --id N<br/>git commit against staged index<br/>using parked subject<br/>flips status → complete<br/>(exit 15 on git failure;<br/>task stays drafted)"]
    ValAgent -->|RESULT: FAIL| FailDraft["cli set-status --id N<br/>--status failed<br/>(staging file preserved for inspection)<br/>NO retry — schema forbids drafted → in-progress"]
    Publish --> PostCheck
    FailDraft --> PostCheck

    PostCheck["cli status<br/>(post-iteration<br/>corruption gate)"]
    PostCheck -->|exit non-zero| Corrupt([Halt: task file corrupted])
    PostCheck -->|"remaining unchanged"| Warn[Warn: no progress this iteration]
    PostCheck -->|"remaining decreased"| Iter
    Warn --> Iter

    Final["Phase 5 — Final summary<br/>cli status + cli list →<br/>task table + plan path"]
    Final --> End([Done])
```

All mutations to the JSON go through `task_list_cli.py`. The CLI is auto-approved by the harness `PreToolUse` hook so the loop runs without per-call permission prompts; every subcommand calls `load_and_validate` first, which is what makes the corruption-gate `status` calls between iterations meaningful.

## The bundled CLI

`task_list_cli.py` is the canonical interface to the task JSON — the runner and dispatched agents never edit the file directly. Subcommands: `next`, `start`, `draft`, `publish`, `set-status`, `get`, `list`, `status`, `remaining`, `verify`. See the "CLI reference" section of [`SKILL.md`](./SKILL.md) for the full surface.

The harness plugin's PreToolUse hook auto-approves invocations of this CLI so the loop runs without per-call permission prompts.

## Schema

The JSON schema is defined once, in [`../../task-list-schema.md`](../../task-list-schema.md). Both `task-list-runner` and `task-list-builder` read from that file rather than duplicating it.

## Files in this directory

| File                    | Purpose                                                     |
| ----------------------- | ----------------------------------------------------------- |
| `SKILL.md`              | Instructions Claude executes when the skill is invoked      |
| `task_list_cli.py`      | Bundled CLI — the only sanctioned mutator for the task JSON |
| `test_task_list_cli.py` | Pytest suite for the CLI                                    |

Run the tests from the repo root:

```text
uv run pytest plugins/harness/skills/task-list-runner/test_task_list_cli.py
```

## Install

The skill ships with the `harness` plugin:

```text
/plugin install harness@wild-horses
```
