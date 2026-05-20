---
name: plan-do
description: Use when the user asks to work on a saved plan, do a plan, implement a plan, execute a plan, pick up a plan to work on, or resume a plan from disk.
---

# plan-do

Pick up a saved plan from `~/plans/<repo>/` and route it to the right next step in the planning pipeline. The bundled `plan_keeper_cli.py` handles listing (repo derivation, newest-first sort, empty-state fallback); this skill classifies the picked plan and routes to the matching next skill.

The skill is the entry point that joins this pipeline at the right stage:

```text
idea ──► brainstorming ──► spec ──► writing-plans ──► implementation plan ──► executing-plans
                                                                          └──► task-list-builder ──► task-list-runner
```

## Quick reference

- **Reads:** `~/plans/<repo>/*.md` (newest first by filename); never writes.
- **`<repo>`:** auto-derived or override — see [../../repo-derivation.md](../../repo-derivation.md).
- **Classification:** idea / spec / sequential impl plan / task-list-shaped.
- **Routing:** `superpowers:brainstorming` (idea), `superpowers:writing-plans` (spec), `superpowers:executing-plans` (sequential), `harness:task-list-builder` (task-list).
- **Confirmation:** required before reading any plan file and before invoking any next skill.

## Procedure

Follow these steps in order. Do not skip steps.

### 1. List the plans

First, check the user's invocation for a repo override. Recognize:

- "do a plan from `<name>`"
- "plan-do `<name>`"
- "pick a plan from `<name>`"
- "in the `<name>` folder/bucket"

Then invoke the CLI:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list
```

Add `--override <name>` if you found one. The CLI handles repo derivation, the `*.md` glob, and newest-first sort. Output is one filename per line (no leading numbering).

**If the output is empty**, the current repo has no active plans. Tell the user and list alternatives by running:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list-repos
```

Output is one repo per line with state counts (e.g., `herds: active=15 done=22 deferred=2`). Wait for the user to pick a different repo (re-run step 1 with `--override`) or steer manually.

**If the output has lines**, display them as a numbered list to the user and ask which one. Do not read or classify any files yet — classification only happens on the picked plan.

Example output to the user:

```text
Plans in ~/plans/wild-horses/:

  1. 2026-05-19-plan-do-design.md
  2. 2026-05-19-plan-save-design.md
  3. 2026-05-17-task-list-runner-refactor.md
  4. 2026-05-15-harness-namespace-cleanup.md

Which one?
```

### 2. User picks a plan

The user replies with a number or a filename fragment. Resolve to a single filename from the CLI's output. If ambiguous (a fragment matches multiple), ask the user to disambiguate.

### 3. Read the picked plan

Use the `Read` tool on `~/plans/<repo>/<filename>` (the full path is the repo dir from step 1 plus the picked filename). The content stays in conversation context for the rest of this skill and for whatever skill is invoked next.

### 4. Classify the plan

Classify the plan as one of four types using the signals below. The model should make a judgment call from reading the file — these are heuristics, not exact-match rules.

| Type                               | Signals                                                                                                                                                                                                                                                   |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **idea**                           | Short (~< 50 lines), exploratory tone, no clear structure, no numbered execution steps. Language like "what if", "thinking about", "could we", "maybe". No `## Design` / `## Architecture` sections.                                                      |
| **spec**                           | Has sections like `## Design`, `## Architecture`, `## Requirements`, `## Components`, `## Goals/Non-goals`, `## Trade-offs`, `## Data model`. Describes WHAT, not step-by-step HOW. Reads like a design doc.                                              |
| **sequential implementation plan** | Numbered phases or steps with explicit review/checkpoint language. Linear flow ("first do X, then do Y"). Mentions TDD cycles, review gates, or "after each phase". Single-thread feel.                                                                   |
| **task-list-shaped plan**          | Explicit independent tasks (Task A / Task B / ... or numbered task IDs). Per-task acceptance criteria. Dependency notation between tasks. Language about "dispatch", "subagents", "in parallel", "independent". Explicit mention of harness or task-list. |

**If the plan is ambiguous between types** (e.g., signals for both sequential and task-list-shaped), present the call to the user rather than guessing silently.

**If the plan doesn't fit any of the four types** (e.g., it's a research note, a meeting log, a list of TODOs), say so and offer to let the user steer manually.

### 5. Suggest the matching next skill

Map type → suggested skill:

| Plan type                      | Suggested skill               | One-line purpose                                                                                      |
| ------------------------------ | ----------------------------- | ----------------------------------------------------------------------------------------------------- |
| idea                           | `superpowers:brainstorming`   | Turn the idea into a reviewed spec                                                                    |
| spec                           | `superpowers:writing-plans`   | Turn the spec into a phased implementation plan                                                       |
| sequential implementation plan | `superpowers:executing-plans` | Execute the plan with human review at each phase                                                      |
| task-list-shaped plan          | `harness:task-list-builder`   | Convert the plan into a structured JSON task-list, then dispatch tasks via `harness:task-list-runner` |

### 6. Confirm before invoking

Tell the user what you read, what you'll do, and give them a chance to redirect:

> I read `<filename>` as a **<type>**. Suggested next: `<plugin:skill>` to <one-line purpose>. Proceed? (Or pick a different skill / steer manually.)

For implementation plans where signals are mixed, mention the alternative:

> I read this as an implementation plan. Suggesting `superpowers:executing-plans` (sequential, human review at each phase). If you'd rather dispatch tasks in parallel, say so and I'll route to `harness:task-list-builder` instead.

Wait for the user's response. Do not auto-invoke without confirmation.

### 7. Invoke the chosen skill

On confirmation, use the `Skill` tool to invoke the chosen skill. The plan content is already in conversation context from step 3, so the invoked skill has full access — no explicit handoff payload is needed.

If the user picked a different skill than the suggestion, invoke that one instead.

If the user wants to steer manually, just stop the skill here. The plan is read into context and they can drive freely.

## Common mistakes

- **Reading and classifying multiple plans before the user picks.** Step 1 lists filenames only. Reading multiple plans wastes context and biases classification toward whatever was read last.
- **Auto-invoking the next skill without confirmation.** Step 6 requires a check-in even when the classification feels obvious. The skill's job is to _suggest_ the next stage, not jump to it.
- **Conflating sequential vs task-list-shaped plans.** Both use "phases" and "tasks" in their vocabulary. The discriminator is **independence** of work units, not the words used.
- **Silently falling back when the current repo has no plans.** Step 1 says: tell the user, run `list-repos`, wait for direction. Don't auto-route to another folder.

## Edge cases

- **No plans for the current repo** — show `list-repos` output to the user and let them pick another repo. Do not silently fall back.
- **`~/plans/` doesn't exist at all** — `list-repos` returns empty. Tell the user `plan-save` hasn't been used yet on this machine.
- **Plan is none of the four types** — say so explicitly; offer to read into context and let the user steer.
- **Filename fragment matches multiple plans** — ask the user to disambiguate; do not pick one arbitrarily.

## Notes

- This skill is read-only against `~/plans/` — it never modifies, moves, or deletes plans. Sibling skill `plan-done` is responsible for archiving completed plans.
- The classification distinguishes _sequential_ implementation plans (linear, review-gated) from _task-list-shaped_ plans (parallel, dispatched). Same vocabulary ("phase", "task") can appear in both — the discriminating signal is task **independence**, not the words used.
- Sibling skills in the `plan-` family (`plan-save`, `plan-done`) share the same CLI and the same `~/plans/<repo>/` tree.
