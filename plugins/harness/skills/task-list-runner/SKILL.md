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

## CLI reference — `task_list_cli.py`

The bundled CLI at `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py` is the canonical interface to the task file. Available subcommands (all take `--file <task-file-path>`):

- **`next`** — atomically claim and print the next task. Resumes in-progress, else flips first pending → in-progress. Exits 14 if no tasks remain.
- **`start --id <N>`** — flip task N from pending → in-progress.
- **`finish --id <N> --status complete|failed --log-file <path>`** — flip in-progress task N to terminal status; log content is read from the file (file-only input avoids shell-arg quoting hazards).
- **`get --id <N>`** — print one task as pretty JSON.
- **`list [--status <s>]`** — print all tasks (or filtered) as a JSON array.
- **`status`** — print task counts + `plan` path + the full `verifySteps` array + a compact `remaining` array (each entry has just `id`, `title`, `effort`, `status` — enough for Phase 3's user-facing summary, no need to also call `list`). Use this for Phase 3 / Phase 5 summary displays AND as the between-iteration halt-gate (it runs `load_and_validate` like every other command, so a non-zero exit means the file is corrupt).

**Exit codes:** 0 success · 1 IO error · 2 argparse · 10 task id not found · 11 invalid state transition · 12 schema validation · 13 JSON parse · 14 no remaining tasks.

Every subcommand calls `load_and_validate` as a precondition before doing its work — there is no separate `validate` verb because there's no need for one. Mutations from dispatched agents go ONLY through this CLI. The runner itself may also use `status` and `list` for its own bookkeeping displays — prefer those over re-reading the JSON natively. Hand-edits to the JSON during the loop are forbidden (see Failure modes).

---

## Phase 1 — Parse arguments

From `$ARGUMENTS`, extract:

- **Path** — the first non-flag argument, if any. May point to a `.json` (read directly) or `.md` file (read its YAML frontmatter `task_file` field, which points to the JSON).
- **Mode flag** — `--all` (run every remaining task non-interactively), `--next` (run exactly one pending/in-progress task and stop), or absent (interactive: show the menu in Phase 3).

If the path is a `.md` file, validate the pointer: the JSON it points to must exist, parse, and contain at least one task with status `"pending"` or `"in-progress"`. If validation fails, report a clear error (e.g., `"task_file points to X which does not exist"` or `"JSON at X has no pending tasks"`) and stop.

---

## Phase 2 — Locate the task file (if no path was given)

If Phase 1 yielded no path, auto-locate by content (not filename):

1. Glob `docs/exec-plans/active/*.json`. For each candidate, run `task_list_cli.py --file <path> status`. Treat as valid if exit is 0 (file parses + schema is well-formed) and `status.remaining` is non-empty. Cache the per-candidate `status` payload — counts and `plan` are what you'd display in step 3 anyway.
2. If no JSON candidates match, repeat the scan against `docs/exec-plans/active/*.md`. For each, read its YAML frontmatter `task_file` field and run `status` against the JSON it points to (same accept criterion).
3. Resolve:
   - **Exactly one match** (from either scan): use it.
   - **Multiple matches:** list them with their cached `status` summaries (counts + `plan` path) and ask the user to pick. Do NOT pick by recency or alphabetical order.
   - **No matches:** report `"No in-progress task files found in docs/exec-plans/active/"` and stop. Do NOT try to build a new task list — that is `task-list-builder`'s job.

From here on, "the task file" means the chosen JSON.

---

## Phase 3 — Show summary and choose mode

Show the user:

- Task file path
- Total / complete / in-progress / pending / failed counts
- The remaining tasks (pending + in-progress) with their `id`, `title`, and `effort`. Source these from `status.remaining` — already in the `status` payload from the counts call. Do NOT make additional `list` calls or pipe `list` through inline `python3 -c '...'` to filter; the data you need is already in hand.

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
   1. Run `task_list_cli.py status` to get current counts and confirm the file is still well-formed (any non-zero exit = corruption — halt the loop).
   2. If `status.remaining` is empty, the loop is done. Show final status:
      - Any `"failed"` tasks (`status.failed > 0`) → `"Done with failures: X/Y complete, Z failed"`.
      - Otherwise → `"All Y tasks complete"`.
   3. If `MAX_ITER` is reached → `"Max iterations (MAX_ITER) reached"` and stop.
   4. Show progress header: `"Iteration X/MAX_ITER — N tasks remaining"`.
   5. **Issue a single foreground `Agent` tool call** with the **Task Implementation Prompt** below, substituting the task file path. Do NOT issue multiple `Agent` calls in parallel — tasks may depend on prior tasks' edits. Wait for it to return.
   6. **Re-validate the task file** by running:

      ```bash
      python3 "${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py" \
          --file TASK_FILE_PATH status
      ```

      `status` runs `load_and_validate` as a precondition (like every other subcommand), so any structural corruption surfaces as a non-zero exit. If exit code is non-zero, halt the loop with `"Task file corrupted on iteration X — see <path>"` and stop. Do NOT continue iterating on a malformed file. The `status` payload itself can be discarded — it's the exit code that matters here.

   7. Run `task_list_cli.py list --status in-progress`. If any returned task has `log: null`, the agent didn't even start updating its task — warn `"Agent did not update task status on iteration X, continuing"` and proceed. (A non-null `log` on an in-progress task means the agent crashed mid-task; the next iteration's `next` will resume it.)
   8. Repeat from step 1.

### Mode = `next`

1. Issue a single foreground `Agent` tool call with the Task Implementation Prompt, substituting the task file path. (Phase 3's `status` already confirmed work remains; the dispatched agent's `next` call will claim and run it. If `next` exits 14, the agent will report no work — propagate that to the user.)
2. After the Agent returns, run `task_list_cli.py status` to show the updated counts (complete/pending/failed), and stop.

---

## Phase 5 — Final summary

After the loop completes (all tasks done, max iterations reached, or `--next` finished), source data via `task_list_cli.py status` (for the `plan` path + per-status counts) and `task_list_cli.py list` (for the full task table) and show:

- A task status table: each task's `id`, `title`, and `status`.
- Plan markdown path (from `status.plan`, if present).
- Task file path.
- If `status.failed > 0` or any tasks remain pending/in-progress: suggest re-running the skill (with `--all` to continue, or with no flag to revise first).

---

## Task Implementation Prompt

Pass this verbatim to each `Agent` tool call, replacing `TASK_FILE_PATH` with the absolute path to the JSON task file:

> You are implementing one task from a structured task list. **Use `task_list_cli.py` for ALL task-file mutations and reads.** Never use `Edit`, `Write`, or inline `python3 -c '...'` against the task file — they bypass atomicity and schema validation, and have caused silent JSON corruption in past runs.
>
> **Step 1 — Claim and read your task:**
>
> ```bash
> python3 "${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py" \
>     --file TASK_FILE_PATH next
> ```
>
> The output is the full task object — note the `id` and read `what`, `resolves`, and `acceptanceCriteria`. (`next` atomically claims the first pending task and flips it to `in-progress`, or returns an already-in-progress task unchanged if a previous iteration crashed mid-task.) If the command exits with code 14, no work remains — exit cleanly.
>
> Implement the change. Verify all acceptance criteria are met.
>
> **Run verification.** Read the `verifySteps` array from the task file (or run `task_list_cli.py status` to see it). Run each step's `command` in order via Bash. If any step exits non-zero, **stop, fix the cause, and re-run the full sequence from step 1** — do not skip ahead. Do not invent additional verification commands; if a step you need is missing from `verifySteps`, that's a bug in the task file, not something to paper over with shell improvisation. When all steps pass, the task is verified.
>
> **Step 2 — Finish:** Use the `Write` tool to dump your log to `/tmp/task-list-runner-<id>.txt`. Then:
>
> ```bash
> python3 "${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py" \
>     --file TASK_FILE_PATH finish --id <id> --status complete --log-file /tmp/task-list-runner-<id>.txt
> ```
>
> If tests failed and you cannot fix forward: same command with `--status failed`.
>
> **Step 3 — Commit.** Stage only the source files you changed. NEVER stage the task file (`TASK_FILE_PATH`) or any `docs/exec-plans/` files — these are loop metadata, not deliverables. Implement exactly ONE task per iteration.

The CLI exits non-zero on any failure (task id not found → 10; invalid state transition → 11; schema/JSON errors → 12 / 13; no remaining tasks → 14). If a step fails, read stderr, fix the cause, and retry. Do not work around it by hand-editing the task file.

---

## Failure modes — prevent these

- **Parallel `Agent` calls.** Never issue multiple `Agent` tool calls in the same response during the loop. Tasks may depend on prior tasks' edits. Always sequential, always foreground.
- **Skipping the re-read.** After every `Agent` returns, re-read the file via the CLI (`status` for counts + corruption check, `list --status in-progress` for the agent-didn't-update detector). Don't trust in-memory state — the Agent has been writing to the file and the in-memory copy is stale.
- **Committing the task file.** The Task Implementation Prompt forbids staging `docs/exec-plans/` files. If an Agent does it anyway, that's a bug — flag it to the user and don't propagate.
- **Auto-locating multiple files silently.** If Phase 2 finds more than one validated match, _always_ ask the user. Don't pick by recency or alphabetical order.
- **Trying to build a missing task list.** This skill consumes an existing JSON. If Phase 2 finds nothing, stop and tell the user — don't shell out to `task-list-builder` or fabricate tasks.
- **Treating `--next` as a silent one-shot.** Even in `--next` mode, show the Phase 3 summary first so the user can see which task is about to run.
- **Hand-editing the task file.** Do not use `Edit`, `Write`, or inline `python3 -c '...'` against the task JSON during the loop. All mutations go through `task_list_cli.py`. The one exception is structural revision in Phase 3 Option 3 (reorder, revise `what`, mark skipped) — and even then, re-run `task_list_cli.py status` after saving so its `load_and_validate` step can confirm the edit didn't break the schema.
- **Skipping the between-iteration check.** Phase 4 step 6 (`status` as halt-gate) is the canary that caught past silent-corruption bugs only after 19 iterations. Never skip it; never downgrade a non-zero exit to a warning.
