---
name: reasoning-gaps
description: Analyze code for AI reasoning gaps — untyped signatures, implicit control flow, hidden state, missing docs, and structural complexity that prevent agents from tracing data flow and predicting behavior. Spawns 3 parallel specialist agents, merges findings, and produces a prioritized remediation plan. Use when AI agents keep misunderstanding code, making wrong edits, or needing excessive exploration to complete tasks.
user-invocable: true
argument-hint: "[file or directory path] [--scope changed|module|full|imports <file>]"
---

# AI Reasoning Gap Analysis

Analyze code for **AI reasoning gaps** — places where an AI agent cannot confidently trace data flow, predict runtime behavior, or orient itself in the codebase. Uses 3 parallel specialist agents, then synthesizes findings into a prioritized remediation plan with concrete interventions.

This is NOT a code quality review. Code can be well-written and still be opaque to AI reasoning. This skill answers: **"If an AI agent read this code, what would it get wrong?"**

**Target:** "$ARGUMENTS"

---

## Phase 1: Determine Scope (you do this)

Based on arguments and context, determine what files to analyze:

1. **If a specific file/directory is given** — collect those file paths
2. **If no arguments (DEFAULT)** — get only the files changed in the current PR branch: `git diff --name-only main...HEAD` plus any uncommitted changes via `git diff --name-only`. Exclude test files. This should typically yield 3-10 files. If it yields more than 15, ask the user to narrow scope.
3. **If `--scope module`** — collect all source files in the module/package containing the current directory
4. **If `--scope full`** — collect all source files in `src/` or the main package directory (warn: may be slow)
5. **If `--scope imports <file>`** — collect the specified file, all files it imports, and all files that import it. Use grep for import statements to trace the graph. Cap at 20 files; if more, ask the user to narrow scope.

Build a newline-separated list of absolute file paths. This is the **file list** you will pass to each agent.

Also read the project's CLAUDE.md if it exists — agents need project conventions for context.

---

## Phase 2: Spawn 3 Specialist Agents (ALL IN PARALLEL)

**CRITICAL REQUIREMENT:** You MUST launch all three agents in a SINGLE response message containing exactly 3 Agent tool calls. This is non-negotiable. Do NOT launch them one at a time. Do NOT wait for one to finish before launching the next. One message, three Agent tool calls, all concurrent.

Each agent receives the same file list but analyzes through a different lens. Each agent must READ the files itself (do not paste file contents into prompts).

### Agent 1: Type & Data Contract Analyst

Use the Agent tool with this prompt:

```
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
- **Dict-based data passing** — functions receiving `dict` and accessing string keys (`data["status"]`, `config["database"]["host"]`). The AI cannot know which keys exist, their types, or whether access will succeed. A Pydantic model or dataclass makes the shape explicit.
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

```
You are an implicit flow and state specialist. You evaluate whether an AI agent can PREDICT WHAT WILL HAPPEN when this code runs — specifically, behavior that is invisible from reading the code linearly.

PROJECT CONVENTIONS:
{paste relevant CLAUDE.md sections here}

FILES TO ANALYZE:
{paste the file list here}

Read each file and analyze for implicit flow and hidden state. Look for:

- **Decorator side effects** — decorators that modify function behavior beyond what the function body shows. `@cache` changes when the function body executes. `@retry` adds invisible retry loops. `@login_required` gates access. `@transaction.atomic` wraps in a transaction. Report what behavior an AI agent would MISS if it only read the function body.
- **Middleware/plugin chains** — request processing pipelines, middleware stacks, or plugin systems where execution order is configured elsewhere. An AI agent reading a handler doesn't know what ran before or after it.
- **Signal/event systems** — `signal.connect()`, event emitters, pub/sub patterns, webhook registrations. Calling a function triggers invisible handlers elsewhere. An AI agent modifying the emitter doesn't know who is listening.
- **Dynamic dispatch** — `getattr(obj, method_name)()`, `registry[name]()`, strategy patterns with string-based lookup, `importlib.import_module()`. An AI agent cannot determine which code will execute without tracing the runtime value.
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

```
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
7. **Identify Top 5 Interventions** — the 5 highest-impact changes, ranked by how many findings each one would resolve. Each intervention should be a coherent change (e.g., "Create a PipelineConfig Pydantic model" or "Add module docstrings to the auth package"). Prefer interventions that resolve findings across multiple dimensions.
8. **Final check** — re-read the merged report. Every finding must have a file:line that exists and code that matches. If you cannot verify a finding, drop it.

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

## Top 5 Interventions (ranked by impact)

### 1. [Intervention title]
**What:** [specific change — files to modify, models to create, annotations to add]
**Resolves:** [list of findings this addresses, by file:line]
**Why highest leverage:** [which dimensions improve and how]
**Effort:** low / medium / high

### 2. ...

### 3. ...

### 4. ...

### 5. ...
```

---

## Phase 4: Propose (you do this)

After presenting the merged report, briefly explain the Top 5 Interventions with trade-offs for each. Then prompt the user:

> **What would you like to do?**
> 1. **Save plan and fix top intervention** — Write the full remediation plan to `docs/plans/reasoning-gaps-<short-description>.md` and implement intervention #1
> 2. **Save full remediation plan** — Write the plan to `docs/plans/` for incremental work
> 3. **Revise** — Provide feedback to refine the analysis or change focus

### Option 1: Save plan and fix top intervention

- Write the full remediation plan to `docs/plans/reasoning-gaps-<short-description>.md` including scope, all findings, all 5 interventions with details
- Implement intervention #1
- Run existing tests (check CLAUDE.md for the test command, fallback to `uv run pytest` or `npm test`) to verify nothing breaks
- If tests fail, fix forward or revert and explain what went wrong
- Present a before/after summary showing the AI-readability improvement

### Option 2: Save full remediation plan

- Write the full remediation plan to `docs/plans/reasoning-gaps-<short-description>.md` including scope, all findings, all 5 interventions with details and effort estimates
- Do NOT implement anything

### Option 3: Revise

- Ask the user for feedback (different focus area, scope change, alternative priorities, additional context)
- Revise the analysis based on their input
- Present the updated report and prompt with the same three options

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
- **Interventions over individual fixes.** The Top 5 Interventions should be designed as coherent changes that resolve multiple findings at once, not one finding per intervention. "Add a PipelineConfig Pydantic model" resolves 12 type-gap findings in one change.
- **This is not a code quality review.** Code can be well-written and still opaque to AI reasoning. A clean, idiomatic function with no type annotations is a reasoning gap. An ugly function with full type annotations and a clear docstring is AI-readable.
