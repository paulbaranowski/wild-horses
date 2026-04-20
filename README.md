# wild-horses

A [Claude Code](https://claude.ai/code) plugin marketplace for making code AI-readable and agent-friendly.

## Plugins

### harness

Harness engineering tools — find AI reasoning gaps, audit code for feedback-loop blockers, and set up the harness directory structure.

```text
/plugin install harness@wild-horses
```

#### Typical workflow

Set up the documentation structure so agents can orient themselves:

```text
/harness:setup
```

Make the code AI-readable — add types, decompose long functions, document implicit flow. This is the highest-leverage starting point for dynamically typed codebases (Python, Ruby, JavaScript):

```text
/harness:reasoning-gaps src/pipeline/
/harness:reasoning-gaps the authentication layer
```

Then fix the feedback loops — testability, opaque errors, tight coupling. This matters for any language:

```text
/harness:feedback-blockers src/pipeline/
/harness:feedback-blockers the decoder pipeline
```

Both analysis commands accept file paths, directory paths, or free-form descriptions of what to analyze. They default to files changed on the current branch when run without arguments. Each produces a remediation plan with ranked interventions that can be implemented automatically — you review the plan, choose "implement all," and the agent loop works through each task, running tests after every change.

---

#### /harness:reasoning-gaps

**Designed for dynamically typed languages** — Python, Ruby, JavaScript, and TypeScript without strict mode. In strongly typed languages (Go, Rust, Java), the compiler already enforces most of what this command checks for. The Implicit Flow & State and Structure & Documentation dimensions are language-agnostic, but the Type & Data Contracts dimension (35% of the score) is largely a non-issue when the compiler enforces types.

**Why this matters for AI development:** Before an AI agent edits code, it reads it. If a function has no type annotations, the agent can't tell what it accepts or returns without reading the entire body — and every caller. If control flow is implicit (decorators that change behavior, signal handlers triggered elsewhere, dynamic dispatch via `getattr`), the agent doesn't know what will actually happen at runtime. If a function is 80 lines long with 5 nested branches, the agent has to hold all of that in context to make a safe change. These gaps don't make code "bad" — they make it opaque to AI reasoning, which means agents make wrong edits, miss hidden connections, or waste time on exploratory reads.

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

**Why this matters for AI development:** AI agents work in a change-test-fix loop. They make an edit, run tests, and use the result to decide what to do next. When that loop is slow (tests take forever), noisy (changing one thing breaks unrelated tests), or opaque (errors don't say what went wrong), agents spiral — they can't tell if their change was correct, so they guess, revert, and try again. This command finds the code patterns that cause those broken feedback loops.

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

**Why this matters for AI development:** AI agents start every task by reading `CLAUDE.md` to orient themselves. If there's no structured documentation — no architecture overview, no pointers to design decisions, no separation between entry-point context and deep reference material — the agent spends its first minutes (and context window) on exploratory reads just to figure out what the project does. A well-organized harness directory (`CLAUDE.md` as a ~100-line table of contents, `ARCHITECTURE.md` for the domain map, `docs/` for everything else) gives agents fast orientation so they can start making useful changes immediately.

Analyzes existing files, proposes moves and generations, executes after approval. Never deletes files.

```text
/harness:setup
/harness:setup /path/to/project
```

### pyright

Run pyright on a Python codebase and fix its findings using a documented playbook of fix patterns. A natural precursor to `/harness:reasoning-gaps` for Python code: pyright rigorously resolves the typing dimension that reasoning-gaps analyzes heuristically, so your reasoning-gaps run can focus on the implicit control flow and documentation axes that only it sees.

```text
/plugin install pyright@wild-horses
```

#### /pyright:run-and-fix

**Python-specific.** This is the one plugin in the marketplace that targets a single language; everything else is language-agnostic. Use it on Python codebases that run (or want to adopt) pyright for type checking.

**Why this matters for AI development:** type-annotated code gives AI agents a trustworthy "what does this function accept and return" signal without reading the body or every caller. Pyright catches the annotation gaps; this command fixes them — and, critically, knows when _not_ to fix them: it suppresses cases where the right resolution is a semantically loaded design decision, and flags cases where pyright has uncovered a real runtime bug rather than a type-system gripe.

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
