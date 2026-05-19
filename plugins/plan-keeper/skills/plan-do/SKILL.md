---
name: plan-do
description: Use when the user asks to work on a saved plan, do a plan, implement a plan, execute a plan, or pick up a plan to work on. Lists plans for the current repo from ~/plans/<repo>/, lets the user pick one, classifies the plan as idea / spec / sequential implementation plan / task-list-shaped, and suggests the matching next skill (superpowers:brainstorming, superpowers:writing-plans, superpowers:executing-plans, or harness:task-list-builder).
---

# plan-do

Pick up a saved plan from `~/plans/<repo>/` and route it to the right next step in the planning pipeline.

The skill is the entry point that joins this pipeline at the right stage:

```
idea ──► brainstorming ──► spec ──► writing-plans ──► implementation plan ──► executing-plans
                                                                          └──► task-list-builder ──► task-list-runner
```

## Procedure

Follow these steps in order. Do not skip steps.

### 1. Determine `<repo>`

Use the same logic as `plan-save`:

**First, check the user's invocation for an explicit override.** Patterns:

- "do a plan from `<name>`"
- "plan-do `<name>`"
- "pick a plan from `<name>`"
- "in the `<name>` folder/bucket"

If an override is present, normalize `<name>` lightly: lowercase, replace whitespace with `-`, and otherwise preserve as-is. **Underscores and existing hyphens are preserved** so repo names like `herds_mobile_app` and `temporal_cloak` round-trip exactly.

**Otherwise, auto-derive — use the result verbatim, do NOT slugify:**

1. Run `git remote get-url origin 2>/dev/null`. If it succeeds, take `basename "$URL" .git`. Use the result as-is.
2. If no git remote, fall back to `basename "$PWD"`, also verbatim.

### 2. List the plans

Run `ls -1 ~/plans/<repo>/*.md 2>/dev/null | sort -r` to list plans newest-first (the `YYYY-MM-DD-` filename prefix sorts naturally).

**If the directory doesn't exist or is empty:**

- Tell the user there are no plans for this repo.
- Offer alternatives: list plans in `~/plans/general/` if it exists, and/or list which other repos under `~/plans/` have plans (`ls -d ~/plans/*/`).
- Wait for the user to decide before continuing.

**If plans exist**, display them as a numbered list with filenames only. Do not read or classify any files yet — classification only happens on the picked plan.

Example output:

```
Plans in ~/plans/wild-horses/:

  1. 2026-05-19-plan-do-design.md
  2. 2026-05-19-plan-save-design.md
  3. 2026-05-17-task-list-runner-refactor.md
  4. 2026-05-15-harness-namespace-cleanup.md

Which one?
```

### 3. User picks a plan

The user replies with a number or a filename fragment. Resolve to a single file. If ambiguous (e.g., a fragment matches multiple), ask the user to disambiguate.

### 4. Read the picked plan

Use the `Read` tool on the full file. The content stays in conversation context for the rest of this skill and for whatever skill is invoked next.

### 5. Classify the plan

Classify the plan as one of four types using the signals below. The model should make a judgment call from reading the file — these are heuristics, not exact-match rules.

| Type                               | Signals                                                                                                                                                                                                                                                   |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **idea**                           | Short (~< 50 lines), exploratory tone, no clear structure, no numbered execution steps. Language like "what if", "thinking about", "could we", "maybe". No `## Design` / `## Architecture` sections.                                                      |
| **spec**                           | Has sections like `## Design`, `## Architecture`, `## Requirements`, `## Components`, `## Goals/Non-goals`, `## Trade-offs`, `## Data model`. Describes WHAT, not step-by-step HOW. Reads like a design doc.                                              |
| **sequential implementation plan** | Numbered phases or steps with explicit review/checkpoint language. Linear flow ("first do X, then do Y"). Mentions TDD cycles, review gates, or "after each phase". Single-thread feel.                                                                   |
| **task-list-shaped plan**          | Explicit independent tasks (Task A / Task B / ... or numbered task IDs). Per-task acceptance criteria. Dependency notation between tasks. Language about "dispatch", "subagents", "in parallel", "independent". Explicit mention of harness or task-list. |

**If the plan is ambiguous between types** (e.g., signals for both sequential and task-list-shaped), present the call to the user rather than guessing silently.

**If the plan doesn't fit any of the four types** (e.g., it's a research note, a meeting log, a list of TODOs), say so and offer to let the user steer manually.

### 6. Suggest the matching next skill

Map type → suggested skill:

| Plan type                      | Suggested skill               | One-line purpose                                                                                      |
| ------------------------------ | ----------------------------- | ----------------------------------------------------------------------------------------------------- |
| idea                           | `superpowers:brainstorming`   | Turn the idea into a reviewed spec                                                                    |
| spec                           | `superpowers:writing-plans`   | Turn the spec into a phased implementation plan                                                       |
| sequential implementation plan | `superpowers:executing-plans` | Execute the plan with human review at each phase                                                      |
| task-list-shaped plan          | `harness:task-list-builder`   | Convert the plan into a structured JSON task-list, then dispatch tasks via `harness:task-list-runner` |

### 7. Confirm before invoking

Tell the user what you read, what you'll do, and give them a chance to redirect:

> I read `<filename>` as a **<type>**. Suggested next: `<plugin:skill>` to <one-line purpose>. Proceed? (Or pick a different skill / steer manually.)

For implementation plans where signals are mixed, mention the alternative:

> I read this as an implementation plan. Suggesting `superpowers:executing-plans` (sequential, human review at each phase). If you'd rather dispatch tasks in parallel, say so and I'll route to `harness:task-list-builder` instead.

Wait for the user's response. Do not auto-invoke without confirmation.

### 8. Invoke the chosen skill

On confirmation, use the `Skill` tool to invoke the chosen skill. The plan content is already in conversation context from step 4, so the invoked skill has full access — no explicit handoff payload is needed.

If the user picked a different skill than the suggestion, invoke that one instead.

If the user wants to steer manually, just stop the skill here. The plan is read into context and they can drive freely.

## Edge cases

- **No plans for the current repo** — list alternatives (`~/plans/general/`, other repos with plans). Do not silently fall back.
- **`~/plans/` doesn't exist at all** — tell the user `plan-save` hasn't been used yet on this machine.
- **Plan is none of the four types** — say so explicitly; offer to read into context and let the user steer.
- **Filename fragment matches multiple plans** — ask the user to disambiguate; do not pick one arbitrarily.

## Notes

- This skill is read-only against `~/plans/` — it never modifies, moves, or deletes plans. Sibling skill `plan-done` is responsible for archiving completed plans.
- The classification distinguishes _sequential_ implementation plans (linear, review-gated) from _task-list-shaped_ plans (parallel, dispatched). Same vocabulary ("phase", "task") can appear in both — the discriminating signal is task **independence**, not the words used.
- The override in step 1 also serves as an escape hatch: if `git remote get-url origin` returns a name that doesn't have a `~/plans/` folder, the user can name the destination explicitly.
