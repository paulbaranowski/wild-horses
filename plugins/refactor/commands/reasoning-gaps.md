---
description: Analyze code for AI reasoning gaps — untyped signatures, implicit control flow, hidden state, missing docs, and structural complexity that prevent agents from tracing data flow and predicting behavior. Spawns 3 parallel specialist agents, merges findings, and produces a prioritized remediation plan. Use when AI agents keep misunderstanding code, making wrong edits, or needing excessive exploration to complete tasks.
argument-hint: "[path or description] [--full] [--resume [task-file-path]]"
---

# AI Reasoning Gap Analysis

Analyze code for **AI reasoning gaps** — places where an AI agent cannot confidently trace data flow, predict runtime behavior, or orient itself in the codebase. Uses 3 parallel specialist agents, then synthesizes findings into a prioritized remediation plan with concrete interventions.

This is NOT a code quality review. Code can be well-written and still be opaque to AI reasoning. This skill answers: **"If an AI agent read this code, what would it get wrong?"**

**Bundled assets at `${CLAUDE_PLUGIN_ROOT}`** (if the variable isn't substituted in this context, find the files with `Glob "**/refactor/loop-protocol.md"` and read the siblings alongside it):

- `loop-protocol.md` — Phase 4 options menu. Shared with `/refactor:feedback-blockers`.
- `task-list-schema.md` — JSON task file shape. Shared with `/refactor:feedback-blockers`, `task-list-builder`, and `task-list-runner`.
- `skills/task-list-builder/SKILL.md` — task-list construction (verifySteps discovery, run-ID, JSON + MD writing, preview). Invoked from Phase 4 Options 1, 2, and 3 with `--slug reasoning-gaps --md-body-from-context`.
- `skills/task-list-runner/SKILL.md` — execution engine (resume, Agent loop, Task Implementation Prompt). Invoked from Phase 4 Options 1 and 2 and from `--resume`.
- `agents/reasoning-gaps/types-and-data-contracts.md`
- `agents/reasoning-gaps/implicit-flow-and-state.md`
- `agents/reasoning-gaps/structure-and-documentation.md`

**Target:** "$ARGUMENTS"

**Slug:** `reasoning-gaps` (use this value wherever `loop-protocol.md` says `<slug>`).

---

## Resume Check (before Phase 1)

If `$ARGUMENTS` contains `--resume`, hand off to the `task-list-runner` skill (it will auto-locate the in-progress task file or accept a path that follows `--resume`). Skip Phases 1–4 entirely.

---

## Phase 1: Determine Scope (you do this)

Based on arguments and context, determine what files to analyze:

1. **If a specific file/directory path is given** — collect those file paths.
2. **If a free-form description is given** (e.g., "the cli code", "the decoder pipeline", "authentication logic") — search the codebase to identify matching files. Use directory names, module names, class/function names, and file contents to resolve the description to a concrete list of files. Confirm the resolved scope with the user if ambiguous.
3. **If no arguments (DEFAULT)** — get only the files changed in the current PR branch: `git diff --name-only main...HEAD` plus any uncommitted changes via `git diff --name-only`. Exclude test files. This should typically yield 3–10 files. If it yields more than 15, ask the user to narrow scope.
4. **If `--full`** — collect all source files in `src/` or the main package directory (warn: may be slow).

Build a newline-separated list of absolute file paths. This is the **file list** you will pass to each agent.

Also read the project's CLAUDE.md if it exists — agents need project conventions for context.

---

## Phase 2: Spawn 3 Specialist Agents (ALL IN PARALLEL)

**CRITICAL REQUIREMENT:** You MUST launch all three agents in a SINGLE response message containing exactly 3 Agent tool calls. This is non-negotiable. Do NOT launch them one at a time. Do NOT wait for one to finish before launching the next. One message, three Agent tool calls, all concurrent.

For each agent below, read the indicated prompt file, substitute the two placeholders inside the fenced block (`{paste relevant CLAUDE.md sections here}` and `{paste the file list here}`), and pass the resulting prompt body to the Agent tool. Each agent must READ the files itself — do not paste file contents into prompts.

| #   | Agent                             | Prompt file                                                                  |
| --- | --------------------------------- | ---------------------------------------------------------------------------- |
| 1   | Type & Data Contract Analyst      | `${CLAUDE_PLUGIN_ROOT}/agents/reasoning-gaps/types-and-data-contracts.md`    |
| 2   | Implicit Flow & State Analyst     | `${CLAUDE_PLUGIN_ROOT}/agents/reasoning-gaps/implicit-flow-and-state.md`     |
| 3   | Structure & Documentation Analyst | `${CLAUDE_PLUGIN_ROOT}/agents/reasoning-gaps/structure-and-documentation.md` |

---

## Phase 3: Merge and Report (you do this, after all 3 agents complete)

Wait for all 3 agents to return. Then synthesize their findings into a unified report:

1. **Verify every finding before including it.** For each finding from each agent:
   - Read the cited file at the cited line number.
   - Confirm the quoted code in the finding matches what is actually in the file.
   - If the finding references content that does not exist at the cited location, **discard it silently** — do not include it in the report.
   - If the description is slightly inaccurate but the underlying issue is real, correct the description.
2. **Collect ratings** from each agent into the summary table.
3. **Deduplicate findings** — if two agents flagged the same file:line, merge into one finding noting which dimensions it affects (this is a signal of high impact).
4. **Cross-dimension findings are gold** — when the same code location appears in 2+ agent reports, flag it prominently. These are the highest-leverage fixes because one change improves multiple dimensions. Example: an untyped function that is also a 60-line monolith with no docstring — one refactor (decompose, type, document) fixes three problems.
5. **Compute overall score** — weighted average: Type & Data Contracts 35%, Implicit Flow & State 35%, Structure & Documentation 30%.
6. **Assign letter grade**:
   - 9–10: **A** — AI agents can reason confidently about this code.
   - 7–8: **B** — minor friction, agents will occasionally struggle.
   - 5–6: **C** — significant gaps, agents will make mistakes.
   - 3–4: **D** — agents cannot reliably reason about this code.
   - 1–2: **F** — opaque to AI reasoning.
7. **Create interventions with full coverage** — generate a ranked list of interventions that **collectively cover every critical, important, and minor finding**. Do NOT cap at a fixed number. Rank by impact: cross-dimension findings first, then by number of findings resolved. Each intervention should be a coherent change (e.g., "Create a PipelineConfig Pydantic model" or "Add module docstrings to the auth package"). Prefer interventions that resolve findings across multiple dimensions. For each intervention, set `createsNewCode: true` when it **either** creates new callable code (new functions, classes, methods, models, enums with methods) **or** modifies the observable behavior of existing code (bug fixes, changed logic, changed return shape, changed error handling — rare in reasoning-gaps work, but possible). Set `createsNewCode: false` for annotation/documentation-only changes (type hints, docstrings, comments, renames) and pure restructures with no behavior change. This field drives paired-test generation in step 9 — when in doubt, set to `true`. When you later expand interventions into `agentValidations` entries for the task list, each entry must be a factual statement that a fresh-context validation subagent can confirm by reading code (structural, behavioral, or documentation facts). The schema's structural rule: **if you can write a shell command that answers the question, it belongs in `verifySteps`, not here.** Entries like `"Tests pass"` or `"No type errors"` are forbidden — they'd force the validation subagent to re-run the commands `verifySteps` already ran. The schema (`task-list-schema.md`) is the source of truth for this contract.
8. **Coverage verification** — verify that every critical, important, and minor finding from the Findings by Severity section maps to at least one intervention's "Resolves" list. If any critical, important, or minor finding is uncovered, add an intervention for it. This step is non-negotiable.
9. **Generate paired test tasks** — for each intervention with `createsNewCode: true`, generate a companion test task placed immediately after it in the task ordering. The test task must:
   - Have a title prefixed with "Write tests for" followed by the name of what was created or changed.
   - Have a `what` field that specifies: (a) the exact new or changed functions/classes/methods to test, (b) specific test cases to write (at minimum: happy path, edge cases, and error handling), (c) where to put the test file (follow existing project test conventions).
   - Set `createsNewCode: false` (it does not create new production code).
   - Set `resolves: []` (it supports the preceding implementation task, not a finding).
   - Have concrete `agentValidations` entries specifying minimum test count and coverage areas (inspection-verifiable structural facts about the test file the validation subagent confirms by reading the test file). **Don't include "Tests pass"** — the `tests` verifyStep handles pass/fail; `agentValidations` describes what only inspection can confirm. See `task-list-schema.md`'s `agentValidations` definition.
   - Set `effort: "low"`.

   Interventions with `createsNewCode: false` do not get a paired test task — they are verified by their own `agentValidations` entries. See step 7 above and `task-list-schema.md`'s "Paired test tasks (rule)" for the full decision table on when to set `createsNewCode: true` vs `false`.

10. **Final check** — re-read the merged report. Every finding must have a file:line that exists and code that matches. If you cannot verify a finding, drop it.

Present the merged report:

```markdown
# AI Reasoning Gap Analysis

## Scope

[What was analyzed and why]

## Ratings Summary

| Dimension                 | Score            | One-line summary |
| ------------------------- | ---------------- | ---------------- |
| Type & Data Contracts     | X/10             | ...              |
| Implicit Flow & State     | X/10             | ...              |
| Structure & Documentation | X/10             | ...              |
| **Overall**               | **X/10 (Grade)** | ...              |

## Cross-Dimension Findings (highest leverage)

- [file:line] Description — affects: [list of dimensions] — why this matters

## Findings by Severity

### Critical

- [file:line] `category-tag` Description — [dimension] — AI reasoning harm

### Important

- [file:line] `category-tag` Description — [dimension] — impact

### Minor

- [file:line] `category-tag` Description — [dimension]

## Interventions (ranked by impact)

> Every critical, important, and minor finding MUST appear in at least one intervention's Resolves list.

### 1. [Intervention title]

**What:** [specific change — files to modify, models to create, annotations to add]
**Resolves:** [list of findings this addresses, by file:line]
**Why highest leverage:** [which dimensions improve and how]
**Effort:** low / medium / high

### 2. ...

[continue until ALL critical, important, and minor findings are covered — no fixed cap]

## Coverage Check

- Critical findings covered: X/X
- Important findings covered: X/X
- Minor findings covered: X/X
- [List any findings NOT covered — this section should be empty.]
```

---

## Phase 4: Propose

Follow the **Phase 4 — Propose** procedure in `${CLAUDE_PLUGIN_ROOT}/loop-protocol.md`. The four options (Save plan and implement all / Save plan and fix top intervention / Save full remediation plan / Revise) live there. `loop-protocol.md` delegates task-list construction (verifySteps discovery, run-ID, JSON + MD writing, preview) to the `task-list-builder` skill (invoked with `--slug reasoning-gaps --md-body-from-context`) and execution to the `task-list-runner` skill. The JSON task file's shape is in `${CLAUDE_PLUGIN_ROOT}/task-list-schema.md`.

---

## Guidelines

- **Read before judging.** Agents must read full files and understand context. A pattern that looks wrong in isolation may be correct in context.
- **Respect existing architecture.** Don't suggest rewriting in a different paradigm. Work within the project's language, framework, and style.
- **AI reasoning is the lens.** Every finding must answer: "What specific thing would an AI agent get wrong, miss, or be unable to determine because of this gap?" If you cannot articulate the AI reasoning harm, do not report the finding.
- **Boundaries matter most.** Prioritize findings at module boundaries, public APIs, and cross-file interfaces. Private implementation details are lower priority because they only affect agents reading that specific function.
- **Concrete fixes only.** Never say "add types." Say "add `-> ProcessResult` return type to `process()` at pipeline.py:45 because callers in executor.py:23 and validator.py:12 cannot determine the return structure."
- **High confidence only.** Skip stylistic preferences and subjective observations. Every finding must cite file:line and explain concrete AI reasoning harm.
- **Verify before reporting.** Every finding must quote the actual code at the cited file:line. During Phase 3, re-read each cited location and discard any finding whose quoted code does not match what is in the file. Never report findings about content you have not verified exists.
- **Cross-dimension signals matter most.** When multiple agents flag the same location, that is where the highest leverage is.
- **Interventions must cover all critical, important, and minor findings.** Design interventions as coherent changes that resolve multiple findings at once — "Add a PipelineConfig Pydantic model" resolves 12 type-gap findings in one change. But never sacrifice coverage for brevity: if a critical, important, or minor finding doesn't fit into an existing intervention, create a new one. No finding left behind.
- **This is not a code quality review.** Code can be well-written and still opaque to AI reasoning. A clean, idiomatic function with no type annotations is a reasoning gap. An ugly function with full type annotations and a clear docstring is AI-readable.
- **Never recommend `getattr()` or `hasattr()` as a fix.** Replacing `data[key]` with `getattr(obj, key)` or `key in data` with `hasattr(obj, key)` moves dynamic lookup from dict to attribute access — the AI still cannot trace which attribute is accessed or checked, cannot validate it exists at the type level, and cannot follow the data flow. Always recommend typed methods or typed mappings instead.
