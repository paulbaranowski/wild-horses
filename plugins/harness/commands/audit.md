---
description: Analyze code for encapsulation, OOP design, testability, and harness-friendliness (agent feedback loops). Spawns 4 parallel specialist agents, merges findings, and proposes the highest-impact refactor. Use when you want to improve code quality for maintainability and agent-assisted development.
argument-hint: "[file or directory path] [--scope changed|module|full]"
---

# Harness Engineering Review

Analyze code for **encapsulation**, **OOP design**, **testability**, and **harness-friendliness** (code that enables tight feedback loops for agents and automated tooling). Uses 4 parallel specialist agents, then synthesizes findings and proposes the single highest-impact refactor.

**Target:** "$ARGUMENTS"

---

## Phase 1: Determine Scope (you do this)

Based on arguments and context, determine what files to analyze:

1. **If a specific file/directory is given** — collect those file paths
2. **If no arguments (DEFAULT)** — get only the files changed in the current PR branch: `git diff --name-only main...HEAD` plus any uncommitted changes via `git diff --name-only`. Exclude test files and non-Python files. This should typically yield 3-10 files. If it yields more than 15, ask the user to narrow scope.
3. **If `--scope module`** — collect all source files in the module/package containing the current directory
4. **If `--scope full`** — collect all source files in `src/` or the main package directory (warn: may be slow)

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
- **Missing boundary validation** — constructors (__init__) or factory methods that accept invalid state. Can you create an instance that violates the class's assumptions?
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
- **Missing polymorphism** — long if/elif chains or isinstance() checks dispatching on type. These often indicate a missing base class or protocol with polymorphic methods.
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
- **Hidden side effects** — functions that read/write files, make network calls, access databases, or mutate global state without this being obvious from the signature. The caller cannot predict or control these effects.
- **Non-determinism** — use of datetime.now(), time.time(), random, uuid, or os.environ reads without injection points. Tests become flaky or require monkeypatching.
- **Large indivisible units** — functions over ~40 lines that do multiple sequential things (fetch, transform, validate, persist). You cannot test one step without running them all.
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
- **Large blast radius** — changing one behavior requires touching many files. Look for: a single constant used across 5+ files, tightly coupled modules that import each other's internals, changes that require coordinated updates. Well-factored code lets an agent change one thing in one place.
- **Missing observability** — functions that take input and produce output with no way to inspect intermediate state. No logging, no debug methods, no way to see what happened inside when the output is wrong. Look for complex multi-step functions with no intermediate visibility.
- **Implicit contracts** — behavior that depends on ordering (must call A before B), naming conventions (file must be named X), global state (reads from module-level variable), or undocumented type expectations. Agents work best with explicit, typed, enforced interfaces.
- **Non-incremental design** — code that must be understood as a whole to be modified safely. A function where you must read all 100 lines to change line 50. A class where methods have hidden interdependencies. Agents work best with local reasoning.
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
4. **Cross-pillar findings are gold** — when the same code location appears in 2+ agent reports, flag it prominently. These are the highest-leverage fixes because one change improves multiple dimensions.
5. **Compute overall score** — weighted average: Encapsulation 20%, OOP Design 20%, Testability 30%, Harness-Friendliness 30% (testability and harness-friendliness weighted higher because they directly affect development velocity)
6. **Identify the Highest-Impact Refactor** — the single change that appears across the most pillars or addresses the highest-severity finding. Prefer changes that improve testability AND harness-friendliness simultaneously.
7. **Final check** — re-read the merged report. Every finding must have a file:line that exists and code that matches. If you cannot verify a finding, drop it.

Present the merged report:

```markdown
# Harness Engineering Review

## Scope
[What was analyzed and why]

## Ratings Summary
| Pillar | Score | One-line summary |
|--------|-------|-----------------|
| Encapsulation | X/10 | ... |
| OOP Design | X/10 | ... |
| Testability | X/10 | ... |
| Harness-Friendliness | X/10 | ... |
| **Overall** | **X/10** | ... |

## Cross-Pillar Findings (highest leverage)
- [file:line] Description — affects: [list of pillars] — why this matters

## Findings by Severity

### Critical
- [file:line] Description — [pillar] — concrete harm

### Important
- [file:line] Description — [pillar] — impact

### Minor
- [file:line] Description — [pillar]

## Highest-Impact Refactor
**What:** [the change]
**Why highest leverage:** [which pillars it improves and why]
**Affected files:** [list]
```

---

## Phase 4: Propose (you do this)

After presenting the merged report, explain the **Highest-Impact Refactor**:

1. What you would change (files affected, new structure)
2. The trade-offs (what gets better, what gets more complex, any breaking changes)
3. How testability or harness-friendliness specifically improves

Then prompt the user with these three options:

> **What would you like to do?**
> 1. **Save plan and implement** -- Write the plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-harness-review-<short-description>.md` and start implementing it
> 2. **Save plan** -- Write the plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-harness-review-<short-description>.md` for later
> 3. **Revise** -- Provide feedback to refine the proposal

Before executing any option below, generate a **run ID** by running `openssl rand -hex 2` to produce a 4-character hex string (e.g., `a3f2`). Use this same run ID in all file names produced by this run — this prevents collisions when the command is run multiple times on the same day.

### Option 1: Save plan and implement

- Write the full refactor plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-harness-review-<short-description>.md` (where YYYY-MM-DD is today's date and `<run-id>` is the hex run ID) including scope, findings, proposed changes, affected files, and trade-offs
- Implement the change
- Run existing tests (check CLAUDE.md for the test command, fallback to `uv run pytest` or `npm test`) to verify nothing breaks
- If tests fail, fix forward or revert and explain what went wrong
- Present a before/after summary showing the improvement

### Option 2: Save plan

- Write the full refactor plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-harness-review-<short-description>.md` (where YYYY-MM-DD is today's date and `<run-id>` is the hex run ID) including scope, findings, proposed changes, affected files, and trade-offs
- Do NOT implement anything

### Option 3: Revise

- Ask the user for their feedback on the proposal (e.g., different focus area, scope change, alternative approach, additional constraints)
- Revise the Highest-Impact Refactor based on their input
- Present the updated proposal and prompt with the same three options again

---

## Guidelines

- **Read before judging.** Agents must read full files and understand context. A pattern that looks wrong in isolation may be correct in context.
- **Respect existing architecture.** Don't suggest rewriting in a different paradigm. Work within the project's style.
- **One refactor at a time.** Phase 4 proposes exactly ONE change. Run the skill again for more.
- **No gold-plating.** Every suggestion must solve a concrete, present problem. No "for the future" abstractions.
- **High confidence only.** Skip stylistic preferences and subjective observations. Every finding must cite file:line and explain concrete harm.
- **Verify before reporting.** Every finding must quote the actual code at the cited file:line. During Phase 3, re-read each cited location and discard any finding whose quoted code does not match what is in the file. Never report findings about content you have not verified exists.
- **Cross-pillar signals matter most.** When multiple agents flag the same location, that's where the highest leverage is.
