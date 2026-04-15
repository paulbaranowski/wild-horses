---
description: Analyze code for AI reasoning gaps — untyped signatures, implicit control flow, hidden state, missing docs, and structural complexity that prevent agents from tracing data flow and predicting behavior. Spawns 3 parallel specialist agents, merges findings, and produces a prioritized remediation plan. Use when AI agents keep misunderstanding code, making wrong edits, or needing excessive exploration to complete tasks.
argument-hint: "[path or description] [--full] [--resume [task-file-path]]"
---

# AI Reasoning Gap Analysis

Analyze code for **AI reasoning gaps** — places where an AI agent cannot confidently trace data flow, predict runtime behavior, or orient itself in the codebase. Uses 3 parallel specialist agents, then synthesizes findings into a prioritized remediation plan with concrete interventions.

This is NOT a code quality review. Code can be well-written and still be opaque to AI reasoning. This skill answers: **"If an AI agent read this code, what would it get wrong?"**

**Target:** "$ARGUMENTS"

---

## Resume Check (before Phase 1)

If `$ARGUMENTS` contains `--resume`, skip all analysis and restart the loop from an existing task file:

1. **Locate the task file:**
   - If a path follows `--resume` (e.g., `--resume docs/exec-plans/active/2026-04-14-a3f2-user-endpoints.reasoning-gaps.json`): read that file directly. If the path ends in `.reasoning-gaps.md`, read its YAML frontmatter `task_file` field, then validate the pointer: the path must exist, be readable, parse as valid JSON, and contain at least one task with `status` `"pending"` or `"in-progress"`. If any check fails, report a clear error (e.g., "task_file points to X which does not exist" or "JSON at X has no pending tasks") and stop. Otherwise, open the validated JSON file.
   - If no path provided (just `--resume`): scan `docs/exec-plans/active/*.reasoning-gaps.json` for files with any task where `status` is `"pending"` or `"in-progress"`. If no JSON matches, fall back to scanning `docs/exec-plans/active/*.reasoning-gaps.md` — for each `.md` candidate, read its YAML frontmatter `task_file` field, then validate the pointer: the path must exist, be readable, parse as valid JSON, and contain at least one task with `status` `"pending"` or `"in-progress"`. Discard any candidate that fails any of these checks.
     - If exactly one validated match (from either scan): use it.
     - If multiple validated matches: list them with progress summaries (complete/pending/failed counts) and ask the user to pick one.
     - If no validated matches: report "No in-progress reasoning-gaps task files found" and stop.

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
3. **If no arguments (DEFAULT)** — get only the files changed in the current PR branch: `git diff --name-only main...HEAD` plus any uncommitted changes via `git diff --name-only`. Exclude test files. This should typically yield 3-10 files. If it yields more than 15, ask the user to narrow scope.
4. **If `--full`** — collect all source files in `src/` or the main package directory (warn: may be slow)

Build a newline-separated list of absolute file paths. This is the **file list** you will pass to each agent.

Also read the project's CLAUDE.md if it exists — agents need project conventions for context.

---

## Phase 2: Spawn 3 Specialist Agents (ALL IN PARALLEL)

**CRITICAL REQUIREMENT:** You MUST launch all three agents in a SINGLE response message containing exactly 3 Agent tool calls. This is non-negotiable. Do NOT launch them one at a time. Do NOT wait for one to finish before launching the next. One message, three Agent tool calls, all concurrent.

Each agent receives the same file list but analyzes through a different lens. Each agent must READ the files itself (do not paste file contents into prompts).

### Agent 1: Type & Data Contract Analyst

Use the Agent tool with this prompt:

```text
You are a type and data contract specialist. You evaluate whether an AI agent can determine WHAT DATA flows through this code — what types functions accept, what they return, what shape data has at each point.

PROJECT CONVENTIONS:
{paste relevant CLAUDE.md sections here}

FILES TO ANALYZE:
{paste the file list here}

Read each file and analyze for type and data contract gaps. Look for:

- **Untyped function parameters** — function arguments with no type hints. An AI agent reading a caller of this function cannot determine what to pass without reading the function body. Prioritize public/exported functions and methods over private helpers.
- **Missing return type annotations** — functions with no return type hint. An AI agent reading a caller cannot determine what the function returns without reading the entire function body. Especially harmful when the function has multiple return paths.
- **`Any` type usage** — explicit or implicit `Any` that kills type narrowing. An AI agent cannot trace data flow through an `Any` boundary.
- **Untyped containers** — `list`, `dict`, `tuple`, `set` without element types. `list` tells the AI nothing; `list[UserConfig]` tells it everything.
- **Dict-based data passing** — functions receiving `dict` and accessing string keys (`data["status"]`, `config["database"]["host"]`). The AI cannot know which keys exist, their types, or whether access will succeed. A Pydantic model or dataclass makes the shape explicit. CAUTION: `getattr(obj, dynamic_key)` and `hasattr(obj, dynamic_key)` are NOT valid fixes — they move dynamic lookup from dict to attribute access or existence checks. The AI still cannot trace which attribute is accessed or validated, and cannot verify it exists at the type level. Fix with a typed method (e.g., `obj.get_calendar_id(provider)`) or a typed mapping (e.g., `dict[Provider, CalendarId]`) that encapsulates the lookup.
- **Stringly-typed interfaces** — status codes as strings (`status = "active"`), event names as strings (`emit("user_created")`), action types as strings. An AI agent cannot enumerate valid values. Enums make the domain explicit.
- **Functions returning different types by code path** — functions that return `str` in one branch, `None` in another, `dict` in a third, without a Union type annotation. The AI cannot predict what the caller receives.
- **Missing validation at boundaries** — public API functions, CLI entry points, or data ingestion functions that accept unvalidated input. No Pydantic model, no dataclass, no manual validation. An AI agent cannot determine what invariants hold after the boundary.
- **Implicit schemas** — database queries, API calls, or file parsers that imply data structure without typed models. The AI must read the query/response to guess the shape.

For each finding, report:
- Severity: critical / important / minor
- Category tag: `type-gap` or `data-contract`
- File path and line number
- Actual code (quote the exact lines — verbatim, not paraphrased)
- AI reasoning harm: specifically what an AI agent CANNOT DETERMINE when it reads a caller or consumer of this code
- Concrete fix: the specific type annotation, model, or enum to add, referencing what callers need to know

Severity calibration:
- **critical**: An AI agent WILL make a wrong edit because it cannot determine the data shape (e.g., untyped public API return used by 3+ callers)
- **important**: An AI agent will STRUGGLE and may guess wrong (e.g., dict-based config accessed in multiple files)
- **minor**: An AI agent will be SLOWED but can figure it out by reading the implementation (e.g., untyped private helper)

End with a rating: `Type & Data Contracts: X/10` with a one-line justification.

Format your response as:
## Type & Data Contract Analysis

### Rating: X/10
[one-line justification]

### Findings
#### Critical
- [file:line] `category-tag` description — AI reasoning harm — concrete fix

#### Important
- [file:line] `category-tag` description — AI reasoning harm — concrete fix

#### Minor
- [file:line] `category-tag` description — AI reasoning harm — concrete fix

IMPORTANT: Prioritize findings at module boundaries, public APIs, and cross-file interfaces. A private helper with loose types is less harmful than a public function with loose types. Every finding must cite a specific file:line and explain what an AI agent cannot determine.
```

### Agent 2: Implicit Flow & State Analyst

Use the Agent tool with this prompt:

```text
You are an implicit flow and state specialist. You evaluate whether an AI agent can PREDICT WHAT WILL HAPPEN when this code runs — specifically, behavior that is invisible from reading the code linearly.

PROJECT CONVENTIONS:
{paste relevant CLAUDE.md sections here}

FILES TO ANALYZE:
{paste the file list here}

Read each file and analyze for implicit flow and hidden state. Look for:

- **Decorator side effects** — decorators that modify function behavior beyond what the function body shows. `@cache` changes when the function body executes. `@retry` adds invisible retry loops. `@login_required` gates access. `@transaction.atomic` wraps in a transaction. Report what behavior an AI agent would MISS if it only read the function body.
- **Middleware/plugin chains** — request processing pipelines, middleware stacks, or plugin systems where execution order is configured elsewhere. An AI agent reading a handler doesn't know what ran before or after it.
- **Signal/event systems** — `signal.connect()`, event emitters, pub/sub patterns, webhook registrations. Calling a function triggers invisible handlers elsewhere. An AI agent modifying the emitter doesn't know who is listening.
- **Dynamic dispatch and dynamic attribute access** — `getattr(obj, method_name)()`, `getattr(obj, key)` (attribute lookup, not just calls), `hasattr(obj, key)` (attribute existence check), `registry[name]()`, strategy patterns with string-based lookup, `importlib.import_module()`. An AI agent cannot determine which code will execute or which attribute is accessed without tracing the runtime value. `getattr(obj, dynamic_key)` and `hasattr(obj, dynamic_key)` are especially harmful when they appear as a "fix" for dict-based access — they are equally opaque. Recommend a typed method that encapsulates the lookup.
- **Magic methods with non-obvious behavior** — `__getattr__`, `__getattribute__`, `__call__`, `__init_subclass__`, `__class_getitem__`, `__set_name__`. These alter how attribute access, instantiation, or subclassing works in ways that an AI agent reading normal code would not predict.
- **Import-time side effects** — modules that register handlers, populate registries, modify global state, or configure systems when imported. An AI agent adding or removing an import doesn't realize it changes runtime behavior.
- **Metaclasses** — classes using metaclasses that modify class creation behavior. An AI agent reading the class definition sees something different from what is actually created.
- **Global mutable state** — module-level lists, dicts, sets, or objects that are mutated at runtime by functions or methods. An AI agent cannot determine the current state without tracing all mutation points.
- **Methods that mutate self as hidden side effect** — methods whose name suggests they read/query/compute (e.g., `get_user`, `validate`, `to_dict`) but also mutate instance state. An AI agent calling these methods doesn't expect side effects.
- **Thread-local or context-var state** — `threading.local()`, `contextvars.ContextVar`, Flask's `g` or `request`. State that varies by execution context, invisible from the function signature.
- **Property setters with side effects** — `@property` setters that do more than assign a value (trigger validation, emit events, update other attributes, write to database). `obj.name = "x"` looks like a simple assignment but triggers hidden behavior.

For each finding, report:
- Severity: critical / important / minor
- Category tag: `implicit-flow` or `state-mutation`
- File path and line number
- Actual code (quote the exact lines — verbatim, not paraphrased)
- Hidden behavior: specifically what happens at runtime that an AI agent would NOT predict from reading the code linearly
- How to make it explicit: concrete recommendation (e.g., "Add inline comment documenting retry behavior" or "Replace decorator with explicit wrapper to make retry visible" or "Add type annotation to registry: dict[str, Callable[[Request], Response]]")

Severity calibration:
- **critical**: An AI agent WILL break something because it doesn't know about the hidden behavior (e.g., decorator that changes return type, global state mutated by common function)
- **important**: An AI agent will produce INCOMPLETE changes because it missed a hidden connection (e.g., signal handler it didn't know about, middleware that transforms the input)
- **minor**: An AI agent will be CONFUSED but unlikely to break things (e.g., cosmetic decorator, well-contained thread-local usage)

End with a rating: `Implicit Flow & State: X/10` with a one-line justification.

Format your response as:
## Implicit Flow & State Analysis

### Rating: X/10
[one-line justification]

### Findings
#### Critical
- [file:line] `category-tag` description — hidden behavior — how to make explicit

#### Important
- [file:line] `category-tag` description — hidden behavior — how to make explicit

#### Minor
- [file:line] `category-tag` description — hidden behavior — how to make explicit

IMPORTANT: This is about INVISIBLE BEHAVIOR, not code quality. A decorator that only adds logging is minor. A decorator that changes the function's return type, adds caching that affects correctness, or gates access is critical. Rate by how likely an AI agent is to make a WRONG EDIT because it didn't know about the hidden behavior.
```

### Agent 3: Structure & Documentation Analyst

Use the Agent tool with this prompt:

```text
You are a structure and documentation specialist. You evaluate whether an AI agent can ORIENT ITSELF — understand what a file does, how it fits in the system, and navigate the codebase structure.

PROJECT CONVENTIONS:
{paste relevant CLAUDE.md sections here}

FILES TO ANALYZE:
{paste the file list here}

Read each file and analyze for structural and documentation gaps. Look for:

- **Missing module-level docstrings** — Python files with no docstring at the top. An AI agent opening this file has no summary of its purpose, responsibilities, or role in the system. It must read the entire file to understand what it does. Report what the docstring SHOULD say (not just "missing docstring").
- **Missing class docstrings** — classes with no docstring explaining purpose, responsibilities, and key collaborators. An AI agent cannot determine whether this class is the right place to make a change without reading all its methods.
- **Missing "why" comments on non-obvious logic** — complex conditionals, magic numbers, regex patterns, workarounds, business rules, or edge case handling with no comment explaining WHY. An AI agent seeing `if x > 42` cannot determine whether 42 is arbitrary, a business rule, or a performance threshold.
- **Undocumented protocols/interfaces** — components that expect objects to have certain methods/attributes without an ABC, Protocol, or TypedDict definition. An AI agent implementing a new provider/handler doesn't know what methods it must have.
- **Long functions (>50 lines)** — functions that do multiple things in sequence. An AI agent must read the entire function to understand any part. Report the distinct responsibilities and suggest decomposition.
- **Deep nesting (>4 levels)** — functions with deeply nested if/for/try/with blocks. An AI agent must hold all branch conditions in context to understand the innermost code. Suggest early returns or extraction.
- **Circular imports** — files that import from each other, directly or through a short chain. An AI agent's mental model of the dependency graph breaks, making it hard to predict the impact of changes. Check for `from X import Y` where X also imports from the current module.
- **Convention-over-configuration** — behavior determined by file naming, directory structure, or naming conventions without explicit registration or documentation. Django auto-discovery, pytest naming, Flask blueprints. An AI agent doesn't know that renaming a file changes runtime behavior unless this is documented.

For each finding, report:
- Severity: critical / important / minor
- Category tag: `documentation` or `structural`
- File path and line number (or file path for file-level findings)
- For code issues: actual code (quote verbatim). For missing documentation: describe what is missing and what it should say.
- AI orientation impact: how this gap affects an AI agent's ability to understand the file's role, navigate the codebase, or make safe changes
- Concrete fix: the specific docstring content, comment text, or decomposition to apply

Severity calibration:
- **critical**: An AI agent CANNOT DETERMINE the file's purpose or a class's responsibility, OR a structural issue forces reading 100+ lines to make a local change (e.g., entry-point file with no module docstring, 80-line function with 5 responsibilities)
- **important**: An AI agent will MISUNDERSTAND the code's role or relationships (e.g., missing "why" on a business rule it might "fix", undocumented protocol with 3+ implementations)
- **minor**: An AI agent will be SLOWED but can figure it out (e.g., missing docstring on a small, well-named class; 55-line function that is mostly sequential)

End with a rating: `Structure & Documentation: X/10` with a one-line justification.

Format your response as:
## Structure & Documentation Analysis

### Rating: X/10
[one-line justification]

### Findings
#### Critical
- [file:line] `category-tag` description — AI orientation impact — concrete fix

#### Important
- [file:line] `category-tag` description — AI orientation impact — concrete fix

#### Minor
- [file:line] `category-tag` description — AI orientation impact — concrete fix

IMPORTANT: For documentation findings, be SPECIFIC about what should be documented. "Missing module docstring" is not a finding. "This module needs a docstring explaining it serves as the authentication middleware layer, processing JWT tokens before requests reach route handlers" IS a finding. For structural findings, suggest specific decomposition.
```

---

## Phase 3: Merge and Report (you do this, after all 3 agents complete)

Wait for all 3 agents to return. Then synthesize their findings into a unified report:

1. **Verify every finding before including it.** For each finding from each agent:
   - Read the cited file at the cited line number
   - Confirm the quoted code in the finding matches what is actually in the file
   - If the finding references content that does not exist at the cited location, **discard it silently** — do not include it in the report
   - If the description is slightly inaccurate but the underlying issue is real, correct the description
2. **Collect ratings** from each agent into the summary table
3. **Deduplicate findings** — if two agents flagged the same file:line, merge into one finding noting which dimensions it affects (this is a signal of high impact)
4. **Cross-dimension findings are gold** — when the same code location appears in 2+ agent reports, flag it prominently. These are the highest-leverage fixes because one change improves multiple dimensions. Example: an untyped function that is also a 60-line monolith with no docstring — one refactor (decompose, type, document) fixes three problems.
5. **Compute overall score** — weighted average: Type & Data Contracts 35%, Implicit Flow & State 35%, Structure & Documentation 30%.
6. **Assign letter grade**:
   - 9-10: **A** — AI agents can reason confidently about this code
   - 7-8: **B** — minor friction, agents will occasionally struggle
   - 5-6: **C** — significant gaps, agents will make mistakes
   - 3-4: **D** — agents cannot reliably reason about this code
   - 1-2: **F** — opaque to AI reasoning
7. **Create interventions with full coverage** — generate a ranked list of interventions that **collectively cover every critical, important, and minor finding**. Do NOT cap at a fixed number. If 3 interventions cover everything, list 3. If 8 are needed, list 8. Rank by impact: cross-dimension findings first, then by number of findings resolved. Each intervention should be a coherent change (e.g., "Create a PipelineConfig Pydantic model" or "Add module docstrings to the auth package"). Prefer interventions that resolve findings across multiple dimensions. For each intervention, determine whether it **creates new callable code** (new functions, classes, methods, models, enums with methods) or **annotates/documents existing code** (type hints, docstrings, comments, renames). Tag each with `createsNewCode: true` or `createsNewCode: false`.
8. **Coverage verification** — after creating the intervention list, verify that every critical, important, and minor finding from the Findings by Severity section maps to at least one intervention's "Resolves" list. If any critical, important, or minor finding is uncovered, add an intervention for it. This step is non-negotiable — no critical, important, or minor finding may exist in the report without a corresponding intervention.
9. **Generate paired test tasks** — for each intervention where `createsNewCode` is `true`, generate a companion test task placed immediately after it in the task ordering. The test task must:
   - Have a title prefixed with "Write tests for" followed by the name of what was created
   - Have a `what` field that specifies: (a) the exact new functions/classes/methods to test, (b) specific test cases to write (at minimum: happy path, edge cases, and error handling), (c) where to put the test file (follow existing project test conventions)
   - Set `createsNewCode: false` (it does not create new production code)
   - Set `resolves: []` (it supports the preceding implementation task, not a finding)
   - Have concrete `acceptanceCriteria` specifying minimum test count and coverage areas
   - Set `effort: "low"`
   Do NOT generate test tasks for interventions that only add type annotations, docstrings, comments, or renames. These are verified by their own acceptance criteria.
10. **Final check** — re-read the merged report. Every finding must have a file:line that exists and code that matches. If you cannot verify a finding, drop it.

Present the merged report:

```markdown
# AI Reasoning Gap Analysis

## Scope
[What was analyzed and why]

## Ratings Summary
| Dimension | Score | One-line summary |
|-----------|-------|------------------|
| Type & Data Contracts | X/10 | ... |
| Implicit Flow & State | X/10 | ... |
| Structure & Documentation | X/10 | ... |
| **Overall** | **X/10 (Grade)** | ... |

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

**Step 2 — Write the markdown report** to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.reasoning-gaps.md` (where YYYY-MM-DD is today's date and `<run-id>` is the 4-character hex run ID).

Use YAML frontmatter for metadata:

```yaml
---
status: in-progress
task_file: "docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.reasoning-gaps.json"
generated: "YYYY-MM-DDTHH:MM:SSZ"
---
```

The body contains the full report: Scope (with repo-relative file paths), Ratings Summary, Cross-Dimension Findings, Findings by Severity, Interventions (with full details), and Coverage Check. This is the human-readable artifact — the Agent loop does NOT modify this file.

**Step 3 — Write the JSON task file** to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.reasoning-gaps.json`.

This is the machine-readable task list that the Agent loop reads and writes for state tracking. Convert the absolute file paths from Phase 1 to repo-relative paths for the `scope` array (strip the repository root prefix — e.g., `/Users/name/project/src/pipeline.py` becomes `src/pipeline.py`). Extract each intervention into a task. For interventions tagged `createsNewCode: true`, place a paired test task immediately after:

```json
{
  "plan": "docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.reasoning-gaps.md",
  "testCommand": "<discovered test command>",
  "scope": ["<repo-relative file paths from Phase 1>"],
  "tasks": [
    {
      "id": 1,
      "title": "Create PipelineConfig Pydantic model",
      "what": "Extract dict-based config access into a PipelineConfig Pydantic model with typed fields for host (str), port (int), timeout (float). Replace all config['host'] style access in pipeline.py.",
      "resolves": ["pipeline.py:23", "pipeline.py:45", "pipeline.py:67"],
      "effort": "medium",
      "createsNewCode": true,
      "status": "pending",
      "acceptanceCriteria": [
        "PipelineConfig model exists with typed fields for host, port, and timeout",
        "All config dict access in pipeline.py replaced with model attribute access",
        "Tests pass"
      ],
      "log": null
    },
    {
      "id": 2,
      "title": "Write tests for PipelineConfig model",
      "what": "Write unit tests for the PipelineConfig model created in task 1. Test: (1) valid construction with all required fields, (2) default values for optional fields, (3) type validation rejects invalid input (string for port), (4) pipeline functions that consume the model work correctly.",
      "resolves": [],
      "effort": "low",
      "createsNewCode": false,
      "status": "pending",
      "acceptanceCriteria": [
        "Test file follows project test conventions",
        "At least 4 test cases covering construction, defaults, validation, and integration",
        "Tests pass"
      ],
      "log": null
    },
    {
      "id": 3,
      "title": "Add type annotations to auth module",
      "what": "Add parameter and return type annotations to all public functions in auth.py",
      "resolves": ["auth.py:12", "auth.py:34", "auth.py:56"],
      "effort": "low",
      "createsNewCode": false,
      "status": "pending",
      "acceptanceCriteria": [
        "All public functions in auth.py have parameter and return type annotations",
        "Tests pass"
      ],
      "log": null
    }
  ]
}
```

Note: Task 1 (`createsNewCode: true`) has a paired test task (task 2) immediately after it. Task 3 (`createsNewCode: false`, annotation-only) does not.

Field definitions:
- `testCommand` — project test command, discovered once and reused every iteration
- `scope` — repo-relative file paths from Phase 1, preserved for potential re-analysis. Use paths relative to the repository root (e.g., `src/pipeline.py` not `/Users/name/project/src/pipeline.py`) to avoid leaking local machine structure if the file is committed
- `createsNewCode` — `true` if the intervention creates new callable code (functions, classes, methods, models), `false` if it only annotates or documents existing code. Determines whether a paired test task is generated
- `acceptanceCriteria` — derived from the intervention's What and Resolves fields. Each criterion should be concrete and verifiable (e.g., "PipelineConfig model exists with typed fields for host, port, and timeout" not "types are added")
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

> You are implementing reasoning-gap interventions. Read the task file at TASK_FILE_PATH (JSON). Find the first task with status "in-progress" or "pending". Set it to "in-progress" and write the file immediately. Read the `what`, `resolves`, and `acceptanceCriteria` fields. Implement the change. Verify all acceptance criteria are met. Run tests using the `testCommand` from the task file. If tests pass, set status to "complete" with a log summary and commit. If tests fail, fix forward or set status to "failed" with a log. Commit. Implement exactly ONE task per iteration. IMPORTANT: When committing, stage only the source files you changed — do NOT stage the task file (TASK_FILE_PATH) or any docs/exec-plans/ files. These are metadata for loop tracking, not deliverables.

**Step 5 — Show final summary.**

After the loop completes (all tasks done or max iterations reached), read the JSON task file and show the user:
- A task status table: each task's id, title, and status
- Plan location: `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.reasoning-gaps.md`
- Task file location: `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.reasoning-gaps.json`
- If any tasks failed or remain pending: suggest re-running with `--resume`

### Option 2: Save plan and fix top intervention

- Write the full remediation plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.reasoning-gaps.md` (where YYYY-MM-DD is today's date and `<run-id>` is the hex run ID) including scope, all findings, all interventions with details
- Implement intervention #1
- Run existing tests (check CLAUDE.md for the test command, fallback to `uv run pytest` or `npm test`) to verify nothing breaks
- If tests fail, fix forward or revert and explain what went wrong
- Present a before/after summary showing the AI-readability improvement

### Option 3: Save full remediation plan

- Write the full remediation plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.reasoning-gaps.md` (where YYYY-MM-DD is today's date and `<run-id>` is the hex run ID) including scope, all findings, all interventions with details and effort estimates
- Do NOT implement anything

### Option 4: Revise

- Ask the user for feedback (different focus area, scope change, alternative priorities, additional context)
- Revise the analysis based on their input
- Present the updated report and prompt with the same four options

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
