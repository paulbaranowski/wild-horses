# plan-update

Edit a plan's frontmatter (status, assigned agent, and other fields) via `plan_keeper_cli.py file-meta set`.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo. The targeted single-plan editor - for bulk/cross-repo promotion, use [`plan-crew`](../plan-crew/) instead.

## Invoke

```text
/plan-update
```

Also model-invoked - trigger phrases include "change the agent on a plan", "promote a plan to todo", "set a plan's status", "edit plan frontmatter".

## What it does

1. **Identifies the plan** - a plan already referenced in conversation, or a numbered pick from a fresh `list` call.
2. **Identifies the field(s) to change** from the user's phrasing: `Status` (promote/reset), `Agent` (change model), `Kind` (reclassify), or a ticket-system id field.
3. **Confirms** the exact old → new change per field before writing anything.
4. **Runs the update** via `file-meta set` with one flag per field (multiple fields can land in a single call).
5. **Confirms the result** using the path the CLI printed - which can differ from the input, since `--status done`/`--status deferred` relocates the file into `done/`/`deferred/`, and `--kind` renames the file's kind segment to match.

Setting `done`/`deferred` moves the file (same as `plan-done`'s job) - this skill still supports it for editing other fields in the same breath, but `plan-done` is the preferred path for simply completing a plan.

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
