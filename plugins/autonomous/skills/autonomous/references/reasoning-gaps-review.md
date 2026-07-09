<!--
Slim autonomous review pass derived from /harness:reasoning-gaps Phases 1–3.
Deliberately omits Phase 4 (task-list-builder/runner, interventions, coverage
check, paired test tasks). Autonomous fixes only major gaps before opening a PR;
deferrals go in the PR Decisions section.

Agent prompts live in the harness plugin — resolve with Glob:
  **/harness/agents/reasoning-gaps/*.md
(dev checkout: plugins/harness/...; installed cache: .../wild-horses/harness/<version>/...)
-->

# Reasoning Gaps Review (autonomous)

Pre-PR pass: would a fresh AI agent misread the code you just wrote? Uses the
same three specialist lenses as `/harness:reasoning-gaps`, but scoped to the
branch diff and triaged to **critical findings only** (plus a narrow important
band). This is not a code-quality review — it catches opacity that slipped past
implementation and simplify.

## Scope

Build a newline-separated list of **absolute file paths** for changed source
files on this branch:

1. `git diff --name-only main...HEAD` (or `master...HEAD` if that is the default)
2. Plus uncommitted changes: `git diff --name-only`
3. Exclude test files (`test_`, `_test.`, `.test.`, `tests/`, `__tests__/`)
4. If the list is empty, skip this review entirely
5. If more than 15 files, narrow to files touched in the most recent commit(s)
   for this task — do not analyze the whole repo

Read the target repo's CLAUDE.md / AGENTS.md if present — agents need project
conventions.

## Spawn 3 specialist agents (parallel)

**CRITICAL:** Launch all three agents in a **single** response with exactly 3
Agent tool calls. Do not run them sequentially.

Locate the harness agent prompt templates with Glob
`**/harness/agents/reasoning-gaps/*.md`. For each agent below, read the
indicated file, substitute `{paste relevant CLAUDE.md sections here}` and
`{paste the file list here}` inside the fenced `text` block, and pass the
result as the Agent prompt. Agents read the files themselves — do not paste
file contents into prompts.

| Agent                             | Prompt file (under harness plugin)                     |
| --------------------------------- | ------------------------------------------------------ |
| Type & Data Contract Analyst      | `agents/reasoning-gaps/types-and-data-contracts.md`    |
| Implicit Flow & State Analyst     | `agents/reasoning-gaps/implicit-flow-and-state.md`     |
| Structure & Documentation Analyst | `agents/reasoning-gaps/structure-and-documentation.md` |

If the harness plugin is not installed and Glob finds no prompts, skip this
review and note `"Reasoning-gaps review skipped: harness plugin not available"`
in the PR Decisions section.

## Merge (orchestrator)

After all three agents return:

1. **Verify every finding** — read each cited file:line; discard findings whose
   quoted code does not match. Correct slightly inaccurate descriptions.
2. **Deduplicate** — merge same file:line from multiple agents; note which
   dimensions overlap (cross-dimension = higher confidence).
3. **Do not** build a full interventions list, coverage check, or task list.
   That belongs to a standalone `/harness:reasoning-gaps` run.

## Triage (autonomous gate — major issues only)

The full reasoning-gaps command fixes every critical, important, and minor
finding. Autonomous does **not**. Apply this gate so the pass stays bounded:

| Severity      | Action                                                                                                                                                                                                                                                                                     |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Critical**  | **Must fix** before opening the PR. Every verified critical finding gets a code change.                                                                                                                                                                                                    |
| **Important** | Fix only when **both** (a) and (b): **(a)** the finding is cross-dimension (2+ agents flagged the same location) **or** it sits on a public/exported API boundary; **and (b)** the concrete fix is annotation, docstring, or a small local type — no multi-file refactor. Otherwise defer. |
| **Minor**     | **Skip.** Do not fix, do not list individually.                                                                                                                                                                                                                                            |

When deferring important findings (out of scope for this pass), summarize them
in the PR **Decisions** section under "Deferred reasoning gaps" — one line each
with file:line and why deferred (e.g. "needs TypedDict extraction across 4
files — run `/harness:reasoning-gaps` post-merge").

## Fix and validate

Fix every must-fix item directly in code — same as simplify, not a separate
implementation loop. If a fix changes behavior, re-run the project's tests before
continuing to the commit + `core:cb-review` step.

Do not spawn task-list-runner, do not invoke `/harness:reasoning-gaps` Phase 4,
and do not attempt to clear the entire findings backlog in this pass.
