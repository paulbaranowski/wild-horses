# plan-clean

Find this repo's complete, partial, and irrelevant plans, and archive the complete ones.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

```text
/plan-clean
/plan-clean herds        # a named repo instead of the current one
/plan-clean dry run      # judge and report, write nothing
```

Also model-invoked. Trigger phrases include "clean plans", "archive complete plans", and "find plans that no longer apply".

## What it does

1. **Lists the scan set:** every `backlog`, `todo`, and `in-progress` plan for the repo. `in-review` plans and `done/` and `deferred/` stay out.
2. **Judges each plan** against the default branch, as the local ref has it. Each plan gets one verdict (complete, partial, irrelevant, or open) and one evidence line.
3. **Archives the complete plans** to `done/` through the CLI. A paired task-list `.json` moves with its plan.
4. **Prints four lists:** complete (with archived paths), partial, irrelevant, and open.

It never moves a partial or irrelevant plan, never edits a plan body, and never fetches from origin.

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
