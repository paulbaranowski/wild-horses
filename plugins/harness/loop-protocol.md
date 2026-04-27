# Loop Protocol — shared by `/harness:feedback-blockers` and `/harness:reasoning-gaps`

This file documents the post-analysis flow that both commands share: resuming an existing task file, the Phase 4 options menu, the JSON task schema, and the iterative Agent loop.

**Slug substitution.** The orchestrating command substitutes `<slug>` with its own slug — `feedback-blockers` or `reasoning-gaps` — wherever it appears below. The orchestrator also substitutes `<intervention-noun>` with `feedback-blocker` or `reasoning-gap` (singular) in the Task Implementation Prompt.

---

## Resume Check (run before Phase 1)

If `$ARGUMENTS` contains `--resume`, skip all analysis and restart the loop from an existing task file:

1. **Locate the task file:**
   - If a path follows `--resume` (e.g., `--resume docs/exec-plans/active/2026-04-14-a3f2-user-endpoints.<slug>.json`): read that file directly. If the path ends in `.<slug>.md`, read its YAML frontmatter `task_file` field, then validate the pointer: the path must exist, be readable, parse as valid JSON, and contain at least one task with `status` `"pending"` or `"in-progress"`. If any check fails, report a clear error (e.g., "task_file points to X which does not exist" or "JSON at X has no pending tasks") and stop. Otherwise, open the validated JSON file.
   - If no path provided (just `--resume`): scan `docs/exec-plans/active/*.<slug>.json` for files with any task where `status` is `"pending"` or `"in-progress"`. If no JSON matches, fall back to scanning `docs/exec-plans/active/*.<slug>.md` — for each `.md` candidate, read its YAML frontmatter `task_file` field, then validate the pointer: the path must exist, be readable, parse as valid JSON, and contain at least one task with `status` `"pending"` or `"in-progress"`. Discard any candidate that fails any of these checks.
     - If exactly one validated match (from either scan): use it.
     - If multiple validated matches: list them with progress summaries (complete/pending/failed counts) and ask the user to pick one.
     - If no validated matches: report "No in-progress <slug> task files found" and stop.

2. **Validate the task file:** Confirm it has a `tasks` array and `testCommand` fields. If invalid, report the error and stop.

3. **Show resume summary:**
   - Task file path
   - Total / complete / in-progress / pending / failed task counts
   - List the remaining tasks (pending and in-progress) with their id, title, and effort

4. **Ask the user what they'd like to do:**

   > **How would you like to proceed?**
   >
   > 1. **Run all remaining** — Implement all pending/in-progress tasks via automated loop
   > 2. **Run next task only** — Implement just the next pending/in-progress task, then stop
   > 3. **Give feedback** — Review or adjust the plan before continuing
   - **Option 1 (Run all remaining):** Jump directly to **Option 1's Step 4** in Phase 4 below — start the Agent loop with `MAX_ITER` = remaining tasks (pending + in-progress) × 1.5 (rounded up) + 1, using the existing task file path.
   - **Option 2 (Run next task only):** Find the first task with status `"in-progress"` or `"pending"` in the task file. Use a single foreground **Agent tool** call with the **Task Implementation Prompt** below, substituting the task file path. After the Agent returns, re-read the JSON task file, show the updated task summary (complete/pending/failed counts) to the user, and return to step 4.
   - **Option 3 (Give feedback):** Ask the user for their feedback (e.g., reorder tasks, skip a task, modify a task's approach, adjust scope). You (the agent) apply the feedback by directly editing the JSON task file — these are structural edits (reordering, setting status to `"skipped"`, revising a task's `what` field), not code implementation. After updating the file, return to step 4 — show the updated task list and ask again.

5. **Skip Phases 1–4 entirely** — no analysis, no report generation, no options menu.

---

## Phase 4 — Propose

After presenting the merged report from the orchestrator's Phase 3, briefly explain the interventions with trade-offs for each. Then prompt the user:

> **What would you like to do?**
>
> 1. **Save plan and implement all** — Write plan and implement ALL interventions iteratively via automated loop
> 2. **Save plan and fix top intervention** — Write the full remediation plan and implement intervention #1
> 3. **Save full remediation plan** — Write the plan for incremental work
> 4. **Revise** — Provide feedback to refine the analysis or change focus

Before executing any option below, generate a **run ID** by running `openssl rand -hex 2` to produce a 4-character hex string (e.g., `a3f2`). Use this same run ID in all file names produced by this run — this prevents collisions when the command is run multiple times on the same day.

### Option 1: Save plan and implement all

Save the analysis and implement all interventions iteratively. Each intervention is implemented by a foreground Agent tool call within this conversation — the user sees every file read, edit, and test run as it happens. The JSON task file tracks progress between iterations.

**Step 1 — Discover the test command.** Check CLAUDE.md for the project's test command. Fall back to `uv run pytest` or `npm test`.

**Step 2 — Write the markdown report** to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.md` (where YYYY-MM-DD is today's date and `<run-id>` is the 4-character hex run ID).

Use YAML frontmatter for metadata:

```yaml
---
status: in-progress
task_file: "docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.json"
generated: "YYYY-MM-DDTHH:MM:SSZ"
---
```

The body contains the full report from Phase 3: Scope (with repo-relative file paths), Ratings Summary, Cross-Pillar/Cross-Dimension Findings, Findings by Severity, Interventions (with full details), and Coverage Check. This is the human-readable artifact — the loop does NOT modify this file.

**Step 3 — Write the JSON task file** to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.json`.

This is the machine-readable task list that the loop reads and writes for state tracking. Convert the absolute file paths from Phase 1 to repo-relative paths for the `scope` array (strip the repository root prefix — e.g., `/Users/name/project/src/pipeline.py` becomes `src/pipeline.py`). Extract each intervention into a task. For interventions tagged `createsNewCode: true`, place a paired test task immediately after.

Schema (illustrative example — actual tasks come from Phase 3 interventions):

```json
{
  "plan": "docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.md",
  "testCommand": "<discovered test command>",
  "scope": ["<repo-relative file paths from Phase 1>"],
  "tasks": [
    {
      "id": 1,
      "title": "<intervention title>",
      "what": "<specific change — files to modify, structures to create, patterns to fix>",
      "resolves": ["<file:line>", "<file:line>"],
      "effort": "low | medium | high",
      "createsNewCode": true,
      "status": "pending",
      "acceptanceCriteria": [
        "<concrete, verifiable criterion>",
        "<another criterion>",
        "Tests pass"
      ],
      "log": null
    },
    {
      "id": 2,
      "title": "Write tests for <thing created in task 1>",
      "what": "<what to test, where to put tests>",
      "resolves": [],
      "effort": "low",
      "createsNewCode": false,
      "status": "pending",
      "acceptanceCriteria": [
        "Test file follows project test conventions",
        "At least N test cases covering happy path, errors, and edge cases",
        "Tests pass"
      ],
      "log": null
    }
  ]
}
```

Note: tasks with `createsNewCode: true` get a paired test task immediately after. Tasks with `createsNewCode: false` (annotation-only or restructuring-only) do not.

Field definitions:

- `testCommand` — project test command, discovered once and reused every iteration
- `scope` — repo-relative file paths from Phase 1, preserved for potential re-analysis. Use paths relative to the repository root to avoid leaking local machine structure if the file is committed
- `createsNewCode` — `true` if the intervention creates new callable code (functions, classes, methods, services, models, protocols), `false` if it only restructures, annotates, or documents existing code. Determines whether a paired test task is generated
- `acceptanceCriteria` — derived from the intervention's What and Resolves fields. Each criterion should be concrete and verifiable
- `status` — `"pending"` | `"in-progress"` | `"complete"` | `"failed"`
- `log` — `null` when pending; a string describing what was done (or what went wrong) when in-progress/complete/failed

**Step 4 — Implement tasks via Agent loop.**

Implement each task using sequential foreground Agent tool calls. Each iteration dispatches one task to an Agent that runs within this conversation, so the user sees every file read, edit, and test run in real time.

Set `MAX_ITER` to the number of tasks multiplied by 1.5 (rounded up) plus 1. For example, 10 tasks → 16 max iterations.

Execute the following loop. On each iteration:

1. Read the JSON task file. Count tasks with status `"pending"` or `"in-progress"`.
2. If none remain, the loop is done. Show final status:
   - If any tasks have status `"failed"`: "Done with failures: X/Y complete, Z failed"
   - Otherwise: "All Y tasks complete"
3. If `MAX_ITER` is reached, show "Max iterations (MAX_ITER) reached" and stop.
4. Show a progress header to the user: "Iteration X/MAX_ITER — N tasks remaining"
5. Use the **Agent tool** (foreground, not background) with the **Task Implementation Prompt** below, substituting the actual task file path.
6. After the Agent returns, re-read the JSON task file. If the task that was in-progress was not updated (status is still `"in-progress"` with no log change), log a warning: "Agent did not update task status on iteration X, continuing" and proceed.
7. Repeat from step 1.

**IMPORTANT:** Issue Agent tool calls **one at a time, sequentially**. Do NOT launch multiple Agent calls in parallel for task implementation — each task may depend on changes from the previous task. Wait for each Agent to complete before starting the next iteration.

**Step 5 — Show final summary.**

After the loop completes (all tasks done or max iterations reached), read the JSON task file and show the user:

- A task status table: each task's id, title, and status
- Plan location: `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.md`
- Task file location: `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.json`
- If any tasks failed or remain pending: suggest re-running with `--resume`

### Option 2: Save plan and fix top intervention

- Write the full remediation plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.md` including scope, all findings, all interventions with details
- Implement intervention #1
- Run existing tests (check CLAUDE.md for the test command, fallback to `uv run pytest` or `npm test`) to verify nothing breaks
- If tests fail, fix forward or revert and explain what went wrong
- Present a before/after summary showing the improvement

### Option 3: Save full remediation plan

- Write the full remediation plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.md` including scope, all findings, all interventions with details and effort estimates
- Do NOT implement anything

### Option 4: Revise

- Ask the user for feedback (different focus area, scope change, alternative priorities, additional context)
- Revise the analysis based on their input
- Present the updated report and prompt with the same four options

---

## Task Implementation Prompt

Pass this prompt to each Agent tool call, replacing `TASK_FILE_PATH` with the actual path to the JSON task file from Step 3, and `<intervention-noun>` with `feedback-blocker` or `reasoning-gap`:

> You are implementing <intervention-noun> interventions. Read the task file at TASK_FILE_PATH (JSON). Find the first task with status "in-progress" or "pending". Set it to "in-progress" and write the file immediately. Read the `what`, `resolves`, and `acceptanceCriteria` fields. Implement the change. Verify all acceptance criteria are met. Run tests using the `testCommand` from the task file. If tests pass, set status to "complete" with a log summary and commit. If tests fail, fix forward or set status to "failed" with a log. Commit. Implement exactly ONE task per iteration. IMPORTANT: When committing, stage only the source files you changed — do NOT stage the task file (TASK_FILE_PATH) or any docs/exec-plans/ files. These are metadata for loop tracking, not deliverables.
