---
name: belongs-here
description: Senior-dev review that asks one question — does this work belong here, in this shape? Applies a 5-shape decision tree (extend / adapt / refactor-first / add-new / flag-gated-rewrite) plus an anti-pattern scan to catch the wrong-shape-of-change before it ships. Two modes — plan-review (input is a plan file or a plan in the conversation; reviews the shape implicit in the plan, ideally before `superpowers:executing-plans` runs) and code-review (input defaults to the current PR; reviews the shape implicit in the diff). Outputs a verdict — approved-with-reasoning, or revise-with-specifics. For plan-file inputs, writes `<plan>.belongs-here.md` next to the input; for in-conversation plans and for code-review, outputs to the conversation as a plan-shaped action list. Auto-invokes on phrases like "review my plan", "review my PR", "review this diff", "does this belong here", "is this the right approach", "before I execute", "should I add", "where should this go", "refactor", or "change the behavior of".
user-invocable: true
disable-model-invocation: false
argument-hint: "[change description or path to plan file]"
---

# Belongs Here (Evolve, Don't Append)

This skill exists to answer one question:

> **Is the proposed shape of this work right, given what's already in the codebase?**

Apply it as a senior-dev quality gate in either of two modes:

- **Plan-review mode.** A plan has been written (typically by `superpowers:writing-plans`, or hand-written, or pasted in). This skill reviews the shape implicit in that plan _before_ execution begins — catching a wrong-shape decomposition before any code is written.
- **Code-review mode.** Code has been written — typically the current PR. This skill reviews the shape implicit in the diff, catching a wrong-shape-of-change at PR-review time when the user actually engages.

In both modes the lens is the same: the 5-option decision tree (extend / adapt / refactor-first / add-new / flag-gated-rewrite), the natural-home audit, the anti-pattern scan. Only the input handling and the output destination differ.

This is **not** "find a util to call." It is asking whether the proposed (or implemented) work reshapes what already exists in the right way — or bolts a parallel structure where extension or refactoring would have been cleaner.

**Input:** "$ARGUMENTS"

### Reference docs this skill consults

The skill itself stays small. The detail lives in three reference docs alongside this SKILL — read each at the phase that cites it:

- **`plugins/harness/shape-options.md`** — definitions of the 5 shapes (extend / adapt / refactor-first / add-new / flag-gated-rewrite). Read at Phase 4.
- **`plugins/harness/flag-gated-rewrite.md`** — the flag-gated-rewrite shape, both decision-time sub-decisions (§1) and execution mechanics (§2–§5). Read at Phase 4 only when that shape is on the table; the executor reads §2–§5 at implementation time.
- **`plugins/harness/verdict-template.md`** — the verdict body template, file-output frontmatter, and mode-specific revision tails. Read at Phase 6.

The executor (whoever runs the implementation after this review) also consults `plugins/harness/rule-checklist.md` at the end of each task — the verdict references it but this skill itself doesn't need to read it.

---

## Phase 1: Detect Mode and Parse Input

The skill picks one of two modes from `$ARGUMENTS` and the surrounding context. State the detected mode and the input clearly before doing anything else.

### Mode-detection rules

| `$ARGUMENTS` looks like                                                                               | Mode                                                                                       | Output destination                                  |
| ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------- |
| Path to a `.md` plan file                                                                             | **plan-review**                                                                            | New file `<plan>.belongs-here.md` next to the input |
| Empty AND a plan was just generated in the current conversation                                       | **plan-review**                                                                            | Conversation                                        |
| Mentions "PR", "diff", "current changes", "code review", "review my code", or names source-file paths | **code-review**                                                                            | Conversation, plan-shaped                           |
| Empty AND no plan in conversation, but `git diff main...HEAD` or uncommitted changes exist            | **code-review**, default scope = **current PR** (changed files vs `main` plus uncommitted) | Conversation, plan-shaped                           |
| Genuinely ambiguous                                                                                   | **Ask one question:** "Are you reviewing a plan, or reviewing code (e.g. the current PR)?" | per above                                           |

Confirm the mode in writing before moving on, e.g.:

- "Mode: plan-review (input: `docs/exec-plans/active/2026-04-28-foo.md`). Output will be written to `docs/exec-plans/active/2026-04-28-foo.belongs-here.md`."
- "Mode: code-review (scope: 7 files in current PR vs `main`, plus 2 uncommitted)."

### Per-mode parsing

**Plan-review mode.** Read the plan. Extract:

- The change description (what is being built or modified, in plain language).
- The data shape, behavior, and trigger/entry-point of the proposed work — the three axes you'll search the codebase for overlap on.
- The acceptance criteria stated in the plan (inputs accepted, observable output, what's out of scope). If the plan does not state these, that's itself a finding — surface it in Phase 6.

**Code-review mode.** Gather the changed files. Default scope (no explicit scope past mode detection): `git diff --name-only main...HEAD` plus uncommitted changes via `git diff --name-only`. For the shape-decision lens, exclude test files; they're in scope for the anti-pattern scan but not for "what shape did the change choose." Then:

- Identify the change description in one sentence — derived from the diff itself, or from the PR title/description if available via `gh pr view`.
- Identify the data shape, behavior, and trigger/entry-point of the changes — same three axes, derived from what was actually modified.
- Acceptance criteria are usually implicit in code-review mode. If the PR has a description, extract any criteria from there. Otherwise, note "criteria not stated in PR" in the verdict — that itself is a finding.
- If the change feels too big to review coherently (>15 changed files, multiple unrelated changes bundled), pause and ask the user to narrow scope before proceeding.

If acceptance criteria are vague in either mode, ask **one** clarifying question before proceeding. Don't ask three at once and don't proceed on assumption — vague criteria propagate into a vague verdict.

---

## Phase 2: Find the Natural Home

Where does this concern _actually belong_? Don't pick the first plausible folder — list candidates and choose deliberately. The mode shapes what you're checking against:

- **Plan-review mode** — the plan proposes a home (or implies one). You're independently picking the right home, then comparing.
- **Code-review mode** — the code already chose a home. You're independently picking the right home, then asking whether the code's choice agrees.

In both modes the procedure is the same:

1. **List 2-3 candidate locations.** A "location" is a module, package, or class. For each, note in one phrase why it's a candidate (e.g. _"`src/billing/invoices.py` — already owns invoice lifecycle logic"_).

2. **Pick one** with a one-sentence justification. The right home is usually the one that already owns the most-related concept — not the most-empty file.

3. **Reject these signals as bad reasons to pick a home:**
   - "This file is shorter / less scary to edit." (Familiarity bias.)
   - "There's a `utils/` folder." (Utils is where dead code goes to retire.)
   - "It would be its own thing." (Premature isolation; defer this judgment to Phase 4.)

4. **Compare to the plan's / code's choice.** If they agree, note the agreement in one line and continue. If they disagree, that's a finding — the plan or code targeted the wrong home, and the verdict in Phase 6 must call it out.

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

## Phase 4: Diagnose the Shape and Challenge It

Identify which of the five shapes the work (plan or code) implicitly chose. Plans rarely name their shape; code never does. Read the proposed/actual structures and decide which option fits.

Then ask: **was this the right call?** If yes, say so plainly with one-sentence reasoning. If no, name the option that should have been chosen and explain why — that becomes the core of the revise verdict in Phase 6.

State exactly **one** shape. The diagnosis must be specific enough that someone reading it knows what file(s) to open first.

### The five shapes (one-line each)

- **Option A — Extend.** Add a method, parameter, or branch to an existing structure.
- **Option B — Adapt.** Generalize the existing structure slightly so the new case fits cleanly.
- **Option C — Refactor first, then add.** Reshape what's there as a separate-commit refactor, then add the new behavior on top.
- **Option D — Add new.** Create a new structure alongside what exists. Justify why A–C are worse.
- **Option E — Flag-Gated Rewrite.** Build the new behavior alongside the old, gated by a flag, with a removal trigger. The right choice for **behavior changes to existing functionality** when local A/B verification has value.

**Read `plugins/harness/shape-options.md`** for the full definitions — when each option applies, the smells that disqualify it, and the disciplines each one requires. Cite the specific section in the verdict (e.g. "diagnosed as Option B per `shape-options.md` Option B").

### When the diagnosed shape is Option E (Flag-Gated Rewrite)

Three sub-decisions must be captured in the verdict's Toggle-mechanism block. The decisions and their criteria live in **`plugins/harness/flag-gated-rewrite.md` §1**:

- §1.1 — **Scan for an existing flag system** (record what was found, or "none found").
- §1.2 — **Pick the implementation tier** (Tier 0 existing / Tier 1 OpenFeature default / Tier 2 minimal in-codebase).
- §1.3 — **Define the removal trigger** (must be concrete; vague triggers rot).

The execution mechanics (bootstrap commit, deprecation comment template, A/B verification test, removal commit) are in §2–§5 of that same document — referenced in the verdict for the executor to follow at implementation time.

---

## Phase 5: Reject the Anti-Patterns

Before you finalize, scan the proposed plan for these failure modes. If you spot one, revisit Phase 4.

- **Parallel duplicate.** Bolting a new function next to an existing one that does 80% of the same thing. Fix: extract the shared core, parameterize the difference.
- **Risk-driven fork.** Creating a new class because modifying the existing one _feels risky_. The risk is the signal — that file needs better tests or a small refactor before any change. Forking just makes the fragmentation permanent.
- **Flag pile-up.** Adding yet another boolean parameter to a function that's already a flag-soup. The real shape is two functions, a strategy object, or a small state machine.
- **Copy-tweak handler.** Duplicating a handler/route/model and changing a few lines. Extract the shared core or generalize the original — every duplicate is a future bug-fix you'll forget to apply twice.
- **Util drift.** Putting "general" helpers in `utils/` or `common/` because they don't obviously belong elsewhere. They _do_ belong somewhere — find it.

---

## Phase 6: Output the Verdict

The output destination depends on mode + input. The verdict body itself, the file-output frontmatter, and the mode-specific revision tails all live in **`plugins/harness/verdict-template.md`** — read that file before producing the output.

### Output destination (the runtime decision)

- **Plan-review with a file input** → write a new file alongside the input. Name it by inserting `.belongs-here` before the trailing `.md` — so `docs/exec-plans/active/2026-04-28-foo.md` becomes `docs/exec-plans/active/2026-04-28-foo.belongs-here.md`. Use the frontmatter from `verdict-template.md`. _Do not modify the input plan._ The verdict is a separate artifact.
- **Plan-review with conversation input** (no file path) → output the verdict body directly to the conversation. Lead with a one-line preamble naming the plan being reviewed.
- **Code-review** → output the verdict body to the conversation, plan-shaped (the "Concrete code revisions" tail in the template makes it actionable). No file written.

### Fields to capture during Phases 1–5 (so Phase 6 has the data ready)

By the time you reach Phase 6, you should already have collected — for the verdict body to be filled cleanly:

- **Change reviewed** — one sentence (Phase 1).
- **Acceptance criteria** — inputs, observable output, out-of-scope (Phase 1).
- **Natural home + agreement/disagreement with the plan/code's choice** (Phase 2).
- **Existing structures to plug into** — `file:line` citations (Phase 3).
- **Diagnosed shape + whether it was the right call** (Phase 4).
- **For flag-gated-rewrite only:** existing flag system found (or none), implementation tier, flag key, force-OLD instruction, removal trigger (Phase 4 sub-decisions, criteria in `flag-gated-rewrite.md` §1).
- **Anti-patterns scanned** — what was considered and avoided, or what's present (Phase 5).
- **Open questions** — if any.

If any field is missing, fill it in before producing the verdict — don't ship a verdict with placeholder slots.

### What "verdict = revise" requires

If the diagnosed shape was the wrong call, or anti-patterns are present, or the natural home was wrong, the verdict is **revise** — and the mode-specific revision tail in `verdict-template.md` is required. Plan-review mode gets prose plan-revisions ("replace task N with…"); code-review mode gets actionable code-revisions ("`file.py:line` — change X to Y").

---

## Guidelines

- **Read before deciding.** Cite `file:line` for every overlapping structure you mention. A survey without specifics is a guess.
- **Defend "Add new".** It's the easiest option to reach for and the one that quietly accretes parallel code over time. Make it earn its place.
- **Refactor first is not failure.** It's the senior choice when the existing code blocks a clean addition. Two small commits beat one tangled one.
- **One survey, one decision.** Don't list "options" in the output. The survey's job is to _make_ the call, not punt it back to the user. (Open questions are fine if the call genuinely depends on user input — but resist the urge to list everything as a question to avoid committing.)
- **Stay scoped.** The survey is about _this change's_ home. Don't expand into "and also we should refactor the entire module" unless the change demands it. Surface broader observations as open questions, not as part of the recommended decision.
- **If the home is ambiguous, ask.** Picking the wrong home propagates into every later phase. One question to the user is cheaper than redoing the implementation.
