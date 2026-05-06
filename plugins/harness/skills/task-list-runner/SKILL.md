---
name: task-list-runner
description: Run a structured task list (JSON file in the harness loop-protocol schema) by dispatching each task to a foreground Agent in sequence. Auto-locates an in-progress task file in docs/exec-plans/active/ when no path is given. Use when the user says "run the plan", "resume the plan", "execute the tasks", "run task-list-runner", or otherwise asks to drive an existing harness task list to completion. Pairs with task-list-builder, which produces the JSON.
user-invocable: true
disable-model-invocation: false
argument-hint: "[path to .json or .md task file] [--all | --next]"
---

# task-list-runner

Drive a harness task list (JSON file matching the `loop-protocol.md` schema) to completion by dispatching each task to a foreground `Agent` tool call, one at a time. Pairs with `task-list-builder`, which produces the JSON.

The schema this skill consumes is defined in `${CLAUDE_PLUGIN_ROOT}/loop-protocol.md` (see "JSON task schema"). Re-read that file rather than relying on memory.

**Arguments:** `$ARGUMENTS`

---

## Phase 1 — Parse arguments

From `$ARGUMENTS`, extract:

- **Path** — the first non-flag argument, if any. May point to a `.json` (read directly) or `.md` file (read its YAML frontmatter `task_file` field, which points to the JSON).
- **Mode flag** — `--all` (run every remaining task non-interactively), `--next` (run exactly one pending/in-progress task and stop), or absent (interactive: show the menu in Phase 3).

If the path is a `.md` file, validate the pointer: the JSON it points to must exist, parse, and contain at least one task with status `"pending"` or `"in-progress"`. If validation fails, report a clear error (e.g., `"task_file points to X which does not exist"` or `"JSON at X has no pending tasks"`) and stop.

---

## Phase 2 — Locate the task file (if no path was given)

If Phase 1 yielded no path, auto-locate by content (not filename):

1. Scan `docs/exec-plans/active/*.json`. For each file, validate that it:
   - parses as valid JSON,
   - has a `tasks` array and a `testCommand` field, and
   - contains at least one task with `status` of `"pending"` or `"in-progress"`.
2. If no JSON matches, fall back to scanning `docs/exec-plans/active/*.md` — for each candidate, read its YAML frontmatter `task_file` field and validate the JSON it points to using the same checks.
3. Resolve:
   - **Exactly one validated match** (from either scan): use it.
   - **Multiple validated matches:** list them with progress summaries (complete/pending/failed counts) and ask the user to pick one. Do NOT pick by recency or alphabetical order.
   - **No validated matches:** report `"No in-progress task files found in docs/exec-plans/active/"` and stop. Do NOT try to build a new task list — that is `task-list-builder`'s job.

From here on, "the task file" means the chosen JSON.

---

## Phase 3 — Show summary and choose mode

Re-validate the task file: it must have a `tasks` array and a `testCommand` field. If invalid, report and stop.

Show the user:

- Task file path
- Total / complete / in-progress / pending / failed counts
- The remaining tasks (pending + in-progress) with their `id`, `title`, and `effort`

Then branch on the Phase 1 mode flag:

- **`--all`** → skip the menu, jump to Phase 4 with mode = `all`.
- **`--next`** → skip the menu, jump to Phase 4 with mode = `next`.
- **No flag (interactive)** — prompt:

  > **How would you like to proceed?**
  >
  > 1. **Run all remaining** — Implement every pending/in-progress task via automated loop
  > 2. **Run next task only** — Implement just the next pending/in-progress task, then stop
  > 3. **Give feedback** — Review or adjust the plan before continuing
  - **Option 1** → Phase 4 with mode = `all`.
  - **Option 2** → Phase 4 with mode = `next`.
  - **Option 3** — Ask the user for their feedback (reorder tasks, skip a task, modify a task's `what` field, adjust scope). Apply the feedback by editing the JSON directly — these are structural edits (revise `what`, reorder, or mark a task as skipped), not code changes. To skip a task, set its `status` to `"complete"` and put `"skipped: <reason>"` in its `log` field — the schema's status enum is `"pending" | "in-progress" | "complete" | "failed"` and does not include `"skipped"`. After saving, return to the start of Phase 3 (re-show summary, prompt again).

---

## Phase 4 — Agent loop

Implement tasks via sequential foreground `Agent` tool calls. Each Agent runs _within this conversation_ — the user sees every file read, edit, and test run in real time.

### Mode = `all`

1. Compute `MAX_ITER` = (number of tasks with status `"pending"` or `"in-progress"`) × 1.5, rounded up, plus 1. Example: 10 remaining → `MAX_ITER = 16`.
2. Run the loop. On each iteration:
   1. Read the JSON. Count tasks with status `"pending"` or `"in-progress"`.
   2. If none remain, the loop is done. Show final status:
      - Any `"failed"` tasks → `"Done with failures: X/Y complete, Z failed"`.
      - Otherwise → `"All Y tasks complete"`.
   3. If `MAX_ITER` is reached → `"Max iterations (MAX_ITER) reached"` and stop.
   4. Show progress header: `"Iteration X/MAX_ITER — N tasks remaining"`.
   5. **Issue a single foreground `Agent` tool call** with the **Task Implementation Prompt** below, substituting the task file path. Do NOT issue multiple `Agent` calls in parallel — tasks may depend on prior tasks' edits. Wait for it to return.
   6. **Strict-parse the task file** with `python3 -c "import json; json.load(open('TASK_FILE_PATH'))"` (or `jq . TASK_FILE_PATH > /dev/null`). If parsing fails, **halt the loop** and report: `"Task file became invalid JSON during iteration X — stopping before further writes compound the corruption. Inspect the file and fix syntax, then resume."` Do NOT proceed to step 7 with a malformed file — subsequent agents will keep writing to it.
   7. Re-read the JSON. If the task that was in-progress wasn't updated (status still `"in-progress"` with no `log` change), warn: `"Agent did not update task status on iteration X, continuing"` and proceed.
   8. Repeat from step 1.

### Mode = `next`

1. Find the first task with status `"in-progress"` or `"pending"` in the JSON.
2. Issue a single foreground `Agent` tool call with the Task Implementation Prompt, substituting the task file path.
3. After the Agent returns, re-read the JSON, show the updated counts (complete/pending/failed), and stop.

---

## Phase 5 — Final summary

After the loop completes (all tasks done, max iterations reached, or `--next` finished), read the JSON one more time and show:

- A task status table: each task's `id`, `title`, and `status`.
- Plan markdown path (from the JSON's `plan` field, if present).
- Task file path.
- If any tasks failed or remain pending: suggest re-running the skill (with `--all` to continue, or with no flag to revise first).

---

## Task Implementation Prompt

Pass this verbatim to each `Agent` tool call, replacing `TASK_FILE_PATH` with the absolute path to the JSON task file:

> You are implementing one task from a structured task list. Read the task file at `TASK_FILE_PATH` (JSON). Find the first task with status `"in-progress"` or `"pending"`. Set its status to `"in-progress"` and write the file immediately. Read the `what`, `resolves`, and `acceptanceCriteria` fields. Implement the change. Verify all acceptance criteria are met. Run tests using the `testCommand` from the task file. If tests pass, set status to `"complete"` with a `log` summary and commit. If tests fail, fix forward or set status to `"failed"` with a `log` describing what went wrong. Commit. Implement exactly ONE task per iteration.
>
> **JSON update discipline (mandatory).** Never use `Edit` or `Write` to modify the task file directly — hand-edited JSON loses structural punctuation (missing `},` between tasks, unescaped quotes inside long `log` strings, etc.) and the corruption survives silently across many iterations. For every task-file mutation, go through a strict parser:
>
> ```bash
> python3 -c '
> import json, sys
> p = "TASK_FILE_PATH"
> d = json.load(open(p))
> # locate the task by id and mutate fields in-place, e.g.:
> # next(t for t in d["tasks"] if t["id"] == 17)["status"] = "complete"
> # next(t for t in d["tasks"] if t["id"] == 17)["log"] = "..."
> json.dump(d, open(p, "w"), indent=2)
> '
> ```
>
> Equivalent `jq` is fine. After every write, verify the file still parses by running `python3 -c "import json; json.load(open('TASK_FILE_PATH'))"` (exit code 0). If parsing fails, stop — do not attempt further writes. The runner will halt the loop on the next iteration's strict-parse check, which is the intended behavior.
>
> IMPORTANT: when committing, stage only the source files you changed — do NOT stage the task file (`TASK_FILE_PATH`) or any `docs/exec-plans/` files. These are loop metadata, not deliverables.

---

## Failure modes — prevent these

- **Parallel `Agent` calls.** Never issue multiple `Agent` tool calls in the same response during the loop. Tasks may depend on prior tasks' edits. Always sequential, always foreground.
- **Skipping the strict-parse check.** Phase 4 step 6 must use a JSON parser, not text inspection. A prior incident: an agent's `Edit` to a long `log` string dropped the `},` between two adjacent task objects; 19 subsequent iterations ran on the corrupted file because the loop only checked status text, not JSON validity. Halt loudly the moment a parse fails — do not let agents pile more writes onto a malformed file.
- **Hand-editing the task JSON.** Agents must use `python3 -c 'json.load → mutate → json.dump'` (or `jq`) for every task-file write, per the Task Implementation Prompt. Long multi-paragraph `log` strings with embedded escapes are the highest-risk shape for `Edit`/`Write` corruption.
- **Skipping the re-read.** After every `Agent` returns, re-read the JSON. Don't trust in-memory state — the Agent has been writing to the file and the in-memory copy is stale.
- **Committing the task file.** The Task Implementation Prompt forbids staging `docs/exec-plans/` files. If an Agent does it anyway, that's a bug — flag it to the user and don't propagate.
- **Auto-locating multiple files silently.** If Phase 2 finds more than one validated match, _always_ ask the user. Don't pick by recency or alphabetical order.
- **Trying to build a missing task list.** This skill consumes an existing JSON. If Phase 2 finds nothing, stop and tell the user — don't shell out to `task-list-builder` or fabricate tasks.
- **Treating `--next` as a silent one-shot.** Even in `--next` mode, show the Phase 3 summary first so the user can see which task is about to run.
