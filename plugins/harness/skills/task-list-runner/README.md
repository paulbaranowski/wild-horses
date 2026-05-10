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

For each `pending` / `in-progress` task in the file:

1. Dispatch a fresh foreground `Agent` with the Task Implementation Prompt.
2. The agent claims the task (`task_list_cli.py next`), implements the change, and runs `verifySteps` (typecheck, tests, etc.) via `task_list_cli.py verify` from inside its own run.
3. The agent dispatches a fresh-context validation subagent to evaluate `agentValidations` against the post-change code.
4. The agent marks the task `complete` or `failed` via `task_list_cli.py finish`, writing its report to the `log` field. The runner re-checks `status` as a corruption gate before the next iteration.

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
    Dispatch --> AgentRun

    subgraph AgentRun["Per-task Agent (fresh context)"]
      direction TB
      A1["Step 1 — cli next<br/>atomically claim →<br/>flips first pending to in-progress"]
      A1 --> A2["Implement the change"]
      A2 --> A3["Step 2 — cli verify --id N<br/>runs verifySteps in order,<br/>fail-fast, per-step log files"]
      A3 -->|exit non-zero| A2
      A3 -->|exit 0| A25["Step 2.5 — dispatch read-only<br/>Explore subagent: evaluates<br/>agentValidations against code<br/>(runtime denies Write/Edit)"]
      A25 -->|RESULT: PASS| A4Ok
      A25 -->|"RESULT: FAIL<br/>(retry once)"| A2
      A25 -->|"RESULT: FAIL twice"| A4Fail
      A4Ok["Step 3 — cli finish<br/>--status complete<br/>--log-file - via heredoc"]
      A4Fail["Step 3 — cli finish<br/>--status failed"]
      A4Ok --> A5
      A4Fail --> A5
      A5["Step 4 — git commit source files<br/>NEVER stage the task file"]
    end

    AgentRun --> PostCheck["cli status<br/>(post-iteration<br/>corruption gate)"]
    PostCheck -->|exit non-zero| Corrupt([Halt: task file corrupted])
    PostCheck -->|"remaining unchanged"| Warn[Warn: no progress this iteration]
    PostCheck -->|"remaining decreased"| Iter
    Warn --> Iter

    Final["Phase 5 — Final summary<br/>cli status + cli list →<br/>task table + plan path"]
    Final --> End([Done])
```

All mutations to the JSON go through `task_list_cli.py`. The CLI is auto-approved by the harness `PreToolUse` hook so the loop runs without per-call permission prompts; every subcommand calls `load_and_validate` first, which is what makes the corruption-gate `status` calls between iterations meaningful.

## The bundled CLI

`task_list_cli.py` is the canonical interface to the task JSON — the runner and dispatched agents never edit the file directly. Subcommands: `next`, `start`, `finish`, `get`, `list`, `status`, `remaining`, `verify`. See the "CLI reference" section of [`SKILL.md`](./SKILL.md) for the full surface.

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
