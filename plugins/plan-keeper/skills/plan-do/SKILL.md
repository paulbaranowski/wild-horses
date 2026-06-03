---
name: plan-do
description: Use when the user asks to work on a saved plan, do a plan, implement a plan, execute a plan, pick up a plan to work on, or resume a plan from disk.
---

# plan-do

Pick up a saved plan from `~/plans/<repo>/` and route it to the right next step in the planning pipeline. The bundled `plan_keeper_cli.py` handles listing (repo derivation, newest-first sort, empty-state fallback); this skill classifies the picked plan and routes to the matching next skill.

The skill is the entry point that joins this pipeline at the right stage:

```text
idea ──► brainstorming ──► spec ──► writing-plans ──► implementation plan ──┬──► executing-plans   (sequential, review-gated)
                                                                            ├──► task-list-builder ──► task-list-runner   (dispatched tasks)
                                                                            └──► autonomous   (AFK, no human, ──► PR)
```

For plans that aren't execution-ready yet (idea, spec), the skill suggests the single next pipeline stage. For execution-ready plans, it offers **all three execution engines at once** — recommended first — and the user picks how hands-off they want to be.

## Quick reference

- **Lists:** the **not-yet-started** plans only — `Status: todo` and `Status: backlog` (`list --status todo,backlog`). In-progress / in-review / done plans are excluded (you're picking something to _start_).
- **Writes:** one frontmatter update when it starts a plan (step 6) — flips `Status` to `in-progress` and clears the `Agent` tag (so groundcrew won't claim a plan you're driving). It never moves, deletes, or rewrites the body.
- **`<repo>`:** auto-derived or override — see [../../repo-derivation.md](../../repo-derivation.md).
- **Classification (tier 1, readiness):** idea / spec / execution-ready. Read the plan's `Kind:` frontmatter first (authoritative — see [../../plan-kinds.md](../../plan-kinds.md)); infer from content only when `Kind` is absent.
- **Classification (tier 2, shape — only for execution-ready):** picks which of the three execution engines to recommend first; all three are always offered.
- **Routing:** `superpowers:brainstorming` (idea), `superpowers:writing-plans` (spec). Execution-ready → menu of `harness:autonomous`, `harness:task-list-builder`→`task-list-runner`, `superpowers:executing-plans`.
- **Confirmation:** required before reading any plan file and before invoking any next skill.

## Procedure

Follow these steps in order. Do not skip steps.

### 1. List the plans

First, check the user's invocation for a repo override. Recognize:

- "do a plan from `<name>`"
- "plan-do `<name>`"
- "pick a plan from `<name>`"
- "in the `<name>` folder/bucket"

Then invoke the CLI, filtered to the plans that haven't been started yet:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list --status todo,backlog
```

**Run this command every time you reach step 1 — including when you've already listed the plans earlier in this same conversation.** The plan set changes between turns (a plan saved mid-conversation, a status flipped by another skill), so a list you printed a moment ago may already be stale. Never reproduce a previously shown list from memory; the numbered list you display must come from the output of the command you _just_ ran.

Add `--override <name>` if you found one. The CLI handles repo derivation. With `--status todo,backlog` it keeps only not-yet-started plans (a missing/blank `Status` counts as `backlog`), groups them `todo` then `backlog`, newest-first within each, and prints one `status<TAB>filename` line per plan. Any active plans it excluded (in-progress, in-review, …) are summarized on **stderr** as a `note: N other active plan(s) hidden (...)` line.

**If stdout is empty:**

- **stderr has a hidden-plans note** → there are active plans, but none are startable (they're already in-progress / in-review / etc.). Tell the user that — surface the note's counts — and offer to list everything (`list` with no `--status`) or steer manually. Do not say "no plans".
- **stderr is also empty** → the current repo has no active plans at all. List alternatives:

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list-repos
  ```

  Output is one repo per line with state counts (e.g., `herds: active=15 done=22 deferred=2`). Wait for the user to pick a different repo (re-run step 1 with `--override`) or steer manually.

**If stdout has lines**, display them as a numbered list — show each plan's status tag so the user sees what's queued vs. untriaged — and ask which one. If stderr carried a hidden-plans note, mention it below the list. Do not read or classify any files yet — classification only happens on the picked plan.

Example output to the user:

```text
Not-yet-started plans in ~/plans/wild-horses/:

  1. [todo]    2026-05-19-plan-do-design.md
  2. [todo]    2026-05-17-task-list-runner-refactor.md
  3. [backlog] 2026-05-15-harness-namespace-cleanup.md

(2 other plans are in progress — say "show all" to see them.)

Which one?
```

### 2. User picks a plan

The user replies with a number or a filename fragment. Resolve to a single filename from the CLI's output — the filename is the part **after the tab** on each line (the leading token is the status tag). If ambiguous (a fragment matches multiple), ask the user to disambiguate.

### 3. Read the picked plan

Use the `Read` tool on `~/plans/<repo>/<filename>` (the full path is the repo dir from step 1 plus the picked filename). The content stays in conversation context for the rest of this skill and for whatever skill is invoked next.

### 4. Classify the plan (tier 1: readiness)

Decide whether the plan is an idea, a spec, or _execution-ready_. There are two ways to land this, in priority order:

**4a. Trust the `Kind` frontmatter if present.** `plan-save` records a `Kind:` field (set with full conversation context at save time) — when it's there, it is the authoritative signal. Map it directly (see [../../plan-kinds.md](../../plan-kinds.md)):

| `Kind` (from frontmatter) | Readiness       | Next                |
| ------------------------- | --------------- | ------------------- |
| `idea`                    | idea            | step 5a             |
| `prd` / `design` / `spec` | spec            | step 5b             |
| `exec-plan`               | execution-ready | step 5c (exec menu) |

The user can still override at the confirmation gate — `Kind` is a strong prior, not a lock. If the file's content flatly contradicts its `Kind` (e.g. `Kind: idea` on a detailed task list), note the mismatch to the user instead of blindly following the tag.

**4b. Infer from content when `Kind` is absent or unrecognized** (old plans, hand-made files, `--from-path` saves). Make a judgment call from reading the file — these are heuristics, not exact-match rules:

| Readiness           | Signals                                                                                                                                                                                                               |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **idea**            | Short (~< 50 lines), exploratory tone, no clear structure, no numbered execution steps. Language like "what if", "thinking about", "could we", "maybe". No `## Design` / `## Architecture` sections.                  |
| **spec**            | Has sections like `## Design`, `## Architecture`, `## Requirements`, `## Components`, `## Goals/Non-goals`, `## Trade-offs`, `## Data model`. Describes WHAT, not step-by-step HOW. Reads like a design doc.          |
| **execution-ready** | Describes HOW: concrete steps, phases, or tasks with enough detail to start building. Includes both linear "do X then Y" plans and task-list-shaped plans with independent work units. This is the executable bucket. |

**If the plan is an idea** → go to step 5a. **If it's a spec** → go to step 5b. **If it's execution-ready** → go to step 5c (the execution menu).

**If the plan is ambiguous between spec and execution-ready** (it describes WHAT but also sketches HOW), present the call to the user rather than guessing silently — offer both the writing-plans path and the execution menu.

**If the plan doesn't fit any bucket** (e.g., it's a research note, a meeting log, a bare list of TODOs), say so and offer to let the user steer manually.

### 5a. Idea → suggest brainstorming

> I read `<filename>` as an **idea**. Suggested next: `superpowers:brainstorming` to turn it into a reviewed spec. Proceed? (Or steer manually.)

Wait for confirmation, then jump to step 6.

### 5b. Spec → suggest writing-plans

> I read `<filename>` as a **spec**. Suggested next: `superpowers:writing-plans` to turn it into a phased implementation plan. Proceed? (Or steer manually.)

Wait for confirmation, then jump to step 6.

### 5c. Execution-ready → offer all three engines (tier 2: shape)

The plan can run now. There are three execution engines; **offer all three**, recommended first. The recommendation comes from the plan's _shape_ — but it is only a best guess, because the deciding factor (how much the user wants to supervise) is theirs to make. List every option so they can override.

Pick the **recommended** engine with this classification (apply in order; first match wins the recommended slot):

| Recommend                                            | Signals in the plan                                                                                                                                                                                                             |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`harness:autonomous`**                             | Self-contained and well-specified; bounded scope; clear acceptance criteria; reads like a single ticket/feature that naturally ends in a PR; no mid-flight human judgment calls implied. _"Could hand this to an AFK agent."_   |
| **`harness:task-list-builder` → `task-list-runner`** | Multiple independent tasks; per-task acceptance criteria; dependency notation between tasks; "dispatch" / "subagents" / "in parallel" / "independent" language; large scope where structured tracking and resumability pay off. |
| **`superpowers:executing-plans`**                    | Sequential phases with explicit review/checkpoint language; dependent linear flow ("first do X, then do Y"); risky or high-uncertainty work the user would want to review phase-by-phase; TDD-with-review-gates.                |

The tiebreaker axis is **autonomy-readiness first** (is it specified enough to need no supervision?), then **independence** (parallel task-list vs. sequential review-gated).

Present the menu — recommended option first with a one-line reason, the others as alternatives, plus a manual escape hatch:

```text
`<filename>` is ready to execute. Here's how I can run it (recommended first):

  1. harness:autonomous — [recommended] AFK, no human in the loop: implements,
     tests, runs an independent sub-agent review to convergence, opens a PR.
  2. harness:task-list-builder → task-list-runner — convert to a structured JSON
     task list, then dispatch each task to a sub-agent; resumable, best for many
     independent tasks.
  3. superpowers:executing-plans — sequential execution with your review at each
     phase gate.
  4. Steer manually — I just keep the plan in context and you drive.

Which one? (1 is recommended because <shape-based reason>.)
```

Reorder 1–3 so the recommended engine is first; keep its `[recommended]` tag and adjust the closing rationale to match. Wait for the user's pick, then go to step 6.

### 6. Mark the plan in-progress, then invoke the chosen skill

Once the user has confirmed a route (any next skill — `brainstorming`, `writing-plans`, or an execution engine), **first** flip the plan's status so it stops showing up as "to start" and starts showing up in `plan-done`'s finish list — and in the same call clear the `Agent` field so groundcrew won't also claim a plan you're now driving yourself:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta update \
  --file ~/plans/<repo>/<filename> --field Status=in-progress --field Agent=
```

`--field Agent=` (empty value) removes the `Agent: <name>` tag entirely. The `Agent` tag is the groundcrew dispatch signal; once you start a plan locally, you are the one working it, so the tag is cleared unconditionally — even if it named a non-`claude` agent. `plan-crew` is the only path that ever _writes_ the tag (on promote to the queue); plan-do only ever removes it. `--file` takes the **full path** (no `--override` here — `file-meta` resolves the path directly). `--ticket <id>` is an alternative to `--file`: it locates the plan by its `Ticket:` frontmatter across all repos (exactly one of the two is required). Do this only when you are about to hand off to a skill. **Do not** mark in-progress (or clear Agent) on the manual-steer path (the user hasn't committed to working it through a skill yet) or before the user has confirmed.

**Then** use the `Skill` tool to invoke the chosen skill. The plan content is already in conversation context from step 3, so the invoked skill has full access — no explicit handoff payload is needed.

**Handoff specifics per engine:**

- **`harness:autonomous`** — the plan read in step 3 _is_ the Task. autonomous accepts an in-context plan as a task source (its input-resolution step 3), so no issue URL is needed — the plan content is the authoritative spec. You may also hand it the plan's file path (`~/plans/<repo>/<filename>`) explicitly. Do not look up or pass any `Ticket:` frontmatter field — the plan is the source of truth.
- **`harness:task-list-builder`** — invoke it to convert the plan into the structured JSON task list; it hands off to `harness:task-list-runner` to execute the tasks.
- **`superpowers:executing-plans`** — invoke directly; the plan in context is the implementation plan it executes.
- **`superpowers:brainstorming` / `superpowers:writing-plans`** — invoke directly (the idea / spec paths).

If the user picked a different skill than the suggestion or the recommended engine, invoke that one instead.

If the user wants to steer manually, just stop the skill here. The plan is read into context and they can drive freely.

## Common mistakes

- **Don't re-display a previously shown plan list from memory.** Step 1's `list` command must be re-run on every invocation — even a re-invocation moments later. The plan set changes between turns (a plan saved mid-conversation won't appear if you reprint a cached list), so the numbered list you show must always come from the output of the command you just ran, never from recall.
- **Reading and classifying multiple plans before the user picks.** Step 1 lists `status<TAB>filename` lines only. Reading multiple plans wastes context and biases classification toward whatever was read last.
- **Marking in-progress too early (or on manual-steer).** Step 6 flips `Status` to `in-progress` and clears `Agent` only _after_ the user confirms a skill handoff. Don't mark it on the manual-steer path, and don't mark it before confirmation — a plan the user hasn't committed to should stay in plan-do's not-yet-started list with its queue tag intact.
- **Saying "no plans" when stdout is empty but stderr has a hidden-plans note.** Empty stdout with a `note: N other active plan(s) hidden` line means everything is already in-progress/in-review — surface that, don't claim the repo is empty.
- **Auto-invoking the next skill without confirmation.** Steps 5a/5b/5c require a check-in even when the classification feels obvious. The skill's job is to _offer_ the next stage, not jump to it.
- **Collapsing the execution menu to a single suggestion.** For execution-ready plans, all three engines are always offered (step 5c). The shape classification only sets which one is _recommended first_ — it does not hide the others.
- **Treating the recommendation as a decision.** The recommended engine is a best guess from plan shape; how hands-off to be is the user's call. Lead with the recommendation, but let them pick any engine.
- **Passing a `Ticket:` URL to `harness:autonomous`.** The in-context plan is the Task — do not resolve or hand autonomous a frontmatter ticket URL.
- **Silently falling back when the current repo has no plans.** Step 1 says: tell the user, run `list-repos`, wait for direction. Don't auto-route to another folder.

## Edge cases

- **No _startable_ plans, but active plans exist** — `list --status todo,backlog` prints nothing on stdout but emits a hidden-plans note on stderr. Tell the user everything is already in progress (or in review), and offer `list` with no `--status` to see all of them.
- **No plans for the current repo at all** — both stdout and stderr empty. Show `list-repos` output and let the user pick another repo. Do not silently fall back.
- **`~/plans/` doesn't exist at all** — `list-repos` returns empty. Tell the user `plan-save` hasn't been used yet on this machine.
- **Plan fits no readiness bucket** — say so explicitly; offer to read into context and let the user steer.
- **Plan is ambiguous between spec and execution-ready** — offer both the `superpowers:writing-plans` path and the execution menu; let the user choose.
- **Filename fragment matches multiple plans** — ask the user to disambiguate; do not pick one arbitrarily.

## Notes

- This skill's only write to `~/plans/` is flipping the picked plan's `Status` to `in-progress` when it starts one (step 6). It never moves, deletes, or rewrites a plan's body. Sibling skill `plan-done` archives completed plans (moving files into `done/`).
- Status is the link between the `plan-*` skills: `plan-save` writes `backlog`, `plan-do` lists `todo`/`backlog` and flips the started plan to `in-progress`, and `plan-done` lists `in-progress`/`todo` (in-progress first). A plan therefore flows `backlog → todo → in-progress → done` across the family.
- Classification is two-tier. Tier 1 (readiness: idea / spec / execution-ready) gates _which path_ the plan takes — driven by the `Kind:` frontmatter when present (set by `plan-save`), falling back to content inference otherwise. Tier 2 (shape) runs only for execution-ready plans and only sets _which engine is recommended first_ in the menu — all three are always offered.
- `Kind` is the persisted form of the tier-1 readiness call: `plan-save` records it once with full context, so `plan-do` reads it instead of re-inferring on every pickup. The mapping (idea→idea, prd/design/spec→spec, exec-plan→execution-ready) lives in [../../plan-kinds.md](../../plan-kinds.md).
- The tier-2 discriminator between recommending `task-list-builder/runner` and `executing-plans` is task **independence** (parallel, dispatched vs. sequential, review-gated), not the words used — both use "phase" and "task" vocabulary. `harness:autonomous` sits above both on the autonomy axis: recommend it when the plan is specified enough to run with no human in the loop.
- Sibling skills in the `plan-` family (`plan-save`, `plan-done`) share the same CLI and the same `~/plans/<repo>/` tree.
