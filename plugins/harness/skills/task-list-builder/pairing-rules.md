# Pairing Rules — Reference for `task-list-builder`

This file is the full reference for **rule 2** (paired test tasks) and **rule 3** (don't bury test work) from `SKILL.md` Phase 4. **Read this before classifying tasks.**

The summary in `SKILL.md` is intentionally short. The lists, the rationalization table, and the rewrite-mode algorithm below are what you actually use during classification.

---

## When to pair a test task

A task **modifies runtime behavior** (and therefore needs a paired test task immediately after) if it:

- Adds, removes, or restructures control-flow branches (guards, error handling, retry logic, gates, finally blocks)
- Adds, removes, or changes state updates, dispatches, side effects, or return shapes
- Changes API contract (request/response shape, status code handling, retry/backoff)
- Adds runtime validation (rejects new inputs, narrows accepted values)
- Changes timing or scheduling (sync↔async, batched↔immediate, polling intervals, refs updated synchronously vs. in effect)
- Modifies user-visible UI behavior (navigation paths, popup visibility, button states, error messages, i18n keys, copy)
- Changes cache invalidation, query tagging, or which observers refire

## When NOT to pair

A task does **NOT** modify runtime behavior if it is purely:

- A type rename, type annotation, or non-load-bearing type change with no runtime effect
- A comment, JSDoc, or documentation edit
- Dead-code removal verified unreferenced
- A CSS/className/copy typo fix not covered by automated tests
- A code restructure with provably preserved behavior **AND** existing tests already cover the affected surface
- A task that itself adds, edits, or deletes tests (these are first-class — never paired)

## Why this is broader than `loop-protocol.md:121`

The rule in `loop-protocol.md:121` (`createsNewCode: true` → paired test) was designed for `/harness:reasoning-gaps` and `/harness:feedback-blockers` outputs, where new-code interventions dominate. `task-list-builder` accepts wider inputs (PR reviews, refactor lists, conversation context) where most behavior changes touch existing code. The output JSON is still schema-compatible — the loop runner consumes it the same way; only the generation rule is wider.

---

## Rationalization table

These reasonings have produced incorrect output in past runs. If you catch yourself using one of them, stop and re-classify.

| Rationalization                                                                                           | Reality                                                                                                                                                                                                                                                                                |
| --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "All tasks are `createsNewCode: false`, so no paired test tasks are needed (per `loop-protocol.md:121`)." | Pairing triggers on behavior modification, not on `createsNewCode`. The `loop-protocol.md` rule is intentionally widened here. If the input has many behavior-modifying refactors and you've added zero paired tests, you've under-paired — re-scan against the inclusion list.        |
| "Every fix is a refactor / type tightening / doc edit, so no tests."                                      | Refactors that change observable behavior (control flow, error handling, dispatches, return shapes, error messages) need regression tests. "Refactor" ≠ "no behavior change". Apply the inclusion list per task, not as a blanket label.                                               |
| "I'll add `'Test asserts X'` to the acceptance criteria — that covers it."                                | No. That criterion describes hidden engineering work (fixture, mocks, arrange/act/assert) bundled into an implementation task. The loop runner can't cleanly retry a half-failed implement-and-test envelope. Extract it into the paired test task per rule 3.                         |
| "Only one of these tasks really creates new code, so only that one gets a pair."                          | Possibly correct under the old rule, almost always wrong under the broadened rule. Re-check each task against the inclusion list independently.                                                                                                                                        |
| "This is a small change, the existing tests will catch any regression."                                   | If you're confident existing tests cover it, the task fits the exclusion bullet "code restructure with provably preserved behavior AND existing tests already cover the affected surface" — but only if you can name the existing test that would fail. If you can't, pair a new test. |
| "`loop-protocol.md` is the source of truth and it says `createsNewCode` triggers pairing."                | The schema (field names, types, structure) is the source of truth and unchanged. The pairing _rule_ is a generation-time guideline that this skill intentionally widens. Schema-level compatibility with the loop runner is preserved either way.                                      |

---

## Red flags — stop and re-classify

- A 20+ task plan generated from a code review with **zero** paired test tasks. Almost always means the pairing trigger was misapplied.
- More than ~3 implementation tasks in a row whose acceptance criteria contain `"Test asserts"`, `"New unit test"`, or `"Test fails if"` strings. Extract them into paired test tasks.
- A task that changes control flow, error handling, dispatches, navigation, popups, or i18n keys but has no paired test task immediately after.
- A task labeled "refactor" or "tighten types" that in fact changes the runtime branch a value flows down. That's behavior modification — pair it.

---

## Rewrite mode — retroactively apply rules 2 and 3

When rewriting an existing plan, do not just preserve the old structure. Apply rules 2 and 3 to the existing tasks:

1. For each existing task, classify it against the behavior-modification inclusion list above.
2. If it modifies behavior and has no paired test task immediately after it, **insert one**.
3. Scan its acceptance criteria for buried test work patterns: `"Test asserts X"`, `"New unit test"`, `"Test fails if X is reverted"`, `"Test mocks Y and asserts Z"`. For each match:
   - Remove the line from the implementation task's criteria.
   - Put it on the paired test task as its acceptance criterion.
   - The paired test's `what` field describes the test setup (fixture, mocks, what to assert).
4. **Renumber** all subsequent task ids after each insertion.
5. Show the splits explicitly in the Phase 5 preview, e.g. `Split task 7 → 7+8: extracted test work into paired task`.
