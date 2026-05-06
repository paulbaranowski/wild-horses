---
description: Analyze code for feedback-loop blockers — encapsulation gaps, OOP design issues, testability barriers, and harness-unfriendly patterns that prevent fast, clear change-test-fix cycles. Spawns 4 parallel specialist agents, merges findings, and produces a prioritized remediation plan. Use when code changes cause unexpected failures, tests are hard to write, or the change-test-fix cycle is slow.
argument-hint: "[path or description] [--full] [--resume [task-file-path]]"
---

# Feedback Blockers Review

Analyze code for **feedback-loop blockers** — encapsulation gaps, OOP design issues, testability barriers, and harness-unfriendly patterns that prevent fast, clear change-test-fix cycles. Uses 4 parallel specialist agents, then synthesizes findings into a prioritized remediation plan with concrete interventions.

**Bundled assets at `${CLAUDE_PLUGIN_ROOT}`** (if the variable isn't substituted in this context, find the files with `Glob "**/harness/loop-protocol.md"` and read the siblings alongside it):

- `loop-protocol.md` — Phase 4 options menu and the JSON task schema. Shared with `/harness:reasoning-gaps`.
- `skills/task-list-runner/SKILL.md` — execution engine (resume, Agent loop, Task Implementation Prompt). Invoked from Phase 4 Options 1 and 2 and from `--resume`.
- `agents/feedback-blockers/encapsulation.md`
- `agents/feedback-blockers/oop-design.md`
- `agents/feedback-blockers/testability.md`
- `agents/feedback-blockers/harness-friendliness.md`

**Target:** "$ARGUMENTS"

**Slug:** `feedback-blockers` (use this value wherever `loop-protocol.md` says `<slug>`).

---

## Resume Check (before Phase 1)

If `$ARGUMENTS` contains `--resume`, hand off to the `task-list-runner` skill (it will auto-locate the in-progress task file or accept a path that follows `--resume`). Skip Phases 1–4 entirely.

---

## Phase 1: Determine Scope (you do this)

Based on arguments and context, determine what files to analyze:

1. **If a specific file/directory path is given** — collect those file paths.
2. **If a free-form description is given** (e.g., "the cli code", "the decoder pipeline", "authentication logic") — search the codebase to identify matching files. Use directory names, module names, class/function names, and file contents to resolve the description to a concrete list of files. Confirm the resolved scope with the user if ambiguous.
3. **If no arguments (DEFAULT)** — get only the files changed in the current PR branch: `git diff --name-only main...HEAD` plus any uncommitted changes via `git diff --name-only`. Exclude test files and non-source files. This should typically yield 3–10 files. If it yields more than 15, ask the user to narrow scope.
4. **If `--full`** — collect all source files in `src/` or the main package directory (warn: may be slow).

Build a newline-separated list of absolute file paths. This is the **file list** you will pass to each agent.

Also read the project's CLAUDE.md if it exists — agents need project conventions for context.

---

## Phase 2: Spawn 4 Specialist Agents (ALL IN PARALLEL)

**CRITICAL REQUIREMENT:** You MUST launch all four agents in a SINGLE response message containing exactly 4 Agent tool calls. This is non-negotiable. Do NOT launch them one at a time. Do NOT wait for one to finish before launching the next. One message, four Agent tool calls, all concurrent.

For each agent below, read the indicated prompt file, substitute the two placeholders inside the fenced block (`{paste relevant CLAUDE.md sections here}` and `{paste the file list here}`), and pass the resulting prompt body to the Agent tool. Each agent must READ the files itself — do not paste file contents into prompts.

| #   | Agent                        | Prompt file                                                              |
| --- | ---------------------------- | ------------------------------------------------------------------------ |
| 1   | Encapsulation Analyst        | `${CLAUDE_PLUGIN_ROOT}/agents/feedback-blockers/encapsulation.md`        |
| 2   | OOP Design Analyst           | `${CLAUDE_PLUGIN_ROOT}/agents/feedback-blockers/oop-design.md`           |
| 3   | Testability Analyst          | `${CLAUDE_PLUGIN_ROOT}/agents/feedback-blockers/testability.md`          |
| 4   | Harness-Friendliness Analyst | `${CLAUDE_PLUGIN_ROOT}/agents/feedback-blockers/harness-friendliness.md` |

---

## Phase 3: Merge and Report (you do this, after all 4 agents complete)

Wait for all 4 agents to return. Then synthesize their findings into a unified report:

1. **Verify every finding before including it.** For each finding from each agent:
   - Read the cited file at the cited line number.
   - Confirm the quoted code in the finding matches what is actually in the file.
   - If the finding references content that does not exist at the cited location, **discard it silently** — do not include it in the report.
   - If the description is slightly inaccurate but the underlying issue is real, correct the description.
2. **Collect ratings** from each agent into the summary table.
3. **Deduplicate findings** — if two agents flagged the same file:line, merge into one finding noting which pillars it affects (this is a signal of high impact).
4. **Cross-pillar findings are gold** — when the same code location appears in 2+ agent reports, flag it prominently. These are the highest-leverage fixes because one change improves multiple pillars.
5. **Compute overall score** — weighted average: Encapsulation 20%, OOP Design 20%, Testability 30%, Harness-Friendliness 30% (testability and harness-friendliness weighted higher because they directly affect development velocity).
6. **Assign letter grade**:
   - 9–10: **A** — fast, clear feedback loops; changes propagate predictably.
   - 7–8: **B** — minor friction; some changes require extra investigation.
   - 5–6: **C** — significant blockers; changes frequently cause unexpected failures.
   - 3–4: **D** — feedback loops are slow and opaque; agents struggle.
   - 1–2: **F** — no reliable feedback signal.
7. **Create interventions with full coverage** — generate a ranked list of interventions that **collectively cover every critical and important finding**. Do NOT cap at a fixed number. Rank by impact: cross-pillar findings first, then by number of findings resolved. Each intervention should be a coherent change (e.g., "Extract shared validation into a ValidationService" or "Inject dependencies via constructor in the pipeline module"). Prefer interventions that resolve findings across multiple pillars. For each intervention, determine whether it **creates new callable code** (new functions, classes, methods, services, protocols) or **modifies/restructures existing code** (extracting methods, adding interfaces, restructuring classes). Tag each with `createsNewCode: true` or `createsNewCode: false`.
8. **Coverage verification** — verify that every critical and important finding from the Findings by Severity section maps to at least one intervention's "Resolves" list. If any critical or important finding is uncovered, add an intervention for it. This step is non-negotiable.
9. **Generate paired test tasks** — for each intervention where `createsNewCode` is `true`, generate a companion test task placed immediately after it in the task ordering. The test task must:
   - Have a title prefixed with "Write tests for" followed by the name of what was created.
   - Have a `what` field that specifies: (a) the exact new functions/classes/methods to test, (b) specific test cases to write (at minimum: happy path, edge cases, and error handling), (c) where to put the test file (follow existing project test conventions).
   - Set `createsNewCode: false` (it does not create new production code).
   - Set `resolves: []` (it supports the preceding implementation task, not a finding).
   - Have concrete `acceptanceCriteria` specifying minimum test count and coverage areas.
   - Set `effort: "low"`.

   Do NOT generate test tasks for interventions that only restructure existing code without creating new callable interfaces. These are verified by their own acceptance criteria.

10. **Final check** — re-read the merged report. Every finding must have a file:line that exists and code that matches. If you cannot verify a finding, drop it.

Present the merged report:

```markdown
# Feedback Blockers Review

## Scope

[What was analyzed and why]

## Ratings Summary

| Pillar               | Score            | One-line summary |
| -------------------- | ---------------- | ---------------- |
| Encapsulation        | X/10             | ...              |
| OOP Design           | X/10             | ...              |
| Testability          | X/10             | ...              |
| Harness-Friendliness | X/10             | ...              |
| **Overall**          | **X/10 (Grade)** | ...              |

## Cross-Pillar Findings (highest leverage)

- [file:line] Description — affects: [list of pillars] — why this matters

## Findings by Severity

### Critical

- [file:line] Description — [pillar] — concrete harm

### Important

- [file:line] Description — [pillar] — impact

### Minor

- [file:line] Description — [pillar]

## Interventions (ranked by impact)

> Every critical and important finding MUST appear in at least one intervention's Resolves list.

### 1. [Intervention title]

**What:** [specific change — files to modify, structures to create, patterns to fix]
**Resolves:** [list of findings this addresses, by file:line]
**Why highest leverage:** [which pillars improve and how]
**Effort:** low / medium / high

### 2. ...

[continue until ALL critical and important findings are covered — no fixed cap]

## Coverage Check

- Critical findings covered: X/X
- Important findings covered: X/X
- [List any findings NOT covered — this section should be empty.]
```

---

## Phase 4: Propose

Follow the **Phase 4 — Propose** procedure in `${CLAUDE_PLUGIN_ROOT}/loop-protocol.md`. The four options (Save plan and implement all / Save plan and fix top intervention / Save full remediation plan / Revise), the run-ID generation, the JSON task schema, the Agent loop, and the Task Implementation Prompt all live there.

---

## Guidelines

- **Read before judging.** Agents must read full files and understand context. A pattern that looks wrong in isolation may be correct in context.
- **Respect existing architecture.** Don't suggest rewriting in a different paradigm. Work within the project's style.
- **No gold-plating.** Every suggestion must solve a concrete, present problem. No "for the future" abstractions.
- **High confidence only.** Skip stylistic preferences and subjective observations. Every finding must cite file:line and explain concrete harm.
- **Verify before reporting.** Every finding must quote the actual code at the cited file:line. During Phase 3, re-read each cited location and discard any finding whose quoted code does not match what is in the file. Never report findings about content you have not verified exists.
- **Cross-pillar signals matter most.** When multiple agents flag the same location, that's where the highest leverage is.
- **Interventions must cover all critical and important findings.** Design interventions as coherent changes that resolve multiple findings at once — but never sacrifice coverage for brevity: if a critical or important finding doesn't fit into an existing intervention, create a new one. No finding left behind.
