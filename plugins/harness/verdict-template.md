# Verdict Template — `/belongs-here` output

This document holds the canonical output template for `/belongs-here`. The skill cites this file at Phase 6. Use the template below verbatim, filling each bracketed slot. Omit sections marked _(only if …)_ when their condition isn't met.

The template body is identical across all three output destinations (file output for plan-review with a path, conversation for plan-review without a path, conversation for code-review). Only the wrapping differs:

- **Plan-review with a file input** → the body goes in a new file `<input-plan>.belongs-here.md`, preceded by frontmatter (see "File-output frontmatter" below). Do not modify the input plan.
- **Plan-review with conversation input** → the body goes directly to the conversation, preceded by a one-line preamble naming the plan being reviewed.
- **Code-review** → the body goes directly to the conversation. The "Concrete code revisions" tail (only when verdict = revise) is what makes the output plan-shaped — actionable file:line directives a user or executor can apply.

---

## File-output frontmatter

```yaml
---
reviewed: <relative path to the input plan>
generated: <ISO 8601 timestamp>
verdict: approved | revise
mode: plan-review
---
```

---

## Verdict body (used in all three destinations)

```markdown
## Belongs Here — [approved | revise]

**Change reviewed:** [one sentence — what the plan or code is doing]

**Mode:** plan-review | code-review
**Source:** [path to plan file | "plan in conversation" | "current PR (N files vs main, M uncommitted)"]

**Acceptance criteria:**

- **Inputs:** [what the work accepts]
- **Observable output / behavior:** [what an outside caller sees]
- **Out of scope:** [explicit guardrails against gold-plating]
- _(If criteria were absent from the input, note this here as a finding — vague criteria are a blocker for execution.)_

**Natural home (independent assessment):** `path/to/module.py` (or class `Foo` within it)

- [one-sentence justification — why this home over the other candidates]
- **Vs. plan/code's choice:** agreed — [one line] / disagreed — [the plan or code targeted X, but Y is the right home because Z]

**Diagnosed shape:** extend / adapt / refactor-first / add-new / flag-gated-rewrite

- [one-sentence justification — what makes this the shape implicit in the plan or code]
- **Right call?** yes — [reasoning] / no — should have been **<other-option>** because [reasoning]. _(For "no", this finding drives the revisions section below.)_

**What this means concretely (for the recommended shape):**

- [for Extend/Adapt: name the existing structure and the specific change to it]
- [for Refactor first: name the refactor as step 1, then the addition as step 2]
- [for Add new: name the new structure and where it lives, and why A/B/C were rejected]
- [for Flag-Gated Rewrite: name the new path, the old path, and the flag that selects between them]

**Existing structures to plug into:**

- `file.py:line` — [type/function/pattern and how the work uses it]
- ...

**Toggle mechanism (only if Diagnosed shape is flag-gated-rewrite):**

- **Existing flag system in project:** [name + entry point, or "none found"] (see `plugins/harness/flag-gated-rewrite.md` §1.1)
- **Implementation tier:** [Tier 0: existing | Tier 1: OpenFeature | Tier 2: minimal in-codebase] — one-sentence why (see §1.2)
- **Flag/feature key:** `<flag-name>`
- **How to force OLD locally:** [one-line — e.g. "set env var FLAG_X=0", "Toggle.with_old(Feature.X)", "Flipper.disable(:flag_x)"]
- **Removal trigger:** [concrete condition; see §1.3 for examples that pass vs. fail]
- **Mechanics reference:** the executor follows `plugins/harness/flag-gated-rewrite.md` §2–§5 for bootstrap commit pattern, deprecation comment template, A/B verification test, and removal commit.

**Anti-patterns scanned:**

- [name each anti-pattern that was a real risk for this work, and either "considered and avoided by <choice>" or "PRESENT — <where> — fix: <how>"]

**Rule-checklist reference:** the executor walks `plugins/harness/rule-checklist.md` (reasoning-gaps + feedback-blockers self-check) at the end of each implementation task.

**Open questions (if any):**

- [anything that needs a human call before the verdict can be acted on]
```

---

## Mode-specific revision tail (only when verdict = revise)

For **verdict = approved** in either mode, the body alone is the output. No tail needed — short answers are fine when they're correct.

For **verdict = revise** in **plan-review** mode, append:

```markdown
**Plan revisions:**

- Replace task N (currently "...") with: "..." because [one-sentence reason]
- Add task: "..." after task N because [reason]
- Remove task N because [reason]
- ...
```

For **verdict = revise** in **code-review** mode, append:

```markdown
**Concrete code revisions:**

- `file.py:line` — change [what] to [what] because [one-sentence reason]
- `file.py:line` — extract [function] into [target] because [reason]
- ...
```

The code-review revision list is the actionable plan the user (or `superpowers:executing-plans`) operates on. Each entry should be tight enough to be a single small commit.
