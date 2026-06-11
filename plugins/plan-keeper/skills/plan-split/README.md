# plan-split

Decompose one plan into several independently-grabbable child plans — thin vertical "tracer bullet" slices — saved into `~/plans/<repo>/`, wired with native `Blocked-by:` dependencies and staged at `todo` (queue them via `plan-crew` to stamp each one's `Agent:` tag, then groundcrew dispatches them in dependency order).

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

This skill is model-invoked by description — no slash command. Trigger phrases include:

```text
"split this plan into slices"
"break the plan into independently-grabbable pieces"
"decompose this spec into implementation tasks"
"turn this plan into tracer bullets for groundcrew"
"split the herds plan"               # decomposes a plan in ~/plans/herds/
```

Pairs with [`plan-save`](../plan-save/) (which writes the source plan and each slice) and [`plan-crew`](../plan-crew/) (which manages the resulting groundcrew queue).

## What it does

1. **Resolves the source plan.** A saved file in `~/plans/<repo>/` (named by path, topic, or "the plan I just saved"), or the current conversation's plan. If it's a saved file, captures its `Plan-keeper Ticket` for the slices' `Source:` back-reference.
2. **Drafts vertical slices.** Thin tracer bullets, each a complete path through every layer, each marked HITL or AFK.
3. **Quizzes the user.** Presents the numbered breakdown (Title / Type / Blocked by) and iterates until approved — nothing is written before approval.
4. **Publishes the slices** in dependency order: `save --kind exec-plan` (mints a frozen ticket), `file-meta set --blocked-by` to wire prerequisites, `file-meta set --status todo` to stage every slice. (Staging at `todo` is not enough to dispatch — groundcrew also needs an `Agent:` tag, which `plan-crew` stamps when you queue the slices.)
5. **Marks the source plan done** when it was a saved file — its deliverable (the slices) has been produced.

## Dependencies, the native way

Slices use plan-keeper's `Blocked-by:` frontmatter field (v5.7.0+). Once the slices are queued via `plan-crew` (which stamps each one's `Agent:` tag), groundcrew holds a `todo` slice until **every** prerequisite is `done`, then auto-dispatches on the next fetch — so the whole wave can be queued at once and still runs in the right order, with no human promoting each step. See [`../../groundcrew/README.md`](../../groundcrew/README.md) "Dependencies between plans".

`Source:` (provenance — the plan a slice was carved from) is kept distinct from `Blocked-by:` (a prerequisite): the source is not a dependency.

## Why a plans-tree decomposition (not an issue tracker)

plan-keeper is the system of record for tasks. `plan-split` is a **planning task that spawns implementation tasks** — it keeps the decomposed work as first-class plans in `~/plans/<repo>/` rather than filing it to an external tracker. Exporting any slice to Linear/Jira remains the occasional [`plan-linear`](../plan-linear/) / [`plan-jira`](../plan-jira/) path.

## Repo derivation

`<repo>` auto-derives from `git remote get-url origin` (with a `basename $PWD` fallback). Override with phrases like "split the `<name>` plan". See [`../../repo-derivation.md`](../../repo-derivation.md).

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
