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

The bundled CLI at `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py` is the canonical interface to the task file. **Subcommands:** `next`, `start`, `finish`, `get`, `list`, `status`, `remaining`, `verify` — these are the only valid verbs (argparse rejects others — don't invent names like `show` or `inspect`). All take `--file <task-file-path>`.

- **`next`** — atomically claim and print the next task. Resumes in-progress, else flips first pending → in-progress. Exits 14 if no tasks remain.
- **`start --id <N>`** — flip task N from pending → in-progress.
- **`finish --id <N> --status complete|failed --log-file <path>`** — flip in-progress task N to terminal status; log content is read from the file (file-only input avoids shell-arg quoting hazards).
- **`get --id <N>`** — print one task as pretty JSON.
- **`list [--status <s>]`** — print all tasks (or filtered) as a JSON array.
- **`status`** — print task counts + a precomputed `remaining` integer (`pending + in_progress`, the halt-gate's one number) + `plan` path. Use this for Phase 5 summary displays AND as the between-iteration halt-gate (it runs `load_and_validate` like every other command, so a non-zero exit means the file is corrupt).
- **`remaining`** — print non-terminal tasks (pending + in-progress) as a compact JSON array — each entry has just `id`, `title`, `effort`, `status`. Use for Phase 3's user-facing summary table. The hot-path halt-gate uses `status.remaining` (the integer) instead so a 30–50-task file doesn't pay an O(N) array on every iteration.
- **`verify --id <N>`** — execute `verifySteps` in order, capturing each step's stdout+stderr to `/tmp/verify-<id>-step<i>-<slug>.log`, stopping on the first failure with that step's exit code, and printing one `verify[i/n] <slug> exit=<EX> log=<path>` line per executed step. `verifySteps` is a single top-level array applied uniformly to every task; `--id` only keeps log filenames distinct, not because each task has its own steps. Auto-approved through the harness PreToolUse hook, so per-task verification runs without per-call prompts; trust for verifySteps content is upstream (task-list-builder).

**Exit codes:** 0 success · 1 IO error · 2 argparse · 10 task id not found · 11 invalid state transition · 12 schema validation · 13 JSON parse · 14 no remaining tasks.

Every subcommand calls `load_and_validate` as a precondition before doing its work — there is no separate `validate` verb because there's no need for one. **Every mutation goes through this CLI** — no exceptions. Dispatched agents never use `Edit`/`Write`/inline `python3 -c '...'` against the task JSON; the runner itself never hand-edits during the loop. For its own bookkeeping displays, the runner uses `status` and `list`, never re-reads the JSON natively. If the plan needs structural revision, run `/harness:task-list-builder` in rewrite mode — this skill consumes plans; it doesn't edit them.

---

## Phase 1 — Parse arguments

From `$ARGUMENTS`, extract:

- **Path** — the first non-flag argument, if any. May point to a `.json` (read directly) or `.md` file (read its YAML frontmatter `task_file` field, which points to the JSON).
- **Mode flag** — `--all` (run every remaining task non-interactively), `--next` (run exactly one pending/in-progress task and stop), or absent (interactive: show the menu in Phase 3).

If the path is a `.md` file, validate the pointer: the JSON it points to must exist, parse, and contain at least one task with status `"pending"` or `"in-progress"`. If validation fails, report a clear error (e.g., `"task_file points to X which does not exist"` or `"JSON at X has no pending tasks"`) and stop.

---

## Phase 2 — Locate the task file (if no path was given)

If Phase 1 yielded no path, auto-locate by content (not filename):

1. Glob `docs/exec-plans/active/*.json`. For each candidate, run `task_list_cli.py --file <path> status`. Treat as valid if exit is 0 (file parses + schema is well-formed) and `status.remaining > 0`. Cache the per-candidate `status` payload — counts and `plan` are what you'd display in step 3 anyway.
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
- The remaining tasks (pending + in-progress) with their `id`, `title`, and `effort`. Source these by running `task_list_cli.py --file <path> remaining` (a compact array, just the four display fields). Do NOT call `list` and pipe it through inline `python3 -c '...'` to filter — the `remaining` subcommand exists for exactly this display.

Then branch on the Phase 1 mode flag:

- **`--all`** → skip the menu, jump to Phase 4 with mode = `all`.
- **`--next`** → skip the menu, jump to Phase 4 with mode = `next`.
- **No flag (interactive)** — prompt:

  > **How would you like to proceed?**
  >
  > 1. **Run all remaining** — Implement every pending/in-progress task via automated loop
  > 2. **Run next task only** — Implement just the next pending/in-progress task, then stop
  - **Option 1** → Phase 4 with mode = `all`.
  - **Option 2** → Phase 4 with mode = `next`.

If the user wants to revise the plan instead of running it (reorder, edit a task's `what`, drop a task), point them at `/harness:task-list-builder` in rewrite mode — that's the canonical revision tool. This skill consumes plans; it does not edit them.

---

## Phase 4 — Agent loop

Implement tasks via sequential foreground `Agent` tool calls. Each Agent runs _within this conversation_ — the user sees every file read, edit, and test run in real time.

### Mode = `all`

1. Compute `MAX_ITER` = (number of tasks with status `"pending"` or `"in-progress"`) × 1.5, rounded up, plus 1. Example: 10 remaining → `MAX_ITER = 16`.
2. Run the loop. On each iteration:
   1. Run `task_list_cli.py status` to get current counts and confirm the file is still well-formed (any non-zero exit = corruption — halt the loop). Note `prev_remaining = status.remaining` (the integer); you'll compare it after the agent runs.
   2. If `status.remaining == 0`, the loop is done. Show final status:
      - Any `"failed"` tasks (`status.failed > 0`) → `"Done with failures: X/Y complete, Z failed"`.
      - Otherwise → `"All Y tasks complete"`.
   3. If `MAX_ITER` is reached → `"Max iterations (MAX_ITER) reached"` and stop.
   4. Show progress header: `"Iteration X/MAX_ITER — N tasks remaining"`.
   5. **Issue a single foreground `Agent` tool call** with the **Task Implementation Prompt** below, substituting the task file path. Do NOT issue multiple `Agent` calls in parallel — tasks may depend on prior tasks' edits. Wait for it to return.
   6. **Re-run `task_list_cli.py status`** as the post-iteration corruption gate. `status` runs `load_and_validate` like every other subcommand, so any structural corruption surfaces as a non-zero exit — halt the loop with `"Task file corrupted on iteration X — see <path>"` and stop. Do NOT continue iterating on a malformed file.
   7. If `status.remaining == prev_remaining`, the agent didn't move any task to a terminal state — warn `"Agent did not finish a task on iteration X, continuing"` and proceed. (Next iteration's `next` will resume any in-progress task.)
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

> You are implementing one task from a structured task list. **Use `task_list_cli.py` for ALL task-file access — mutations AND read-only inspections of single fields.** Never use `Edit`, `Write`, `cat`, `jq`, or inline `python3 -c '...'` against the task file. The CLI's `get` / `list` / `status` / `remaining` subcommands cover the read surface; if you can't find a field through them, it likely doesn't exist in the schema (e.g. `verifySteps` is a single top-level array shared by every task — there is no per-task verifySteps to look up). Bypassing the CLI skips atomicity and schema validation, and has caused silent JSON corruption in past runs.
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
> **Run verification:**
>
> ```bash
> python3 "${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py" \
>     --file TASK_FILE_PATH verify --id <id>
> ```
>
> The CLI runs each `verifySteps` command in order, capturing stdout+stderr to a per-step log file (`/tmp/verify-<id>-step<N>-<slug>.log`), and stops on the first failing step. If the command exits non-zero, that exit code is the failing step's exit code; the last `verify[i/n]` line in stdout names the failing step's log path. `Read` that file, fix the underlying cause in your code, then re-run the same `verify --id <id>` invocation. When the command exits zero, all steps passed and the task is verified.
>
> Strictly forbidden during verification:
>
> - Re-invoking individual `verifySteps` commands directly (e.g. running `npx tsc --noEmit` yourself). The CLI is the contract; running steps by hand splits your verification rhythm and burns budget.
> - Permuting redirection flags on the same command hoping for clearer output (`| head -50` → `2>&1` → drop `2>&1` → repeat). The CLI's redirection is canonical; the answer is in the log file. If the log is unclear, `Read` more of it — don't re-run.
> - Inventing additional verification commands not in `verifySteps`. If a step you need is missing, that's a bug in the task file, not something to paper over with shell improvisation.
>
> **Step 2 — Finish:** Pipe your log into `finish` via a quoted heredoc. The `--log-file -` token tells the CLI to read from stdin; the quoted `<<'EOF'` makes the shell pass the body verbatim (no `$VAR` expansion, no quote-mangling), so embedded `"`, `$`, and newlines are safe.
>
> ```bash
> python3 "${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py" \
>     --file TASK_FILE_PATH finish --id <id> --status complete --log-file - <<'EOF'
> Task <id>: <one-line summary of what changed>
>
> Acceptance criteria: <which were verified>
> Verification: <which steps ran, all passed>
> EOF
> ```
>
> If tests failed and you cannot fix forward: same command with `--status failed`. Do NOT use the `Write` tool to stage a `/tmp/` log file — the heredoc path is one Bash call (auto-approved by the harness hook); the Write path is two tool calls each gated separately by the auto-mode classifier.
>
> **Step 3 — Commit.** Stage only the source files you changed. NEVER stage the task file (`TASK_FILE_PATH`) or any `docs/exec-plans/` files — these are loop metadata, not deliverables. Implement exactly ONE task per iteration.

The CLI exits non-zero on any failure (task id not found → 10; invalid state transition → 11; schema/JSON errors → 12 / 13; no remaining tasks → 14). If a step fails, read stderr, fix the cause, and retry. Do not work around it by hand-editing the task file.

---

## Failure modes — prevent these

- **Parallel `Agent` calls.** Never issue multiple `Agent` tool calls in the same response during the loop. Tasks may depend on prior tasks' edits. Always sequential, always foreground.
- **Skipping the re-read.** After every `Agent` returns, re-run `status` — both to corruption-check and to compare `status.remaining` (integer) against `prev_remaining` for the no-progress warn. Don't trust in-memory state — the Agent has been writing to the file and the in-memory copy is stale.
- **Committing the task file.** The Task Implementation Prompt forbids staging `docs/exec-plans/` files. If an Agent does it anyway, that's a bug — flag it to the user and don't propagate.
- **Auto-locating multiple files silently.** If Phase 2 finds more than one validated match, _always_ ask the user. Don't pick by recency or alphabetical order.
- **Trying to build a missing task list.** This skill consumes an existing JSON. If Phase 2 finds nothing, stop and tell the user — don't shell out to `task-list-builder` or fabricate tasks.
- **Treating `--next` as a silent one-shot.** Even in `--next` mode, show the Phase 3 summary first so the user can see which task is about to run.
- **Skipping the between-iteration check.** Phase 4 step 6 (`status` as halt-gate) is the canary that caught past silent-corruption bugs only after 19 iterations. Never skip it; never downgrade a non-zero exit to a warning.
- **Re-running verification with permuted redirection flags.** A dispatched agent that runs `npx tsc --noEmit` (or any verifySteps command) directly, then re-runs it with `| head -50`, then with `2>&1`, then without — that's a re-read loop, not progress. The `verify` subcommand exists precisely so the agent never composes redirection itself; if you see this pattern, the agent has bypassed `verify` and should be steered back to it.
