---
name: guru-dev-review
description: Pre-implementation senior-dev review — decides where a change belongs in the codebase (extend / adapt / refactor-first / add-new / parallel-new-with-toggle) and captures the planning details an executor needs (acceptance criteria, natural home, overlapping structures, anti-patterns rejected, and — for parallel-new-with-toggle — the flag-system tier + removal trigger). Outputs a structured recommendation designed to be handed to `superpowers:writing-plans` for task decomposition, then to `superpowers:executing-plans` or `superpowers:subagent-driven-development` for execution. Use before writing any non-trivial new code. Auto-invokes on phrases like "should I add", "where should this go", "is there already a", "refactor", "change the behavior of", or "before I implement".
user-invocable: true
disable-model-invocation: false
argument-hint: "[change description or path to plan file]"
---

# Guru-Dev Review (Evolve, Don't Append)

This skill exists to answer one question before you write any code:

> **Does the codebase already have a structure that this change belongs inside, even if it needs to grow first?**

It is the senior-dev companion to `/guru-dev-implement (harness)`. Run it first whenever a change might overlap with existing code — which is most of the time on a non-trivial codebase. The output is a structured recommendation you can paste into `/guru-dev-implement` (or hand to a colleague) so the implementation phase doesn't re-litigate the design.

This is **not** "find a util to call." It is asking whether the new requirement should reshape what already exists.

**Change to review:** "$ARGUMENTS"

---

## Phase 1: Understand the Proposed Change

1. Parse `$ARGUMENTS`:
   - If it's a path to a plan file, read it and extract what is to be implemented.
   - If it's a free-form description, restate it back to the user in one sentence and confirm.
   - If empty, ask the user what change they want surveyed.

2. Identify three things explicitly — these are the angles you'll search the codebase for overlap on. A change is rarely brand new on all three axes; usually at least one already has a home.
   - **The data shape** the change introduces or moves around (inputs, outputs, persisted records).
   - **The behavior** it produces (the verb — what does it _do_?).
   - **The trigger / entry point** (a CLI command? an HTTP route? a callback? a scheduled job?).

3. Capture **acceptance criteria** explicitly:
   - What inputs does the new code accept?
   - What observable behavior or output does it produce?
   - What is _out of scope_? (This is the executor's guardrail against gold-plating.)

If acceptance criteria are vague, ask **one** clarifying question before proceeding to Phase 2. Don't ask three at once and don't proceed on assumption — vague criteria propagate into a vague plan and an over-scoped implementation.

---

## Phase 2: Find the Natural Home

Where does this concern _actually belong_? Don't pick the first plausible folder — list candidates and choose deliberately.

1. **List 2-3 candidate locations.** A "location" is a module, package, or class. For each, note in one phrase why it's a candidate (e.g. _"`src/billing/invoices.py` — already owns invoice lifecycle logic"_).

2. **Pick one** with a one-sentence justification. The right home is usually the one that already owns the most-related concept — not the most-empty file.

3. **Reject these signals as bad reasons to pick a home:**
   - "This file is shorter / less scary to edit." (Familiarity bias.)
   - "There's a `utils/` folder." (Utils is where dead code goes to retire.)
   - "It would be its own thing." (Premature isolation; defer this judgment to Phase 4.)

If the right home is genuinely ambiguous and you can't justify one over another in a sentence, stop and ask the user — guessing here propagates into every later decision.

---

## Phase 3: Audit What Already Exists in That Home

Read the chosen home (and any other files it imports from heavily). Catalog the overlap with the new change along the three axes from Phase 1:

- **Types / models that overlap with the new data shape.** Existing dataclasses, Pydantic models, ORM rows, TypedDicts. Note name and location. Are they the same shape? A superset? A subset?
- **Functions / methods that overlap with the new behavior.** What already does something close? Cite `file:line`. How close is "close" — same inputs, same side effects, just one parameter different?
- **Patterns the new code should plug into.** Is there a registry, factory, dispatcher, base class, plugin system, middleware chain, or service layer that similar things already pass through? New code that bypasses an established pattern is a smell.

Be specific. "There's some user-related stuff in `users.py`" is not an audit. "`UserRecord` (users.py:23) holds the same fields the new code wants to persist; `UserService.upsert` (users.py:88) already does the exact upsert pattern, just not for the new field" is.

If the audit turns up _nothing_ overlapping on any axis, that's a real signal — but verify by widening the search before concluding "add new". Truly novel concerns are rare on a mature codebase.

---

## Phase 4: Make the Decision

Pick exactly **one** of the five options and state it. The decision must be specific enough that someone reading it knows what file(s) to open first.

### Option A — Extend

Add a new method, parameter, or branch to an existing structure.

- **Use when:** the new behavior is a natural variant of existing behavior (same inputs, slightly different output; same output, slightly different inputs; same operation, new caller).
- **Smell to reject:** if extending requires a new flag parameter on a function that's _already_ branching on flags, you're heading for a god-function — switch to **Adapt** or **Refactor first**.

### Option B — Adapt

Generalize the existing structure slightly so the new case fits cleanly.

- **Use when:** the existing structure is _almost_ right but hardcodes something the new case needs to vary. Promote the constant to a parameter, widen a type, parameterize a strategy, accept an interface instead of a concrete class.
- **Test:** after the adaptation, the existing callers should still work _without changes to their call sites_ (or with trivial type-driven changes). If the adaptation breaks all callers, you're really doing **Refactor first**.

### Option C — Refactor first, then add

The existing code blocks a clean addition. Do a small refactor (extract method, introduce a seam, split a class, replace conditional with polymorphism) as a **separate first step** with no behavior change, then add the new behavior on top.

- **Use when:** your honest assessment is "I can't add this cleanly without touching the shape of what's there."
- **Discipline:** the refactor is one commit (existing tests still green, no new tests), the addition is a second commit (new tests, new behavior). Two commits, not one.

### Option D — Add new

Create a new structure (file, class, function) alongside what exists.

- **Use when:** no existing structure fits without distortion AND you've considered Options A–C and rejected each with a concrete reason.
- **Defend it:** "Add new" is the choice that has to be _justified_. State why extension/adaptation/refactor would be worse here. If the only reason is "easier", you're choosing the wrong option.

### Option E — Parallel-new-with-Toggle

Build the new behavior alongside the old, gated by a flag, with a removal trigger. This is the right choice for **behavior changes to existing functionality** when you want a local A/B verification path before committing to the new path.

- **Use when:** the change alters the _observable behavior_ of existing functionality AND you want a local A/B verification path — letting the developer or AI agent flip between OLD and NEW to confirm equivalence (or intentional difference) before committing. Production rollout via the same toggle is a secondary, optional benefit.
- **Required properties of the toggle mechanism** (the implementation must satisfy all five):
  1. **Type-safe flag references** — named constants/enums/symbols in one centralized registry, not scattered string literals.
  2. **Default is NEW** — OLD is opt-in for verification.
  3. **Local-verification path** that doesn't require touching production config (an AI or dev can force OLD with a parameter, env var, or in-memory provider).
  4. **Deprecation comment on the old branch** naming the replacement, how to force OLD, and the removal trigger.
  5. **Removal as a separate follow-up commit** — delete the registry entry and the old branch together once validated.
- **Don't use when:** purely additive new feature (no existing behavior changes — go to A or D); pure refactor with verified no-behavior-change (green existing tests are the verification); trivial single-call-site change where inspection is enough.

#### Option E sub-decisions you must make at review time

If you've chosen Option E, work through these three sub-decisions in order and capture the answers in the Phase 6 output. Implementation patterns for each are in `plugins/harness/option-e-mechanics.md` — but the _decisions_ are planning, and they belong here.

##### E.1 — Scan for an existing flag system in the project

Before introducing any toggle infrastructure, scan the project. If a flag system already exists, the executor must integrate at its boundary instead of adding a parallel one. Look for:

- Imports / usage of: `flipper`, `Flipper.enabled?`, `launchdarkly`, `ldclient`, `unleash`, `flagsmith`, `statsig`, `openfeature`, `posthog`
- Env-var-driven flags: `os.getenv("FEATURE_X")`, `ENV["FEATURE_X"]`
- Custom flag modules: `flags.py`, `lib/flags.rb`, `app/feature_flags.rb`, similar

Record what you found (or "none found").

##### E.2 — Pick the implementation tier

The tiers, lightest first:

- **Tier 0: Existing project flag system** (the result of E.1). Always preferred when one exists. The `Toggle` design is just "named, type-safe, default-NEW reference to a flag the existing system evaluates."
- **Tier 1: OpenFeature** — recommended default when no flag system exists. Vendor-neutral, in-memory provider for dev/test, swap providers for cloud later.
  - Python: `pip install openfeature-sdk`. Use `InMemoryProvider` for dev/test.
  - Ruby: `gem install openfeature-sdk`. Use the in-memory provider.
- **Tier 2: Minimal in-codebase pattern** (`Toggle` value object + `Feature` enum) — only if you specifically want frozen-snapshot threading semantics (decisions made at boundary, immutable thereafter) AND you don't anticipate cloud-capable rollouts. ~30 lines per language.

Tier 1 is the strong default when no existing system was found. Tier 2 should be defended in writing.

##### E.3 — Define the removal trigger

The deprecation comment on the old branch will reference this trigger verbatim. It must be **concrete**, not "eventually" or "when ready". Vague triggers rot. Examples that pass: "after local A/B verification confirms parity", "after 2 weeks default-NEW in production with no rollback signal", "after PR #123 ships". Examples that fail: "soon", "when stable", "later".

---

## Phase 5: Reject the Anti-Patterns

Before you finalize, scan the proposed plan for these failure modes. If you spot one, revisit Phase 4.

- **Parallel duplicate.** Bolting a new function next to an existing one that does 80% of the same thing. Fix: extract the shared core, parameterize the difference.
- **Risk-driven fork.** Creating a new class because modifying the existing one _feels risky_. The risk is the signal — that file needs better tests or a small refactor before any change. Forking just makes the fragmentation permanent.
- **Flag pile-up.** Adding yet another boolean parameter to a function that's already a flag-soup. The real shape is two functions, a strategy object, or a small state machine.
- **Copy-tweak handler.** Duplicating a handler/route/model and changing a few lines. Extract the shared core or generalize the original — every duplicate is a future bug-fix you'll forget to apply twice.
- **Util drift.** Putting "general" helpers in `utils/` or `common/` because they don't obviously belong elsewhere. They _do_ belong somewhere — find it.

---

## Phase 6: Output the Review Result

Present the recommendation in this exact shape so it can be pasted into `superpowers:writing-plans` or shared with a colleague:

```markdown
## Guru-Dev Review Result

**Change:** [one sentence — the new behavior in plain language]

**Acceptance criteria:**

- **Inputs:** [what the new code accepts]
- **Observable output / behavior:** [what an outside caller sees]
- **Out of scope:** [explicit guardrails against gold-plating]

**Natural home:** `path/to/module.py` (or class `Foo` within it)
[one-sentence justification — why this home over the other candidates]

**Decision:** extend / adapt / refactor-first / add-new / parallel-new-with-toggle

**What this means concretely:**

- [for Extend/Adapt: name the existing structure and the specific change to it]
- [for Refactor first: name the refactor as step 1, then the addition as step 2]
- [for Add new: name the new structure and where it lives, and why A/B/C were rejected]
- [for Parallel-new-with-Toggle: name the new path, the old path, and the flag that selects between them]

**Existing structures to plug into:**

- `file.py:line` — [type/function/pattern and how the new code uses it]
- ...

**Toggle mechanism (only if Decision is parallel-new-with-toggle):**

- **Existing flag system in project:** [name + entry point, or "none found"] (from sub-decision E.1)
- **Implementation tier:** [Tier 0: existing | Tier 1: OpenFeature | Tier 2: minimal in-codebase] — and one-sentence why (from sub-decision E.2)
- **Flag/feature key:** `<flag-name-here>`
- **How to force OLD locally:** [one-line — e.g. "set env var FLAG_X=0", "Toggle.with_old(Feature.X)", "Flipper.disable(:flag_x)"]
- **Removal trigger:** [concrete condition — see sub-decision E.3 examples]
- **Mechanics reference:** the executor should follow `plugins/harness/option-e-mechanics.md` for bootstrap commit pattern, deprecation comment template, A/B verification test, and removal commit checklist.

**Anti-patterns considered and avoided:**

- [name the anti-pattern and the alternative chosen]

**Rule-checklist reference:** the executor should walk `plugins/harness/rule-checklist.md` (reasoning-gaps + feedback-blockers self-check) at the end of each implementation task.

**Open questions for the user (if any):**

- [anything that needs a human call before implementation starts]
```

If there are no open questions, omit that section. If the survey concluded "Add new" with no overlap found, say so plainly — short answers are fine when they're correct.

**Next step — handoff to writing-plans:** paste the result above into `superpowers:writing-plans` (or save it to a spec file under `docs/superpowers/specs/` or your project's equivalent). The plan-writer turns the decision + acceptance criteria + structures-to-plug-into into bite-sized TDD tasks. Then run `superpowers:executing-plans` (or `superpowers:subagent-driven-development`) to execute.

---

## Guidelines

- **Read before deciding.** Cite `file:line` for every overlapping structure you mention. A survey without specifics is a guess.
- **Defend "Add new".** It's the easiest option to reach for and the one that quietly accretes parallel code over time. Make it earn its place.
- **Refactor first is not failure.** It's the senior choice when the existing code blocks a clean addition. Two small commits beat one tangled one.
- **One survey, one decision.** Don't list "options" in the output. The survey's job is to _make_ the call, not punt it back to the user. (Open questions are fine if the call genuinely depends on user input — but resist the urge to list everything as a question to avoid committing.)
- **Stay scoped.** The survey is about _this change's_ home. Don't expand into "and also we should refactor the entire module" unless the change demands it. Surface broader observations as open questions, not as part of the recommended decision.
- **If the home is ambiguous, ask.** Picking the wrong home propagates into every later phase. One question to the user is cheaper than redoing the implementation.
