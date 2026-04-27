---
name: guru-dev-implement
description: Implement a feature or fix using strict TDD discipline (tests first) and write-time application of the reasoning-gaps and feedback-blockers rule sets. Pairs with `/guru-dev-review (harness)` for the senior-dev "evolve, don't append" decision (extend / adapt / refactor-first / add-new / parallel-new-with-toggle) — run guru-dev-review first for non-trivial changes, then implement against its recommendation. For behavior changes, supports a parallel-new-with-toggle pattern using the project's existing flag system, OpenFeature, or a minimal in-codebase Toggle. Use when implementing any non-trivial change — a new function, class, endpoint, bug fix, or behavior change. Auto-invokes on "implement", "build", "add", or "refactor" intents tied to code work.
user-invocable: true
disable-model-invocation: false
argument-hint: "[task description or path to plan file]"
---

# Guru-Dev Implement (TDD + Rule-Driven Discipline)

This skill enforces a write-time discipline for implementing code. It is the **write-time mirror** of the `/harness:feedback-blockers` and `/harness:reasoning-gaps` review-time analyzers: same rule-set, applied as a generation discipline rather than a post-hoc audit.

It pairs with **`/guru-dev-review (harness)`** — the companion skill that decides whether the change should extend, adapt, refactor-first, add-new, or parallel-new-with-toggle. For non-trivial changes, run `guru-dev-review` first; this skill then consumes its recommendation.

Three non-negotiables:

1. **Decide the shape before writing.** Either run `/guru-dev-review (harness)` first or make a brief decision yourself (Phase 2). Don't start writing before naming which of extend / adapt / refactor-first / add-new / parallel-new-with-toggle you're doing.
2. **Tests before code.** Write failing tests first. Watch them fail for the _right reason_. Then write the smallest code that makes them pass.
3. **Apply the rules at write-time.** Walk a condensed checklist from `/harness:reasoning-gaps` (types, implicit flow, structure) and `/harness:feedback-blockers` (testability, encapsulation, observability) before declaring done.

**Task:** "$ARGUMENTS"

---

## Phase 1: Understand the Task

1. Parse `$ARGUMENTS`:
   - If it's a path to a plan file (e.g. `docs/exec-plans/active/*.md`), read it and extract the specific intervention(s) to implement.
   - If it's a free-form description, restate it back to the user in one sentence and confirm before proceeding.
   - If empty, ask the user what they want implemented. Don't guess.

2. Identify **acceptance criteria** explicitly:
   - What inputs does the new code accept?
   - What observable behavior or output does it produce?
   - What is _out of scope_? (This is your guardrail against gold-plating in Phase 5.)

If acceptance criteria are vague, ask **one** clarifying question before proceeding. Don't ask three at once and don't proceed on assumption.

---

## Phase 2: Confirm the Senior-Dev Decision

The deep "evolve, don't append" analysis lives in **`/guru-dev-review (harness)`**. This phase is the gate that ensures the decision has been made before writing any tests or code.

**If the user (or you) already ran `/guru-dev-review` for this change:** restate the conclusion in two lines (or three if Option E was chosen):

- **Decision:** [extend | adapt | refactor-first | add-new | parallel-new-with-toggle]
- **Natural home:** `path/to/module.py` (or class) — [one-line why]
- **Toggle mechanism (only if parallel-new-with-toggle):** [implementation tier + flag key + how to force OLD + removal trigger — copied from the review output]

Then proceed to Phase 3.

**If `/guru-dev-review` has not been run:** decide whether you need it.

- **Run `/guru-dev-review` first when:** the change overlaps with multiple existing modules, the natural home is non-obvious, you'd be tempted to "add new" without strong justification, **the change alters the observable behavior of an existing feature** (Option E territory), or the user explicitly asked for a careful design pass. Stop, invoke `/guru-dev-review (harness)`, then return here with its output.
- **Skip `/guru-dev-review` and decide inline when:** the change is genuinely small and local (a bug fix in a known function, a new test, a one-line config change, a contained tweak inside one class). In that case, state in one sentence: which of the five options you're doing, and why.

**"Add new" and "parallel-new-with-toggle" both require explicit justification.** For "add new": state why extending, adapting, or refactoring-first would be worse here. For "parallel-new-with-toggle": confirm the change actually alters observable behavior (not pure refactor, not pure addition) and that local A/B verification has real value here. If the justification is weak for either, run `/guru-dev-review` and reconsider.

Do not move to Phase 3 until the decision is named in writing.

---

## Phase 3: Design Tests (Before Any Code)

List the tests you will write **before writing them**. Group them as:

- **Happy path** — the primary behavior. At least 1.
- **Edge cases** — boundary inputs, empty/null/zero, max sizes, off-by-one, unusual-but-valid inputs. At least 1-2 if applicable.
- **Error cases** — invalid input, downstream failure, contract violation. At least 1 if the function has a real failure mode.

For each, write a one-line description of what it asserts. If you cannot think of an edge case or an error case, treat that as a signal to re-read the requirements — most real code has both, and skipping them hides the gap until production.

**Locate the test file:**

- Mirror the existing project structure (e.g. `tests/test_<module>.py`, `<module>.test.ts`, `<module>_test.go`).
- If no test file exists for the target module, create one in the conventional location.
- If the project uses fixtures/factories, identify which ones you'll reuse before writing the tests.

---

## Phase 4: Write the Failing Tests

Write the tests from Phase 3. Then **run them and confirm they fail for the right reason**:

- Failure should be `AssertionError`, `ImportError`, `AttributeError`, or equivalent — pointing at the not-yet-existing function/method/class.
- Failure should NOT be a typo, a missing fixture, or an unrelated import problem. Those are bugs in the test, not evidence that the implementation is needed.

If a test passes _before_ you've written the implementation, the test is wrong (or it's testing existing behavior, not the new behavior). Fix it before proceeding.

If the project supports incremental commits, commit the failing tests as their own commit — this makes the TDD discipline visible in the git history and helps future readers reason about intent.

---

## Phase 5: Implement Minimally

Write the smallest code that makes the tests pass. Apply the survey decision from Phase 2:

- **Extend**: add the method/parameter to the existing structure.
- **Adapt**: generalize the existing structure, then call into it.
- **Refactor first**: do the refactor, run _existing_ tests to confirm green (no behavior change), commit, then add the new behavior on top.
- **Add new**: create the new structure, but match the project's existing patterns for similar things (naming, file layout, error style, logging style).
- **Parallel-new-with-Toggle**: build the new path alongside the old, gated by the toggle mechanism (see "Option E mechanics" immediately below). Default to NEW. Apply the deprecation comment template to the old branch.

Resist the urge to:

- Add features that aren't covered by a test.
- Add error handling for cases that can't happen.
- Add abstraction layers "for the future."
- Write helpers that have only one caller.
- Bundle unrelated cleanups ("while I'm here").

Run the tests after each meaningful chunk. The signal you want is red → green incrementally, not a single big-bang flip at the end.

### Option E mechanics — Parallel-new-with-Toggle

This sub-section fires only when the Phase 2 decision is **parallel-new-with-toggle**. Skip it for the other four options.

#### 5E.1 — Pre-check: scan for existing flag systems

Before introducing any toggle infrastructure, scan the project. If a flag system already exists, integrate at the boundary instead of adding a parallel one. Look for:

- Imports / usage of: `flipper`, `Flipper.enabled?`, `launchdarkly`, `ldclient`, `unleash`, `flagsmith`, `statsig`, `openfeature`, `posthog`
- Env-var-driven flags: `os.getenv("FEATURE_X")`, `ENV["FEATURE_X"]`
- Custom flag modules: `flags.py`, `lib/flags.rb`, `app/feature_flags.rb`, similar

If found, use it. The `Toggle` design is just "named, type-safe, default-NEW reference to a flag the existing system evaluates."

#### 5E.2 — Pick the implementation tier (only if no existing flag system was found)

Confirm Phase 2's tier choice was correct given what's actually in the codebase. The tiers (lightest first):

- **Tier 1: OpenFeature** — recommended default when no flag system exists. Vendor-neutral, in-memory provider for dev/test, swap providers for cloud later.
  - Python: `pip install openfeature-sdk`. Use `InMemoryProvider` for dev/test.
  - Ruby: `gem install openfeature-sdk`. Use the in-memory provider.
- **Tier 2: Minimal in-codebase pattern** (`Toggle` value object + `Feature` enum) — only if you specifically want frozen-snapshot threading semantics (decisions made at boundary, immutable thereafter) AND you don't anticipate cloud-capable rollouts. ~30 lines per language.

Tier 1 is the strong default. Tier 2 should be defended in writing.

#### 5E.3 — Bootstrap (separate commit) if no flag infrastructure exists

If you're introducing a flag system as part of this work, do it as a **separate commit before the feature commit**:

1. **Commit 1:** add the registry module (`flags.py` / `lib/flags.rb`) with no entries yet, plus any minimal infrastructure (OpenFeature provider config, or the `Toggle` + `Feature` skeleton). Run existing tests to confirm green.
2. **Commit 2:** introduce the actual feature with old/new branches gated by the flag, add the `Feature` entry, write tests for the new path.

Do not bundle. The bootstrap is reusable across future Option E rewrites; the feature is a one-off.

#### 5E.4 — Apply the deprecation comment template

On the old branch (the `else` arm or the function being shadowed), apply:

```text
DEPRECATED <YYYY-MM-DD>: replaced by `<new_function_or_path>`.
Force OLD via <one-line: how to force OLD locally — e.g. "set FLAG_X=0",
"Toggle.with_old(Feature.X)", "Flipper.disable(:flag_x) in console">.
Remove this branch and FLAG_X once <removal trigger from the review output —
e.g. "validated locally", "after 2 weeks default-NEW with no rollback signal">.
```

The removal trigger must be **concrete**, not "eventually" or "when ready". Vague triggers rot.

#### 5E.5 — Tests

- **Full tests on the NEW path.** This is the primary path going forward; cover happy / edge / error per Phase 3.
- **Old path keeps its existing coverage.** Don't extend it — it's on borrowed time.
- **At least one A/B verification test.** Run a representative input through both branches and assert equivalence (or assert the intentional difference explicitly). This is what makes the toggle actually useful.
- **Default-NEW test.** Confirm that with no toggle override, the new path runs.

#### 5E.6 — Removal commit (record as a follow-up; do not execute now)

When the removal trigger fires (later, possibly in another session), the cleanup is:

1. Delete the `Feature` entry from the registry.
2. Delete the old branch and any helpers only used by it (compiler/linter will flag callsites).
3. Delete the toggle parameter only if no other features use it.
4. Commit separately from any other work.

Record this in Phase 8's "suggested follow-ups" so the user has a reminder.

---

## Phase 6: Self-Check Against the Rules

Before declaring done, walk the new and modified code against this condensed checklist. These are the highest-leverage rules from `/harness:reasoning-gaps` and `/harness:feedback-blockers`, applied at write-time.

### Reasoning-Gaps checklist (can a future agent understand this?)

- [ ] **Typed signatures** — every public function/method has parameter and return type annotations. No `Any`. No bare `list`/`dict` — use `list[Foo]`, `dict[K, V]`.
- [ ] **No dict-based contracts** — data passed between functions uses a `dataclass`, Pydantic model, or `TypedDict`, not `dict[str, Any]`. Stringly-typed status/event values become `Enum`s. (`getattr(obj, dynamic_key)` is _not_ a fix — it's the same opacity moved one layer.)
- [ ] **No hidden flow** — no decorator that changes return type or adds I/O without a one-line comment; no import-time side effects; no dynamic dispatch without an explicit registry type.
- [ ] **Module/class docstring on new files and classes** — one or two sentences saying what it is and why it exists. Be concrete: not "the user service", but "validates and persists user records; the only writer to the users table".
- [ ] **No "why" gap** — magic numbers, regex patterns, workarounds, business rules each get a one-line `# why:` comment. The _why_, not the _what_.

### Feedback-Blockers checklist (can this be tested and changed safely?)

- [ ] **Dependencies injected, not constructed** — the new code accepts collaborators via parameters/constructor; it doesn't `Database()` or `requests.get(...)` deep inside its logic.
- [ ] **No untestable side effects** — the core logic is callable without I/O. I/O sits at the edge.
- [ ] **No non-determinism without a seam** — `datetime.now()`, `random`, `uuid.uuid4()`, env reads come from injected providers (or are passed in), not called inline.
- [ ] **Errors are loud and located** — exceptions name the failing input, the failing component, and a hint of cause. No bare `except:` or `except Exception:` swallowing context. No "something went wrong" messages.
- [ ] **Encapsulation honored** — fields and methods that should be private _are_ private (leading underscore in Python; `private`/`#` in TS/JS). Mutable internals aren't returned by reference.
- [ ] **Single responsibility** — the new class/function does one thing. If the name needs "and" to describe it, it's two things — split.

If a box is unchecked, fix it before claiming done. If a rule genuinely doesn't apply (e.g. no error case exists for a pure transform), say so explicitly in Phase 8 — don't silently skip.

---

## Phase 7: Verify

Run the project's verification commands and confirm green. Run the focused tests first (the new test file alone), then the full suite — fast feedback before broad feedback.

<!-- TODO(user): Replace this list with your project's actual verification commands.
     Examples:
       - Python:     `uv run pytest tests/test_<module>.py -v`  then  `uv run pytest`
                     `uv run pyright src/`
       - Node/TS:    `npm test -- --run path/to/file.test.ts`  then  `npm test -- --run`
                     `npm run typecheck`
       - Lint:       `ruff check .`  /  `eslint .`
     The skill runs these in order; first failure stops the chain and is reported.
     If you leave this TODO unfilled, the skill falls back to the auto-detection
     described below.
-->

**Default fallback (used when the TODO above is unfilled):**

- If `pyproject.toml` or `uv.lock` is present → `uv run pytest` (focused, then full).
- If `package.json` is present → `npm test`.
- If a `Makefile` defines a `test` target → `make test`.
- Otherwise, ask the user for the command.

Stop and report on the first failure rather than continuing blindly. If a failure is unrelated to the change (pre-existing breakage), say so explicitly and ask the user how to proceed — do not silently skip or "fix" it without permission.

---

## Phase 8: Report

Tell the user, in this order, in this shape:

1. **What was built** — one sentence describing the change.
2. **Senior-dev decision** — which option from Phase 2 (extend / adapt / refactor-first / add-new / parallel-new-with-toggle) and why, in one sentence.
3. **Toggle (only if parallel-new-with-toggle)** — implementation tier used (existing flag system / OpenFeature / minimal pattern), the flag/feature key, how to force OLD locally, and the removal trigger recorded in the deprecation comment.
4. **Tests** — number of tests added, all green; for Option E, confirm the A/B verification test passes; note any tests that were intentionally skipped.
5. **Rule checks** — call out any items that didn't apply, with a one-line reason. If everything passed, say "all rules applied".
6. **Files touched** — short list with paths, grouped by created vs modified. For Option E with bootstrap, list the bootstrap commit and the feature commit separately.
7. **Suggested follow-ups** — if the survey or implementation revealed something out-of-scope, name it but do **not** act on it. **For Option E, always list the removal commit as a future follow-up**, with the trigger condition restated.

---

## Guidelines

- **TDD is non-negotiable here.** If you find yourself writing production code without a failing test pointing at it, stop, back up, and write the test first.
- **The survey is the senior move.** Skipping Phase 2 is how codebases accumulate parallel half-duplicated structures. Spend real effort here even when it feels slow — it pays back at every future change.
- **The rule checklists are the floor, not the ceiling.** Pass them, then keep going if more rigor is warranted by the change.
- **Don't gold-plate.** Implement what the task asks for, with the rules applied. Don't bundle "while I'm here" cleanups unless the user agreed to them. Surface them as follow-ups in Phase 8.
- **Match project conventions.** If the project uses `snake_case`, use `snake_case`. If it has a particular pattern for services, factories, or error classes, follow it. The existing code is the spec.
- **When in doubt about scope, ask once and proceed.** Don't paralyze on clarification loops; one targeted question is fine, three is friction.
