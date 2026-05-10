# wild-horses

A [Claude Code](https://claude.ai/code) plugin marketplace for making code AI-readable and agent-friendly.

## Overview

Three questions an AI agent has to be able to answer before it can edit your code reliably:

1. _Does the type layer tell the truth?_ — answered by `/pyright:run-and-fix` (Python only).
2. _If an AI agent read this code, what would it get wrong?_ — answered by `/harness:reasoning-gaps`.
3. _Can an AI edit this code and know whether it got it right?_ — answered by `/harness:feedback-blockers`.

Run them in that order on a PR or feature branch. Each step asks a harder question than the last: types, then comprehension, then verification. A prerequisite step — does the repo have a map for the agent to read at all? — is handled separately by `/harness:setup`.

## Plugins

### pyright

Run pyright on a Python codebase and fix its findings using a documented playbook of fix patterns. A natural precursor to `/harness:reasoning-gaps`: pyright rigorously resolves the typing dimension that reasoning-gaps would otherwise analyze heuristically, so your reasoning-gaps run can focus on the implicit control flow and documentation axes that only it sees.

```text
/plugin install pyright@wild-horses
```

#### /pyright:run-and-fix

**Core question:** _Does the type layer tell the truth?_

**Python-specific.** This is the one plugin in the marketplace that targets a single language; everything else is language-agnostic. Type-annotated code gives AI agents a trustworthy "what does this function accept and return" signal without reading the body or every caller. Pyright catches the annotation gaps; this command fixes them — and, critically, knows when _not_ to fix them: it suppresses cases where the right resolution is a semantically loaded design decision, and flags cases where pyright has uncovered a real runtime bug rather than a type-system gripe.

Detects the project's pyright config (`[tool.pyright]` in `pyproject.toml` or `pyrightconfig.json`), runs pyright, triages by rule and file, and applies fixes from a documented playbook. Three fix intents shape how fixes are written:

| Intent          | Lean                                                                                                                                                                                                                                                         |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`silence`**   | Rule-specific suppressions + `cast()` at boundaries. Fastest to zero, small diff, many suppressions. Still flags real bugs.                                                                                                                                  |
| **`improve`**   | Widen over coerce, annotate over cast, extract factories, and extract TypedDict/Pydantic models from opaque `dict[str, Any]` with repeated key reads. Pauses on semantically loaded decisions (e.g., `bool \| None → bool` collapses). Larger diff, durable. |
| **`bugs-only`** | Fixes only real bugs (wrong attribute reads, shadowed methods, dead references). Suppresses the rest with a `# TODO(types): revisit under --intent improve` marker. Zero type churn, grep-able follow-up list.                                               |

The playbook is split across three pattern files, each keyed by a discoverable signal: `rules.md` (pyright-rule recipes — `reportOptionalMemberAccess`, `reportArgumentType`, TypedDict ↔ dict asymmetry, pydantic v1/v2 field renames, opaque `dict[str, Any]` extraction, …), `libraries.md` (library-stub workarounds for bitstring, scipy, tornado, matplotlib, Beanie, Supabase, litellm, pymongo, pydantic, …), and `bugs.md` (patterns where pyright has caught a real runtime bug, not a type-system gripe).

For codebases with ≥ 20 errors, fixes are parallelized across agents with disjoint file partitions. After each parallel dispatch, a consolidation pass detects cross-partition repetition (≥ 10 suppressions of the same rule, ≥ 5 casts to the same target type) — those clusters often point to a single upstream fix that erases dozens of downstream suppressions.

Supports optional strictness override (`basic` / `standard` / `strict`), persisting the level to config on zero-error runs (`--persist`), progressive ratcheting (`--ratchet`, climbs `basic → standard → strict` fixing to zero at each rung), and scope restriction (`--scope`). Every run ends with a pointer to `/harness:reasoning-gaps` as the natural next step.

```text
/pyright:run-and-fix
/pyright:run-and-fix strict --persist
/pyright:run-and-fix --ratchet
/pyright:run-and-fix --scope src/workers/ --intent improve
```

### harness

Three commands for making code agent-friendly — diagnose reasoning gaps, fix feedback-loop blockers, and scaffold the documentation agents read first.

```text
/plugin install harness@wild-horses
```

#### Typical workflow

The two analysis commands take a file path, directory path, or free-form description of what to analyze. With no argument they default to files changed on the current branch:

```text
/harness:reasoning-gaps src/pipeline/
/harness:reasoning-gaps the authentication layer
/harness:reasoning-gaps
```

```text
/harness:feedback-blockers src/pipeline/
/harness:feedback-blockers the decoder pipeline
/harness:feedback-blockers
```

Each produces a remediation plan with ranked interventions that can be implemented automatically — you review the plan, choose "implement all," and the agent loop works through each task, running tests after every change. Progress lives in a JSON task file; `--resume` picks up where you left off across sessions.

If you're starting on a new project, run `/harness:setup` once to scaffold the harness documentation structure that AI agents read for orientation.

#### When to use the harness loop vs. superpowers plans

The harness loop runner (`/harness:task-list-builder` + `/harness:task-list-runner`) and the [superpowers](https://github.com/obra/superpowers) plan skills (`writing-plans` + `executing-plans`) solve overlapping problems but are tuned for different shapes of work.

**Feature-level: which one fits the work I'm doing?**

| Aspect                 | `superpowers:writing-plans` + `executing-plans`                         | `task-list-builder` + `task-list-runner`                                                                                    |
| ---------------------- | ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Task type              | Exploratory — requirements ambiguous, plan shifts as you learn          | Autonomous batch execution — uniform-shape tasks defined up front                                                           |
| Human involvement      | Review-gated — human inspects each step before the next runs            | Unattended — kick off `--all` and read the final report                                                                     |
| Resume across sessions | Manual — re-read the plan, find your place                              | First-class — `cli status` auto-locates in-progress files; `cli next` claims the next task atomically                       |
| Plan stability         | Plan can be revised mid-execution at review checkpoints                 | Plan is fixed up front; structural revisions go back through `task-list-builder` rewrite mode                               |
| Failure handling       | Conversational — agent pauses at the checkpoint and asks how to proceed | Recorded — failing tasks move to `failed` status with a log; the loop continues to the next task                            |
| Test-task discipline   | Optional — author choice                                                | Mandatory paired `"Write tests for X"` task after every task with `createsNewCode: true`                                    |
| Typical scale          | A handful of well-scoped tasks                                          | 10–50 uniform tasks (the typical `/harness:reasoning-gaps` or `/harness:feedback-blockers` output)                          |
| Best fit               | Greenfield features, ambiguous design, "I'm not sure what I'm building" | Large-batch refactors and remediations — especially the output of `/harness:reasoning-gaps` or `/harness:feedback-blockers` |

**Technical: how does each one enforce its guarantees?**

| Aspect              | `superpowers:writing-plans` + `executing-plans`                                | `task-list-builder` + `task-list-runner`                                                                                                         |
| ------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Plan artifact       | Free-form Markdown plan                                                        | Schema-validated JSON paired with a readable Markdown summary                                                                                    |
| Plan mutations      | Agents edit the markdown directly                                              | Every mutation goes through `task_list_cli.py` (atomic write + schema validation)                                                                |
| Verification        | Author writes verification steps in prose; agent decides how to run them       | Top-level `verifySteps` array; the CLI runs typecheck → tests in order, fail-fast, with per-step log files                                       |
| Per-task acceptance | Read-and-judge by the executing agent                                          | Fresh-context, read-only `Explore` subagent evaluates an `agentValidations` array of inspection-only facts (runtime denies `Write`/`Edit` to it) |
| Corruption gate     | None                                                                           | Between every iteration the runner re-runs `cli status`; any non-zero exit halts the loop on a malformed file                                    |
| Concurrency         | `subagent-driven-development` supports parallel subagents on independent tasks | Strictly sequential foreground `Agent` calls — tasks may depend on prior tasks' edits, so parallelism is forbidden                               |

Pick superpowers when the plan itself is a deliverable and a human will review each step. Pick the harness loop when the plan is a means to an end and you want strict verification and unattended execution across a homogenous batch of tasks.

---

#### /harness:reasoning-gaps

**Core question:** _If an AI agent read this code, what would it get wrong?_

**Designed for dynamically typed languages** — Python, Ruby, JavaScript, and TypeScript without strict mode. In strongly typed languages (Go, Rust, Java), the compiler already enforces most of what this command checks for. The Implicit Flow & State and Structure & Documentation dimensions are language-agnostic, but the Type & Data Contracts dimension (35% of the score) is largely a non-issue when the compiler enforces types.

This is **not a code quality review.** Code can be well-written, type-clean, and still be opaque to an AI. The question is whether an agent reading the code will form the right model of what it does — if a function has no annotations, the agent has to read the body and every caller; if control flow is implicit (decorators that change behavior, dynamic dispatch via `getattr`, signal handlers triggered elsewhere), the agent doesn't know what will actually happen at runtime; if a function is 80 lines with 5 nested branches, the agent has to hold all of it in context to make a safe change.

Spawns 3 parallel specialist agents that examine code through different lenses, then merges findings into a prioritized remediation plan.

| Dimension                     | What it looks for                                                                                                                      |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| **Type & Data Contracts**     | Untyped signatures, dict-based data passing, missing return types, `Any` usage, stringly-typed interfaces, missing boundary validation |
| **Implicit Flow & State**     | Decorator side effects, dynamic dispatch, magic methods, signal/event systems, global mutable state, hidden mutations                  |
| **Structure & Documentation** | Missing module/class docstrings, long functions, deep nesting, circular imports, undocumented protocols                                |

Findings are merged and deduplicated across dimensions. Cross-dimension findings (e.g., an untyped function that is also 60 lines long with implicit state mutations) are the highest-leverage fixes — one refactor improves AI readability on multiple fronts.

The report produces ranked interventions. Each intervention is a coherent change: "Create a `PipelineConfig` Pydantic model to replace dict-based config access," or "Decompose `process_request()` into typed single-responsibility functions." When you choose to implement, interventions become tasks executed by an automated agent loop.

**Interventions that create new code get paired test tasks.** If an intervention decomposes a long function into smaller functions, or extracts a new class or service, the plan automatically generates a companion test task placed immediately after it. The test task specifies the exact new functions/classes to test, concrete test cases (happy path, edge cases, error handling), and where the test file should go. Interventions that only add type annotations, docstrings, or comments do not get test tasks — they're verified by their own acceptance criteria.

Progress is tracked in a JSON task file that supports `--resume`, so you can stop and pick up where you left off across sessions.

```text
/harness:reasoning-gaps src/auth/
/harness:reasoning-gaps src/api.py
/harness:reasoning-gaps the cli code
/harness:reasoning-gaps
/harness:reasoning-gaps --resume
```

#### /harness:feedback-blockers

**Core question:** _Can an AI edit this code and know whether it got it right?_

This is about **correctness and observability**, not cycle speed. When an AI makes an edit, the question is whether it can tell — without a human eyeballing the result — that the edit was correct. If tests pass but assert the wrong invariants, if effects happen invisibly to the caller, if the only confirmation a change worked is to run the full app and look at the UI, if a seam is so wide the agent can't isolate "did I break X?" from everything else, the agent has no way to verify its own work. Speed and noise show up as downstream symptoms of the same underlying problem.

How this differs from `/harness:reasoning-gaps`:

|             | reasoning-gaps                       | feedback-blockers                                 |
| ----------- | ------------------------------------ | ------------------------------------------------- |
| Asks        | "would an AI misread this?"          | "can an AI verify its edit was correct?"          |
| Lens        | comprehension                        | correctness & observability                       |
| Typical fix | add types, docs, narrow control flow | tighten assertions, surface effects, shrink seams |

Spawns 4 parallel specialist agents that each examine the code through a different lens, then merges their findings into a unified report with a prioritized remediation plan.

| Pillar                   | What it looks for                                                                                              |
| ------------------------ | -------------------------------------------------------------------------------------------------------------- |
| **Encapsulation**        | Leaky abstractions, mutable state exposure, missing boundary validation, god objects                           |
| **OOP Design**           | Procedural code hiding in classes, inheritance vs composition mismatches, SRP violations, missing polymorphism |
| **Testability**          | Hard-wired dependencies, hidden side effects, non-determinism, missing seams for test doubles                  |
| **Harness-Friendliness** | Opaque failures, large blast radius, implicit contracts, poor error locality                                   |

Findings from all four agents are deduplicated and merged. When the same code location is flagged by multiple agents (e.g., a god object that is also untestable and produces opaque errors), it's highlighted as a cross-pillar finding — these are the highest-leverage fixes because one refactor improves multiple pillars.

The report ranks interventions by impact and can optionally be implemented via an automated agent loop: each intervention becomes a task, agents implement them one at a time, and progress is tracked in a JSON task file that supports `--resume` across sessions.

```text
/harness:feedback-blockers src/auth/
/harness:feedback-blockers src/api.py
/harness:feedback-blockers the ingestion service
/harness:feedback-blockers
/harness:feedback-blockers --resume
```

#### /harness:setup

**Core question:** _Does the repo have a map for the agent to read?_

AI agents start every task by reading `CLAUDE.md` to orient themselves. If there's no structured documentation — no architecture overview, no pointers to design decisions, no separation between entry-point context and deep reference material — the agent spends its first minutes (and context window) on exploratory reads just to figure out what the project does. A well-organized harness directory (`CLAUDE.md` as a ~100-line table of contents, `ARCHITECTURE.md` for the domain map, `docs/` for everything else) gives agents fast orientation so they can start making useful changes immediately.

Analyzes existing files, proposes moves and generations, executes after approval. Never deletes files.

```text
/harness:setup
/harness:setup /path/to/project
```

### marketplace

Scaffold a new Claude Code plugin marketplace with proper structure, schema validation, and CLAUDE.md conventions.

```text
/plugin install marketplace@wild-horses
```

#### /create

**Why this matters for AI development:** Claude Code plugins are how you package reusable AI workflows — analysis tools, scaffolding commands, automated loops — and share them across projects and teams. A marketplace is a collection of plugins that others can install with a single command. Getting the directory structure, manifests, and conventions right is fiddly; this skill handles it interactively so you can focus on the plugin content.

Walks you through creating a marketplace repo: asks for a name, checks for an existing skill to import, and generates `marketplace.json`, `plugin.json`, and `CLAUDE.md` with marketplace conventions.

```text
/create
/create my-marketplace
```

## Install

1. Run `/plugin` in Claude Code
2. Select **Marketplaces**
3. Select **Add marketplace**
4. Enter `paulbaranowski/wild-horses`

## License

MIT
