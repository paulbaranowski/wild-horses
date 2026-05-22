# task-list-builder

Build or rewrite a paired `.json` + `.md` task list in the harness task-list-schema format.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

```text
/harness:task-list-builder [free-form description | path to .md report | path to .json task file (rewrite) | empty for conversation context]
```

Pairs with [`task-list-runner`](../task-list-runner/), which consumes the JSON the builder produces.

## What it does

Turns one of these inputs into a paired task list under `docs/exec-plans/active/`:

- a free-form description of work
- a `/harness:reasoning-gaps` or `/harness:feedback-blockers` report (`.md`)
- an existing `.json` task file (rewrite-in-place)
- recent conversation context (when no argument is given)

Output is always two files: a `.<slug>.json` (machine-readable, the runner's input) and a paired `.<slug>.md` (human-readable summary). The default slug is `task-list-builder`; callers can override it with `--slug <name>` to preserve provenance (e.g., `/harness:feedback-blockers` passes `--slug feedback-blockers` so its output files are clearly distinguishable).

## How it works

```mermaid
flowchart TD
    Start([User invokes<br/>/harness:task-list-builder])

    subgraph Inputs["Input sources (Phase 1.B)"]
      direction TB
      I1[Free-form description]
      I2[".md report from<br/>reasoning-gaps or<br/>feedback-blockers"]
      I3[".json task file<br/>(rewrite-in-place)"]
      I4["Recent conversation<br/>(no argument)"]
    end

    Start --> P0
    P0["Phase 0 — Parse meta-flags<br/>--slug, --md-body-from-context"]
    P0 --> Inputs
    Inputs --> P1A
    P1A{"Phase 1.A — Output target?"}
    P1A -->|".json path or<br/>'rewrite' phrasing"| Rewrite["Rewrite mode:<br/>reuse existing path,<br/>preserve verifySteps + scope"]
    P1A -->|otherwise| Fresh["Fresh build:<br/>compute new run-id path"]
    Rewrite --> P2
    Fresh --> P2

    P2["Phase 2 — Discover verifySteps<br/>typecheck + tests, sourced from<br/>CLAUDE.md / package.json /<br/>pyproject.toml"]
    P2 --> P3["Phase 3 — Compute paths<br/>docs/exec-plans/active/<br/>&lt;date&gt;-&lt;run-id&gt;-&lt;desc&gt;.&lt;slug&gt;.{json,md}"]
    P3 --> P4["Phase 4 — Build tasks<br/>sequential ids · paired test task<br/>after every createsNewCode:true<br/>(new code OR behavior change) ·<br/>agentValidations · per-task<br/>verifySteps when report supplies them"]
    P4 --> P5{"Phase 5 — Preview<br/>to user"}
    P5 -->|cancel| Stop([Stop, no files written])
    P5 -->|edit| P4
    P5 -->|yes| P6
    P6["Phase 6 — Write files<br/>JSON: always written<br/>MD: written unless rewrite-mode<br/>+ MD already exists"]
    P6 --> Output

    subgraph Output["Output: docs/exec-plans/active/"]
      direction TB
      O1[".&lt;slug&gt;.json<br/>canonical artifact —<br/>consumed by task-list-runner"]
      O2[".&lt;slug&gt;.md<br/>human-readable summary —<br/>preserved on rewrite"]
    end

    Output --> P7["Phase 7 — Hand off<br/>not staged · not committed"]
    P7 --> Runner([Ready for<br/>/harness:task-list-runner])
```

## Schema

The JSON schema is defined once, in [`../../task-list-schema.md`](../../task-list-schema.md). Both `task-list-builder` and `task-list-runner` read from that file rather than duplicating it.

See [`example.json`](./example.json) for a minimal valid task file.

## Files in this directory

| File           | Purpose                                                     |
| -------------- | ----------------------------------------------------------- |
| `SKILL.md`     | Instructions Claude executes when the skill is invoked      |
| `example.json` | Reference task file used by `SKILL.md` to anchor the schema |

## Install

The skill ships with the `harness` plugin:

```text
/plugin install harness@wild-horses
```
