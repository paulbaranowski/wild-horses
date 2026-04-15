---
description: Analyze code for feedback-loop blockers — encapsulation gaps, OOP design issues, testability barriers, and harness-unfriendly patterns that prevent fast, clear change-test-fix cycles. Spawns 4 parallel specialist agents, merges findings, and produces a prioritized remediation plan. Use when code changes cause unexpected failures, tests are hard to write, or the change-test-fix cycle is slow.
argument-hint: "[path or description] [--full] [--resume [task-file-path]]"
---

# Feedback Blockers Review

Analyze code for **feedback-loop blockers** — encapsulation gaps, OOP design issues, testability barriers, and harness-unfriendly patterns that prevent fast, clear change-test-fix cycles. Uses 4 parallel specialist agents, then synthesizes findings into a prioritized remediation plan with concrete interventions.

**Target:** "$ARGUMENTS"

---

## Resume Check (before Phase 1)

If `$ARGUMENTS` contains `--resume`, skip all analysis and restart the loop from an existing task file:

1. **Locate the task file:**
   - If a path follows `--resume` (e.g., `--resume docs/exec-plans/active/2026-04-14-a3f2-user-endpoints.feedback-blockers.json`): read that file directly. If the path ends in `.feedback-blockers.md`, read its YAML frontmatter `task_file` field, then validate the pointer: the path must exist, be readable, parse as valid JSON, and contain at least one task with `status` `"pending"` or `"in-progress"`. If any check fails, report a clear error (e.g., "task_file points to X which does not exist" or "JSON at X has no pending tasks") and stop. Otherwise, open the validated JSON file.
   - If no path provided (just `--resume`): scan `docs/exec-plans/active/*.feedback-blockers.json` for files with any task where `status` is `"pending"` or `"in-progress"`. If no JSON matches, fall back to scanning `docs/exec-plans/active/*.feedback-blockers.md` — for each `.md` candidate, read its YAML frontmatter `task_file` field, then validate the pointer: the path must exist, be readable, parse as valid JSON, and contain at least one task with `status` `"pending"` or `"in-progress"`. Discard any candidate that fails any of these checks.
     - If exactly one validated match (from either scan): use it.
     - If multiple validated matches: list them with progress summaries (complete/pending/failed counts) and ask the user to pick one.
     - If no validated matches: report "No in-progress feedback-blockers task files found" and stop.

2. **Validate the task file:** Confirm it has a `tasks` array and `testCommand` fields. If invalid, report the error and stop.

3. **Show resume summary:**
   - Task file path
   - Total / complete / in-progress / pending / failed task counts
   - List the remaining tasks (pending and in-progress) with their id, title, and effort

4. **Ask the user what they'd like to do:**

   > **How would you like to proceed?**
   > 1. **Run all remaining** — Implement all pending/in-progress tasks via automated loop
   > 2. **Run next task only** — Implement just the next pending/in-progress task, then stop
   > 3. **Give feedback** — Review or adjust the plan before continuing

   - **Option 1 (Run all remaining):** Jump directly to **Option 1's Step 4** — start the Agent loop with `MAX_ITER` = remaining tasks (pending + in-progress) × 1.5 (rounded up) + 1, using the existing task file path.
   - **Option 2 (Run next task only):** Find the first task with status `"in-progress"` or `"pending"` in the task file. Use a single foreground **Agent tool** call with the **Task Implementation Prompt** from Option 1's Step 4, substituting the task file path. After the Agent returns, re-read the JSON task file, show the updated task summary (complete/pending/failed counts) to the user, and return to step 4.
   - **Option 3 (Give feedback):** Ask the user for their feedback (e.g., reorder tasks, skip a task, modify a task's approach, adjust scope). You (the agent) apply the feedback by directly editing the JSON task file — no separate `claude -p` call needed, since these are structural edits (reordering, setting status to `"skipped"`, revising a task's `what` field), not code implementation. After updating the file, return to step 4 — show the updated task list and ask again.

5. **Skip Phases 1–4 entirely** — no analysis, no report generation, no options menu.

---

## Phase 1: Determine Scope (you do this)

Based on arguments and context, determine what files to analyze:

1. **If a specific file/directory path is given** — collect those file paths
2. **If a free-form description is given** (e.g., "the cli code", "the decoder pipeline", "authentication logic") — search the codebase to identify matching files. Use directory names, module names, class/function names, and file contents to resolve the description to a concrete list of files. Confirm the resolved scope with the user if ambiguous.
3. **If no arguments (DEFAULT)** — get only the files changed in the current PR branch: `git diff --name-only main...HEAD` plus any uncommitted changes via `git diff --name-only`. Exclude test files and non-source files. This should typically yield 3-10 files. If it yields more than 15, ask the user to narrow scope.
4. **If `--full`** — collect all source files in `src/` or the main package directory (warn: may be slow)

Build a newline-separated list of absolute file paths. This is the **file list** you will pass to each agent.

Also read the project's CLAUDE.md if it exists — agents need project conventions for context.

---

## Phase 2: Spawn 4 Specialist Agents (ALL IN PARALLEL)

**CRITICAL REQUIREMENT:** You MUST launch all four agents in a SINGLE response message containing exactly 4 Agent tool calls. This is non-negotiable. Do NOT launch them one at a time. Do NOT wait for one to finish before launching the next. One message, four Agent tool calls, all concurrent.

Each agent receives the same file list but analyzes through a different lens. Each agent must READ the files itself (do not paste file contents into prompts).

### Agent 1: Encapsulation Analyst

Use the Agent tool with this prompt:

```
You are an encapsulation specialist reviewing code for information hiding, boundary integrity, and minimal interfaces.

PROJECT CONVENTIONS:
{paste relevant CLAUDE.md sections here}

FILES TO ANALYZE:
{paste the file list here}

Read each file and analyze for encapsulation quality. Look for:

- **Public fields that should be private** — fields accessed only internally but exposed publicly. Check: are there fields that no external caller references? In Python, look for attributes that lack a leading underscore but are only used within the class.
- **Leaky abstractions** — callers reaching into implementation details (e.g., accessing .data, ._internal, or internal structure directly instead of using methods). Check cross-file references.
- **Missing boundary validation** — constructors (__init__) or factory methods that accept invalid state. Can you create an instance that violates the class's own invariants? Focus on object construction integrity, not public API input validation (that is a type/contract concern covered elsewhere).
- **Mutable state exposure** — methods returning mutable internals (lists, dicts, sets) that callers could modify, breaking invariants. Look for properties or getters that return self._list directly.
- **God objects** — classes with too many attributes (>7-8) or methods (>10-12) suggesting multiple responsibilities merged into one.

For each finding, report:
- Severity: critical / important / minor
- File path and line number
- Actual code (quote the exact lines you are flagging — verbatim, not paraphrased)
- What the issue is and the concrete harm (not just "could be better")
- A brief suggested fix direction (1 sentence)

End with a rating: `Encapsulation: X/10` with a one-line justification.

Format your response as:
## Encapsulation Analysis

### Rating: X/10
[one-line justification]

### Findings
#### Critical
- [file:line] description — harm — fix direction

#### Important
- [file:line] description — harm — fix direction

#### Minor
- [file:line] description — harm — fix direction

IMPORTANT: Only report issues where you have HIGH CONFIDENCE the code would meaningfully improve. Skip stylistic preferences. Every finding must cite a specific file:line.
```

### Agent 2: OOP Design Analyst

Use the Agent tool with this prompt:

```
You are an object-oriented design specialist reviewing code for proper use of OOP principles: polymorphism, composition, single responsibility, and domain modeling.

PROJECT CONVENTIONS:
{paste relevant CLAUDE.md sections here}

FILES TO ANALYZE:
{paste the file list here}

Read each file and analyze for OOP design quality. Look for:

- **Procedural code hiding in classes** — classes that are just namespaces for functions with no real object identity or state. The methods don't use self meaningfully. These should either be standalone functions or redesigned as proper objects.
- **Inheritance vs composition mismatches** — deep inheritance hierarchies (>2 levels) that should be composition; OR duplicated code across sibling classes that would benefit from a shared base or mixin.
- **Single Responsibility violations** — classes doing more than one thing. Signs: methods that cluster into unrelated groups, __init__ that sets up multiple unrelated subsystems, class name requires "and" to describe.
- **Missing polymorphism** — long if/elif chains or isinstance() checks dispatching on type, causing duplicated logic across branches. Adding a new type requires modifying every dispatch site instead of adding one class. Violates the open/closed principle — the code is not extensible without editing existing branches.
- **Anemic domain models** — data classes or dataclasses with no behavior, where all logic lives in external functions that take the data class as a parameter. The behavior should live with the data.

For each finding, report:
- Severity: critical / important / minor
- File path and line number
- Actual code (quote the exact lines you are flagging — verbatim, not paraphrased)
- What the issue is and the concrete harm
- A brief suggested fix direction (1 sentence)

End with a rating: `OOP Design: X/10` with a one-line justification.

Format your response as:
## OOP Design Analysis

### Rating: X/10
[one-line justification]

### Findings
#### Critical
- [file:line] description — harm — fix direction

#### Important
- [file:line] description — harm — fix direction

#### Minor
- [file:line] description — harm — fix direction

IMPORTANT: Respect the project's existing architecture. Don't suggest rewriting in a different paradigm. Only flag issues where the CURRENT design creates concrete problems. Skip stylistic preferences.
```

### Agent 3: Testability Analyst

Use the Agent tool with this prompt:

```
You are a testability specialist reviewing code for dependency injection, seam availability, determinism, and unit isolation.

PROJECT CONVENTIONS:
{paste relevant CLAUDE.md sections here}

FILES TO ANALYZE:
{paste the file list here}

Read each file and analyze for testability. Also read any corresponding test files (test_*.py or *_test.py) to understand current test coverage and testing patterns.

Look for:

- **Hard-wired dependencies** — classes that construct their own collaborators inside __init__ or methods (e.g., `self.db = Database()`) instead of accepting them as parameters. This makes it impossible to substitute test doubles.
- **Untestable side effects** — functions that perform I/O (file, network, database) or mutate shared state as a side effect of their primary purpose, making it impossible to test the core logic without triggering the side effect. The test is forced to become an integration test when a unit test should suffice. Look for: side effects you cannot stub out, side effects that make tests slow or flaky, logic buried behind I/O that cannot be exercised in isolation.
- **Non-determinism** — use of datetime.now(), time.time(), random, uuid, or os.environ reads without injection points. Tests become flaky or require monkeypatching.
- **Missing seams** — no way to substitute a dependency for testing. No constructor parameter, no protocol/ABC, no configuration mechanism. The only option is monkeypatching, which is brittle.

For each finding, report:
- Severity: critical / important / minor
- File path and line number
- Actual code (quote the exact lines you are flagging — verbatim, not paraphrased)
- What the issue is and what it prevents you from testing
- A brief suggested fix direction (1 sentence)

End with a rating: `Testability: X/10` with a one-line justification.

Format your response as:
## Testability Analysis

### Rating: X/10
[one-line justification]

### Findings
#### Critical
- [file:line] description — what you can't test — fix direction

#### Important
- [file:line] description — what you can't test — fix direction

#### Minor
- [file:line] description — what you can't test — fix direction

IMPORTANT: Focus on PRACTICAL testability. Don't suggest making everything injectable for the sake of it. Flag cases where the current design actively prevents writing useful tests or forces tests to be fragile.
```

### Agent 4: Harness-Friendliness Analyst

Use the Agent tool with this prompt:

```
You are a harness-friendliness specialist. You evaluate whether code gives fast, clear feedback loops when an agent or automated tool modifies it. Your unique perspective: "If an AI agent made a mistake in this code, how quickly and clearly would it find out?"

PROJECT CONVENTIONS:
{paste relevant CLAUDE.md sections here}

FILES TO ANALYZE:
{paste the file list here}

Read each file and analyze for harness-friendliness. Look for:

- **Opaque failures** — exceptions or error paths that lose context. Bare `except:` or `except Exception:` that swallow the original error. Generic error messages like "something went wrong" instead of including the actual values and state. An agent seeing this error cannot diagnose what happened.
- **Large blast radius** — changing one behavior requires touching many files. Look for: a single constant or configuration value used across 5+ files without a central definition, changes that require coordinated updates across multiple modules with no automated enforcement (e.g., renaming a status value requires updating 4 files manually). Well-factored code lets an agent change one thing in one place.
- **Missing observability** — functions that take input and produce output with no way to inspect intermediate state. No logging, no debug methods, no way to see what happened inside when the output is wrong. Look for complex multi-step functions with no intermediate visibility.
- **Noisy feedback from local changes** — code where a small, local change triggers failures in distant, seemingly-unrelated tests or modules. The feedback signal is noisy — the agent cannot tell if its change was wrong or if the failure is unrelated coupling. Look for: test suites where changing one function breaks tests for a different feature, shared setup/fixtures that create invisible dependencies between test cases, modules where editing one method requires updating assertions in 3+ unrelated test files.
- **Poor error locality** — when something goes wrong, can you tell WHERE and WHY from the error alone? Or do you need to trace through 3+ layers? Look for: re-raised exceptions without context, error messages that don't include the triggering input, validation errors that don't say which field failed.

For each finding, report:
- Severity: critical / important / minor
- File path and line number
- Actual code (quote the exact lines you are flagging — verbatim, not paraphrased)
- What the issue is and how it degrades the feedback loop
- A brief suggested fix direction (1 sentence)

End with a rating: `Harness-Friendliness: X/10` with a one-line justification.

Format your response as:
## Harness-Friendliness Analysis

### Rating: X/10
[one-line justification]

### Findings
#### Critical
- [file:line] description — feedback loop impact — fix direction

#### Important
- [file:line] description — feedback loop impact — fix direction

#### Minor
- [file:line] description — feedback loop impact — fix direction

IMPORTANT: This is NOT a general code review. Only flag issues that specifically degrade the feedback loop for agents and automated tooling. A function can be ugly but harness-friendly if it fails fast and fails loud.
```

---

## Phase 3: Merge and Report (you do this, after all 4 agents complete)

Wait for all 4 agents to return. Then synthesize their findings into a unified report:

1. **Verify every finding before including it.** For each finding from each agent:
   - Read the cited file at the cited line number
   - Confirm the quoted code in the finding matches what is actually in the file
   - If the finding references content that does not exist at the cited location, **discard it silently** — do not include it in the report
   - If the description is slightly inaccurate but the underlying issue is real, correct the description
2. **Collect ratings** from each agent into the summary table
3. **Deduplicate findings** — if two agents flagged the same file:line, merge into one finding noting which pillars it affects (this is a signal of high impact)
4. **Cross-pillar findings are gold** — when the same code location appears in 2+ agent reports, flag it prominently. These are the highest-leverage fixes because one change improves multiple pillars.
5. **Compute overall score** — weighted average: Encapsulation 20%, OOP Design 20%, Testability 30%, Harness-Friendliness 30% (testability and harness-friendliness weighted higher because they directly affect development velocity)
6. **Assign letter grade**:
   - 9-10: **A** — fast, clear feedback loops; changes propagate predictably
   - 7-8: **B** — minor friction; some changes require extra investigation
   - 5-6: **C** — significant blockers; changes frequently cause unexpected failures
   - 3-4: **D** — feedback loops are slow and opaque; agents struggle
   - 1-2: **F** — no reliable feedback signal
7. **Create interventions with full coverage** — generate a ranked list of interventions that **collectively cover every critical and important finding**. Do NOT cap at a fixed number. If 3 interventions cover everything, list 3. If 8 are needed, list 8. Rank by impact: cross-pillar findings first, then by number of findings resolved. Each intervention should be a coherent change (e.g., "Extract shared validation into a ValidationService" or "Inject dependencies via constructor in the pipeline module"). Prefer interventions that resolve findings across multiple pillars. For each intervention, determine whether it **creates new callable code** (new functions, classes, methods, services, protocols) or **modifies/restructures existing code** (extracting methods, adding interfaces, restructuring classes). Tag each with `createsNewCode: true` or `createsNewCode: false`.
8. **Coverage verification** — after creating the intervention list, verify that every critical and important finding from the Findings by Severity section maps to at least one intervention's "Resolves" list. If any critical or important finding is uncovered, add an intervention for it. This step is non-negotiable — no critical or important finding may exist in the report without a corresponding intervention.
9. **Generate paired test tasks** — for each intervention where `createsNewCode` is `true`, generate a companion test task placed immediately after it in the task ordering. The test task must:
   - Have a title prefixed with "Write tests for" followed by the name of what was created
   - Have a `what` field that specifies: (a) the exact new functions/classes/methods to test, (b) specific test cases to write (at minimum: happy path, edge cases, and error handling), (c) where to put the test file (follow existing project test conventions)
   - Set `createsNewCode: false` (it does not create new production code)
   - Set `resolves: []` (it supports the preceding implementation task, not a finding)
   - Have concrete `acceptanceCriteria` specifying minimum test count and coverage areas
   - Set `effort: "low"`
   Do NOT generate test tasks for interventions that only restructure existing code without creating new callable interfaces. These are verified by their own acceptance criteria.
10. **Final check** — re-read the merged report. Every finding must have a file:line that exists and code that matches. If you cannot verify a finding, drop it.

Present the merged report:

```markdown
# Feedback Blockers Review

## Scope
[What was analyzed and why]

## Ratings Summary
| Pillar | Score | One-line summary |
|--------|-------|-----------------|
| Encapsulation | X/10 | ... |
| OOP Design | X/10 | ... |
| Testability | X/10 | ... |
| Harness-Friendliness | X/10 | ... |
| **Overall** | **X/10 (Grade)** | ... |

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
- [List any findings NOT covered — this section should be empty. If it is not, add more interventions above.]
```

---

## Phase 4: Propose (you do this)

After presenting the merged report, briefly explain the interventions with trade-offs for each. Then prompt the user:

> **What would you like to do?**
> 1. **Save plan and implement all** — Write plan and implement ALL interventions iteratively via automated loop
> 2. **Save plan and fix top intervention** — Write the full remediation plan and implement intervention #1
> 3. **Save full remediation plan** — Write the plan for incremental work
> 4. **Revise** — Provide feedback to refine the analysis or change focus

Before executing any option below, generate a **run ID** by running `openssl rand -hex 2` to produce a 4-character hex string (e.g., `a3f2`). Use this same run ID in all file names produced by this run — this prevents collisions when the command is run multiple times on the same day.

### Option 1: Save plan and implement all

Save the analysis and implement all interventions iteratively. Each intervention is implemented by a foreground Agent tool call within this conversation — you will see every file read, edit, and test run as it happens. The JSON task file tracks progress between iterations.

**Step 1 — Discover the test command.** Check CLAUDE.md for the project's test command. Fall back to `uv run pytest` or `npm test`.

**Step 2 — Write the markdown report** to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.feedback-blockers.md` (where YYYY-MM-DD is today's date and `<run-id>` is the 4-character hex run ID).

Use YAML frontmatter for metadata:

```yaml
---
status: in-progress
task_file: "docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.feedback-blockers.json"
generated: "YYYY-MM-DDTHH:MM:SSZ"
---
```

The body contains the full report: Scope (with repo-relative file paths), Ratings Summary, Cross-Pillar Findings, Findings by Severity, Interventions (with full details), and Coverage Check. This is the human-readable artifact — the loop does NOT modify this file.

**Step 3 — Write the JSON task file** to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.feedback-blockers.json`.

This is the machine-readable task list that the loop reads and writes for state tracking. Convert the absolute file paths from Phase 1 to repo-relative paths for the `scope` array (strip the repository root prefix — e.g., `/Users/name/project/src/pipeline.py` becomes `src/pipeline.py`). Extract each intervention into a task. For interventions tagged `createsNewCode: true`, place a paired test task immediately after:

```json
{
  "plan": "docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.feedback-blockers.md",
  "testCommand": "<discovered test command>",
  "scope": ["<repo-relative file paths from Phase 1>"],
  "tasks": [
    {
      "id": 1,
      "title": "Extract ValidationService from UserController",
      "what": "Move validation logic from UserController into a new ValidationService class with injectable dependencies. Replace direct calls in UserController with delegated calls to the service.",
      "resolves": ["user_controller.py:23", "user_controller.py:67", "user_controller.py:89"],
      "effort": "medium",
      "createsNewCode": true,
      "status": "pending",
      "acceptanceCriteria": [
        "ValidationService class exists with clear single responsibility",
        "UserController delegates to ValidationService instead of inline validation",
        "ValidationService accepts dependencies via constructor injection",
        "Tests pass"
      ],
      "log": null
    },
    {
      "id": 2,
      "title": "Write tests for ValidationService",
      "what": "Write unit tests for the ValidationService created in task 1. Test: (1) valid input passes validation, (2) invalid input raises appropriate errors with context, (3) dependency injection works (service uses injected dependencies), (4) edge cases for each validation rule.",
      "resolves": [],
      "effort": "low",
      "createsNewCode": false,
      "status": "pending",
      "acceptanceCriteria": [
        "Test file follows project test conventions",
        "At least 4 test cases covering happy path, errors, injection, and edge cases",
        "Tests pass"
      ],
      "log": null
    },
    {
      "id": 3,
      "title": "Replace bare except clauses in pipeline module",
      "what": "Replace all bare except/except Exception clauses in pipeline.py with specific exception types and add context to error messages including the triggering input values.",
      "resolves": ["pipeline.py:45", "pipeline.py:78"],
      "effort": "low",
      "createsNewCode": false,
      "status": "pending",
      "acceptanceCriteria": [
        "No bare except or except Exception clauses remain in pipeline.py",
        "Each except clause catches specific exception types",
        "Error messages include triggering input values",
        "Tests pass"
      ],
      "log": null
    }
  ]
}
```

Note: Task 1 (`createsNewCode: true`) has a paired test task (task 2) immediately after it. Task 3 (`createsNewCode: false`, restructuring-only) does not.

Field definitions:
- `testCommand` — project test command, discovered once and reused every iteration
- `scope` — repo-relative file paths from Phase 1, preserved for potential re-analysis. Use paths relative to the repository root (e.g., `src/pipeline.py` not `/Users/name/project/src/pipeline.py`) to avoid leaking local machine structure if the file is committed
- `createsNewCode` — `true` if the intervention creates new callable code (functions, classes, methods, services), `false` if it only restructures or fixes existing code. Determines whether a paired test task is generated
- `acceptanceCriteria` — derived from the intervention's What and Resolves fields. Each criterion should be concrete and verifiable (e.g., "ValidationService class exists with clear single responsibility" not "code is better")
- `status` — `"pending"` | `"in-progress"` | `"complete"` | `"failed"`
- `log` — `null` when pending, a string describing what was done (or what went wrong) when in-progress/complete/failed

**Step 4 — Implement tasks via Agent loop.**

Implement each task using sequential foreground Agent tool calls. Each iteration dispatches one task to an Agent that runs within this conversation, so the user sees every file read, edit, and test run in real time.

Set `MAX_ITER` to the number of tasks multiplied by 1.5 (rounded up) plus 1. For example, 10 tasks → 16 max iterations.

Execute the following loop. On each iteration:

1. Read the JSON task file. Count tasks with status `"pending"` or `"in-progress"`.
2. If none remain, the loop is done. Show final status:
   - If any tasks have status `"failed"`: "Done with failures: X/Y complete, Z failed"
   - Otherwise: "All Y tasks complete"
3. If `MAX_ITER` is reached, show "Max iterations (MAX_ITER) reached" and stop.
4. Show a progress header to the user: "Iteration X/MAX_ITER — N tasks remaining"
5. Use the **Agent tool** (foreground, not background) with the **Task Implementation Prompt** below, substituting the actual task file path.
6. After the Agent returns, re-read the JSON task file. If the task that was in-progress was not updated (status is still `"in-progress"` with no log change), log a warning: "Agent did not update task status on iteration X, continuing" and proceed to the next iteration.
7. Repeat from step 1.

**IMPORTANT:** You MUST issue Agent tool calls **one at a time, sequentially**. Do NOT launch multiple Agent calls in parallel for task implementation — each task may depend on changes from the previous task. Wait for each Agent to complete before starting the next iteration.

#### Task Implementation Prompt

Use this prompt for each Agent tool call, replacing `TASK_FILE_PATH` with the actual path to the JSON task file from Step 3:

> You are implementing feedback-blocker interventions. Read the task file at TASK_FILE_PATH (JSON). Find the first task with status "in-progress" or "pending". Set it to "in-progress" and write the file immediately. Read the `what`, `resolves`, and `acceptanceCriteria` fields. Implement the change. Verify all acceptance criteria are met. Run tests using the `testCommand` from the task file. If tests pass, set status to "complete" with a log summary and commit. If tests fail, fix forward or set status to "failed" with a log. Commit. Implement exactly ONE task per iteration. IMPORTANT: When committing, stage only the source files you changed — do NOT stage the task file (TASK_FILE_PATH) or any docs/exec-plans/ files. These are metadata for loop tracking, not deliverables.

**Step 5 — Show final summary.**

After the loop completes (all tasks done or max iterations reached), read the JSON task file and show the user:
- A task status table: each task's id, title, and status
- Plan location: `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.feedback-blockers.md`
- Task file location: `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.feedback-blockers.json`
- If any tasks failed or remain pending: suggest re-running with `--resume`

### Option 2: Save plan and fix top intervention

- Write the full remediation plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.feedback-blockers.md` (where YYYY-MM-DD is today's date and `<run-id>` is the hex run ID) including scope, all findings, all interventions with details
- Implement intervention #1
- Run existing tests (check CLAUDE.md for the test command, fallback to `uv run pytest` or `npm test`) to verify nothing breaks
- If tests fail, fix forward or revert and explain what went wrong
- Present a before/after summary showing the improvement

### Option 3: Save full remediation plan

- Write the full remediation plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.feedback-blockers.md` (where YYYY-MM-DD is today's date and `<run-id>` is the hex run ID) including scope, all findings, all interventions with details and effort estimates
- Do NOT implement anything

### Option 4: Revise

- Ask the user for feedback (different focus area, scope change, alternative priorities, additional context)
- Revise the analysis based on their input
- Present the updated report and prompt with the same four options

---

## Guidelines

- **Read before judging.** Agents must read full files and understand context. A pattern that looks wrong in isolation may be correct in context.
- **Respect existing architecture.** Don't suggest rewriting in a different paradigm. Work within the project's style.
- **No gold-plating.** Every suggestion must solve a concrete, present problem. No "for the future" abstractions.
- **High confidence only.** Skip stylistic preferences and subjective observations. Every finding must cite file:line and explain concrete harm.
- **Verify before reporting.** Every finding must quote the actual code at the cited file:line. During Phase 3, re-read each cited location and discard any finding whose quoted code does not match what is in the file. Never report findings about content you have not verified exists.
- **Cross-pillar signals matter most.** When multiple agents flag the same location, that's where the highest leverage is.
- **Interventions must cover all critical and important findings.** Design interventions as coherent changes that resolve multiple findings at once — but never sacrifice coverage for brevity: if a critical or important finding doesn't fit into an existing intervention, create a new one. No finding left behind.
