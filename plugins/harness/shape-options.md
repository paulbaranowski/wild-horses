# Shape Options — the 5-shape decision tree

This document holds the canonical definitions of the five shapes a code change can take. The `/belongs-here` skill cites this file at Phase 4 (Diagnose the Shape and Challenge It); the executor consults it whenever a verdict references a shape decision.

A change always falls into one of these five shapes. Plans rarely name their shape; code never does. The diagnostician's job is to identify which shape the proposed (plan-review mode) or actual (code-review mode) work represents — then ask whether it was the right call given what already exists in the codebase.

State exactly **one** shape per review. The diagnosis must be specific enough that someone reading it knows what file(s) to open first.

---

## Option A — Extend

Add a new method, parameter, or branch to an existing structure.

- **Use when:** the new behavior is a natural variant of existing behavior (same inputs, slightly different output; same output, slightly different inputs; same operation, new caller).
- **Smell to reject:** if extending requires a new flag parameter on a function that's _already_ branching on flags, you're heading for a god-function — switch to **Adapt** or **Refactor first**.

---

## Option B — Adapt

Generalize the existing structure slightly so the new case fits cleanly.

- **Use when:** the existing structure is _almost_ right but hardcodes something the new case needs to vary. Promote the constant to a parameter, widen a type, parameterize a strategy, accept an interface instead of a concrete class.
- **Test:** after the adaptation, the existing callers should still work _without changes to their call sites_ (or with trivial type-driven changes). If the adaptation breaks all callers, you're really doing **Refactor first**.

---

## Option C — Refactor first, then add

The existing code blocks a clean addition. Do a small refactor (extract method, introduce a seam, split a class, replace conditional with polymorphism) as a **separate first step** with no behavior change, then add the new behavior on top.

- **Use when:** your honest assessment is "I can't add this cleanly without touching the shape of what's there."
- **Discipline:** the refactor is one commit (existing tests still green, no new tests), the addition is a second commit (new tests, new behavior). Two commits, not one.

---

## Option D — Add new

Create a new structure (file, class, function) alongside what exists.

- **Use when:** no existing structure fits without distortion AND you've considered Options A–C and rejected each with a concrete reason.
- **Defend it:** "Add new" is the choice that has to be _justified_. State why extension/adaptation/refactor would be worse here. If the only reason is "easier", you're choosing the wrong option.

---

## Flag-Gated Rewrite

Build the new behavior alongside the old, gated by a flag, with a removal trigger. This is the right choice for **behavior changes to existing functionality** when you want a local A/B verification path before committing to the new path.

- **Use when:** the change alters the _observable behavior_ of existing functionality AND you want a local A/B verification path — letting the developer or AI agent flip between OLD and NEW to confirm equivalence (or intentional difference) before committing. Production rollout via the same toggle is a secondary, optional benefit.
- **Required properties of the toggle mechanism** (the implementation must satisfy all five):
  1. **Type-safe flag references** — named constants/enums/symbols in one centralized registry, not scattered string literals.
  2. **Default is NEW** — OLD is opt-in for verification.
  3. **Local-verification path** that doesn't require touching production config (an AI or dev can force OLD with a parameter, env var, or in-memory provider).
  4. **Deprecation comment on the old branch** naming the replacement, how to force OLD, and the removal trigger.
  5. **Removal as a separate follow-up commit** — delete the registry entry and the old branch together once validated.
- **Don't use when:** purely additive new feature (no existing behavior changes — go to A or D); pure refactor with verified no-behavior-change (green existing tests are the verification); trivial single-call-site change where inspection is enough.
- **Decision-time sub-decisions and execution mechanics:** see `plugins/harness/flag-gated-rewrite.md`. That document covers (1) scanning for an existing flag system, (2) picking the implementation tier (Tier 0 existing / Tier 1 OpenFeature / Tier 2 minimal in-codebase), (3) defining the removal trigger, plus the bootstrap commit, deprecation comment template, A/B verification test, and removal commit.
