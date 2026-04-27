# Option E — Parallel-new-with-Toggle: Mechanics Reference

This document describes **how to apply** the parallel-new-with-toggle pattern when implementing a change whose `guru-dev-review` decision was **Option E**. The decisions themselves (whether to use Option E, which flag-system tier, what the removal trigger is) are made earlier — see `plugins/harness/skills/guru-dev-review/SKILL.md` Phase 4 / Option E.

This is a write-time pattern reference. Read it when you are about to implement an Option E change. The four sections below — bootstrap, deprecation comment, tests, removal — are applied in order.

---

## 1. Bootstrap commit (separate from the feature commit)

If you are introducing a flag system as part of this work, do it as a **separate commit before the feature commit**:

1. **Commit 1:** add the registry module (`flags.py` / `lib/flags.rb`) with no entries yet, plus any minimal infrastructure (OpenFeature provider config, or the `Toggle` + `Feature` skeleton). Run existing tests to confirm green — no behavior change.
2. **Commit 2:** introduce the actual feature with old/new branches gated by the flag, add the `Feature` entry, write tests for the new path.

Do not bundle. The bootstrap is reusable across future Option E rewrites; the feature is a one-off.

If a flag system already exists in the project, skip the bootstrap — integrate at the boundary instead of adding a parallel one.

---

## 2. Deprecation comment template

On the old branch (the `else` arm or the function being shadowed), apply:

```text
DEPRECATED <YYYY-MM-DD>: replaced by `<new_function_or_path>`.
Force OLD via <one-line: how to force OLD locally — e.g. "set FLAG_X=0",
"Toggle.with_old(Feature.X)", "Flipper.disable(:flag_x) in console">.
Remove this branch and the flag once <removal trigger from the review output —
e.g. "validated locally", "after 2 weeks default-NEW with no rollback signal">.
```

The removal trigger must be **concrete**, not "eventually" or "when ready". Vague triggers rot. The trigger comes from `guru-dev-review`'s Phase 4 / Option E output — copy it verbatim.

---

## 3. Tests for an Option E change

- **Full tests on the NEW path.** This is the primary path going forward; cover happy / edge / error per your implementation plan's test design — see `superpowers:writing-plans` for how that plan is typically generated.
- **Old path keeps its existing coverage.** Don't extend it — it's on borrowed time.
- **At least one A/B verification test.** Run a representative input through both branches and assert equivalence (or assert the intentional difference explicitly). This is what makes the toggle actually useful.
- **Default-NEW test.** Confirm that with no toggle override, the new path runs.

The A/B verification test is the **load-bearing** test for Option E. Without it, the toggle is just a flag — with it, the toggle becomes a verification harness.

---

## 4. Removal commit (record as a follow-up; do not execute now)

When the removal trigger fires (later, possibly in another session), the cleanup is:

1. Delete the `Feature` entry from the registry.
2. Delete the old branch and any helpers only used by it (compiler/linter will flag callsites).
3. Delete the toggle parameter only if no other features use it.
4. Commit separately from any other work.

Record this as a follow-up in your implementation report (or as a `TODO` linked to the trigger condition) so it doesn't get forgotten.

---

## When this document does NOT apply

Skip Option E entirely — and therefore skip this document — when:

- The change is **purely additive** (no existing behavior changes) — use Option A (extend) or Option D (add new) instead.
- The change is a **pure refactor** with verified no-behavior-change — green existing tests are the verification; no toggle needed.
- The change is **trivial** (single call site, inspection is sufficient) — toggle infrastructure isn't worth the cost.

`guru-dev-review` Phase 4 names these rejections explicitly. If you've reached this document for a change where one of those rejections applies, return to `guru-dev-review` and reconsider.
