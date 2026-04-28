# Option E — Flag-Gated Rewrite

This document is the comprehensive reference for the Option E (flag-gated-rewrite) shape — both the **decision-time sub-decisions** (consulted by `/belongs-here` Phase 4 when diagnosing or recommending this shape) and the **execution mechanics** (consulted by the executor when implementing it).

Decision-time sub-decisions are in §1 below. Execution mechanics are in §2–§5. The "When this document does NOT apply" section at the end names the cases where this shape is wrong.

---

## 1. Decision-time sub-decisions

When `/belongs-here` settles on Option E (or diagnoses it as the implicit shape of a plan or PR), three sub-decisions must be captured in the verdict. These are policy questions about _how_ to do the flag-gating; the corresponding mechanics are in §2–§5.

### 1.1 Scan for an existing flag system in the project

Before recommending toggle infrastructure, scan the project. If a flag system already exists, the executor must integrate at its boundary instead of adding a parallel one. Look for:

- Imports / usage of: `flipper`, `Flipper.enabled?`, `launchdarkly`, `ldclient`, `unleash`, `flagsmith`, `statsig`, `openfeature`, `posthog`
- Env-var-driven flags: `os.getenv("FEATURE_X")`, `ENV["FEATURE_X"]`
- Custom flag modules: `flags.py`, `lib/flags.rb`, `app/feature_flags.rb`, similar

Record what you found (or "none found") in the verdict's Toggle mechanism block.

### 1.2 Pick the implementation tier

The tiers, lightest first:

- **Tier 0: Existing project flag system** (the result of §1.1). Always preferred when one exists. The `Toggle` design is just "named, type-safe, default-NEW reference to a flag the existing system evaluates."
- **Tier 1: OpenFeature** — recommended default when no flag system exists. Vendor-neutral, in-memory provider for dev/test, swap providers for cloud later.
  - Python: `pip install openfeature-sdk`. Use `InMemoryProvider` for dev/test.
  - Ruby: `gem install openfeature-sdk`. Use the in-memory provider.
- **Tier 2: Minimal in-codebase pattern** (`Toggle` value object + `Feature` enum) — only if you specifically want frozen-snapshot threading semantics (decisions made at boundary, immutable thereafter) AND you don't anticipate cloud-capable rollouts. ~30 lines per language.

Tier 1 is the strong default when no existing system was found. Tier 2 should be defended in writing.

### 1.3 Define the removal trigger

The deprecation comment template in §3 references this trigger verbatim. It must be **concrete**, not "eventually" or "when ready". Vague triggers rot.

- Examples that pass: "after local A/B verification confirms parity", "after 2 weeks default-NEW in production with no rollback signal", "after PR #123 ships".
- Examples that fail: "soon", "when stable", "later", "once we're confident".

---

## 2. Bootstrap commit (separate from the feature commit)

If you are introducing a flag system as part of this work, do it as a **separate commit before the feature commit**:

1. **Commit 1:** add the registry module (`flags.py` / `lib/flags.rb`) with no entries yet, plus any minimal infrastructure (OpenFeature provider config, or the `Toggle` + `Feature` skeleton). Run existing tests to confirm green — no behavior change.
2. **Commit 2:** introduce the actual feature with old/new branches gated by the flag, add the `Feature` entry, write tests for the new path.

Do not bundle. The bootstrap is reusable across future flag-gated rewrites; the feature is a one-off.

If a flag system already exists in the project, skip the bootstrap — integrate at the boundary instead of adding a parallel one.

---

## 3. Deprecation comment template

On the old branch (the `else` arm or the function being shadowed), apply:

```text
DEPRECATED <YYYY-MM-DD>: replaced by `<new_function_or_path>`.
Force OLD via <one-line: how to force OLD locally — e.g. "set FLAG_X=0",
"Toggle.with_old(Feature.X)", "Flipper.disable(:flag_x) in console">.
Remove this branch and the flag once <removal trigger from the review output —
e.g. "validated locally", "after 2 weeks default-NEW with no rollback signal">.
```

The removal trigger must be **concrete**, not "eventually" or "when ready". Vague triggers rot — the criteria for what passes are in §1.3 above. The trigger captured in the `belongs-here` verdict's Toggle-mechanism block is what gets copied into this comment verbatim.

---

## 4. Tests for a flag-gated rewrite

- **Full tests on the NEW path.** This is the primary path going forward; cover happy / edge / error per your implementation plan's test design — see `superpowers:writing-plans` for how that plan is typically generated.
- **Old path keeps its existing coverage.** Don't extend it — it's on borrowed time.
- **At least one A/B verification test.** Run a representative input through both branches and assert equivalence (or assert the intentional difference explicitly). This is what makes the toggle actually useful.
- **Default-NEW test.** Confirm that with no toggle override, the new path runs.

The A/B verification test is the **load-bearing** test for a flag-gated rewrite. Without it, the toggle is just a flag — with it, the toggle becomes a verification harness.

---

## 5. Removal commit (record as a follow-up; do not execute now)

When the removal trigger fires (later, possibly in another session), the cleanup is:

1. Delete the `Feature` entry from the registry.
2. Delete the old branch and any helpers only used by it (compiler/linter will flag callsites).
3. Delete the toggle parameter only if no other features use it.
4. Commit separately from any other work.

Record this as a follow-up in your implementation report (or as a `TODO` linked to the trigger condition) so it doesn't get forgotten.

---

## When this document does NOT apply

Skip the flag-gated rewrite pattern entirely — and therefore skip this document — when:

- The change is **purely additive** (no existing behavior changes) — use Option A (extend) or Option D (add new) instead.
- The change is a **pure refactor** with verified no-behavior-change — green existing tests are the verification; no toggle needed.
- The change is **trivial** (single call site, inspection is sufficient) — toggle infrastructure isn't worth the cost.

`belongs-here` Phase 4 names these rejections explicitly. If you've reached this document for a change where one of those rejections applies, return to `belongs-here` and reconsider.
