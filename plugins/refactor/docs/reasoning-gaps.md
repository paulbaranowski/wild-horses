# /refactor:reasoning-gaps

**Core question:** _If an AI agent read this code, what would it get wrong?_

Designed for dynamically typed languages — Python, Ruby, JavaScript, and TypeScript without strict mode. In strongly typed languages (Go, Rust, Java), the compiler already enforces most of what this command checks for; the Implicit Flow & State and Structure & Documentation dimensions are language-agnostic, but the Type & Data Contracts dimension (35% of the score) is largely a non-issue when the compiler enforces types.

This is **not a code quality review.** Code can be well-written, type-clean, and still be opaque to an AI. The question is whether an agent reading the code will form the right model of what it does — if a function has no annotations, the agent has to read the body and every caller; if control flow is implicit (decorators, dynamic dispatch via `getattr`, signal handlers triggered elsewhere), the agent doesn't know what will actually happen at runtime; if a function is 80 lines with 5 nested branches, the agent has to hold all of it in context to make a safe change.

## Usage

```text
/refactor:reasoning-gaps src/auth/
/refactor:reasoning-gaps src/api.py
/refactor:reasoning-gaps the cli code
/refactor:reasoning-gaps
/refactor:reasoning-gaps --resume
```

The argument is a file path, directory path, or free-form description. With no argument, defaults to files changed on the current branch. `--resume` picks up an in-progress task list from `docs/exec-plans/active/`.

## How it works

Spawns 3 parallel specialist agents that examine the code through different lenses, then merges their findings into a prioritized remediation plan.

| Dimension                     | What it looks for                                                                                                                      |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| **Type & Data Contracts**     | Untyped signatures, dict-based data passing, missing return types, `Any` usage, stringly-typed interfaces, missing boundary validation |
| **Implicit Flow & State**     | Decorator side effects, dynamic dispatch, magic methods, signal/event systems, global mutable state, hidden mutations                  |
| **Structure & Documentation** | Missing module/class docstrings, long functions, deep nesting, circular imports, undocumented protocols                                |

Cross-dimension findings — e.g., an untyped function that is also 60 lines long with implicit state mutations — are the highest-leverage fixes because one refactor improves AI readability on multiple fronts.

## Output

A ranked list of interventions. Each intervention is a coherent change like _"Create a `PipelineConfig` Pydantic model to replace dict-based config access"_ or _"Decompose `process_request()` into typed single-responsibility functions"_. When you choose to implement, interventions are converted into a JSON task list and handed to [task-list-runner](task-list-runner.md) for unattended execution.

**Interventions that create new code get paired test tasks.** If an intervention decomposes a long function or extracts a new class, the plan automatically generates a companion test task placed immediately after it. The test task specifies the exact new functions/classes to test, concrete test cases (happy path, edge cases, error handling), and where the test file should go. Annotation/docstring/comment-only interventions don't get test tasks — they're verified by their own acceptance criteria.

## How this differs from `/refactor:feedback-blockers`

|             | reasoning-gaps                       | feedback-blockers                                 |
| ----------- | ------------------------------------ | ------------------------------------------------- |
| Asks        | "would an AI misread this?"          | "can an AI verify its edit was correct?"          |
| Lens        | comprehension                        | correctness & observability                       |
| Typical fix | add types, docs, narrow control flow | tighten assertions, surface effects, shrink seams |

In Python, **run this command before `/pyright:run-and-fix --intent improve`, not after.** Design the types here first — turning `str` into `Literal[...]`, `dict[str, Any]` into `TypedDict`, etc. — then pyright propagates that design across every call site. Pyright-first invites silencing with `: Any` and `# type: ignore` before any design pass runs. Full rationale: [pyright README — Relationship to /refactor:reasoning-gaps](../../pyright/README.md#relationship-to-refactorreasoning-gaps).
