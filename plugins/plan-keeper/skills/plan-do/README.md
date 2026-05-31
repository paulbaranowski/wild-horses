# plan-do

Pick up a saved plan from `~/plans/<repo>/` and route it to the right next step in the planning pipeline.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

This skill is model-invoked by description — no slash command. Trigger phrases include:

```text
"do a plan"
"pick a plan to work on"
"resume a plan"
"implement one of my saved plans"
"do a plan from herds"               # lists ~/plans/herds/
"pick a plan from general"           # lists ~/plans/general/
```

Pairs with [`plan-save`](../plan-save/) (which wrote the files this skill reads) and [`plan-done`](../plan-done/) (which archives them once finished).

## What it does

```text
idea  ──► superpowers:brainstorming    ──► spec
spec  ──► superpowers:writing-plans    ──► impl plan
execution-ready ──► menu (recommended first):
        ├──► harness:autonomous                              (AFK, no human, ──► PR)
        ├──► harness:task-list-builder ──► task-list-runner  (dispatched tasks)
        └──► superpowers:executing-plans                     (sequential, review-gated)
```

1. **Lists not-yet-started plans** via `plan_keeper_cli.py list --status todo,backlog` — only `todo` and `backlog` plans (you're picking something to _start_), tagged with their status, newest-first within each group. Already-started plans (`in-progress`, `in-review`) are hidden, with a count on stderr.
2. **Asks which one.** Displays the list and waits. Reads only the picked plan — never reads multiple candidates ahead of time (wastes context, biases classification).
3. **Classifies readiness (tier 1)** as **idea**, **spec**, or **execution-ready**.
4. **For idea / spec** → suggests the single next pipeline stage (`brainstorming` / `writing-plans`) and confirms.
5. **For execution-ready** → offers **all three execution engines at once**, recommended first. The recommendation comes from the plan's _shape_ (tier 2); the user picks how hands-off to be. All options are always listed, plus a manual escape hatch.
6. **Marks the plan `in-progress`** (`file-meta update --field Status=in-progress`) when it hands off to any skill — not on manual-steer — so it leaves this list and enters `plan-done`'s finish list. **Then hands off** via the `Skill` tool. The plan content is already in conversation context, so no explicit payload is needed. For `harness:autonomous`, the in-context plan _is_ the Task — no issue URL or `Ticket:` lookup.

## Classification cheatsheet

**Tier 1 — readiness (which path).** `plan-do` reads the plan's `Kind:` frontmatter first — `plan-save` sets it, so it's authoritative (`idea` → idea, `prd`/`design`/`spec` → spec, `exec-plan` → execution-ready; see [`../../plan-kinds.md`](../../plan-kinds.md)). When `Kind` is absent (old or hand-made files), it infers from content using these signals:

| Readiness           | Signals                                                                                            |
| ------------------- | -------------------------------------------------------------------------------------------------- |
| **idea**            | Short, exploratory tone, no clear structure; "what if" / "could we" / "maybe" language             |
| **spec**            | `## Design` / `## Architecture` / `## Requirements` sections; describes WHAT, not step-by-step HOW |
| **execution-ready** | Describes HOW — concrete steps, phases, or tasks with enough detail to start building              |

**Tier 2 — shape (which engine to recommend first, for execution-ready plans):**

| Recommend                          | Signals                                                                                                       |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| **`harness:autonomous`**           | Self-contained, well-specified, bounded; reads like a single ticket that ends in a PR; no mid-flight judgment |
| **`task-list-builder` → `runner`** | Independent tasks, per-task acceptance criteria, dependency notation, parallel/dispatch language, large scope |
| **`executing-plans`**              | Sequential phases with review/checkpoint language; dependent linear flow; risky work to review phase-by-phase |

Tiebreaker: autonomy-readiness first, then independence. `plan-do`'s only write to `~/plans/` is flipping the started plan's `Status` to `in-progress`; it never moves or deletes. Routing decisions always go through a user confirmation gate.

## Empty-list behavior

If no `todo`/`backlog` plans show but the stderr note reports active plans, everything is already in progress — `plan-do` says so and offers to list all of them. If there are no active plans at all, it runs `plan_keeper_cli.py list-repos` (one repo per line with active/done/deferred counts) and asks the user to pick another repo or steer manually. It does **not** silently fall back to a different folder.

## Repo derivation

`<repo>` auto-derives from `git remote get-url origin` (with a `basename $PWD` fallback). Override with phrases like "do a plan from `<name>`" or "pick a plan from `<name>`". See [`../../repo-derivation.md`](../../repo-derivation.md).

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
