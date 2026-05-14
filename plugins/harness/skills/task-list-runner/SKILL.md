---
name: task-list-runner
description: Run a structured task list (JSON file in the harness task-list schema) by dispatching each task to a foreground Agent in sequence. Auto-locates an in-progress task file in docs/exec-plans/active/ when no path is given. Use when the user says "run the plan", "resume the plan", "execute the tasks", "run task-list-runner", or otherwise asks to drive an existing harness task list to completion. Pairs with task-list-builder, which produces the JSON.
user-invocable: true
disable-model-invocation: false
argument-hint: "[path to .json or .md task file] [--all | --next]"
---

# task-list-runner

Drive a harness task list (JSON file matching the `task-list-schema.md` schema) to completion by dispatching each task to a foreground `Agent` tool call, one at a time. Pairs with `task-list-builder`, which produces the JSON.

The schema this skill consumes is defined in `${CLAUDE_PLUGIN_ROOT}/task-list-schema.md`. Re-read that file rather than relying on memory.

**Arguments:** `$ARGUMENTS`

---

## CLI reference — `task_list_cli.py`

The bundled CLI at `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py` is the canonical interface to the task file. **Subcommands:** `next`, `start`, `set-status`, `draft`, `publish`, `get`, `list`, `status`, `remaining`, `verify`. All take `--file <task-file-path>`. **Don't invent verbs** like `show`, `inspect`, `info`, or `view` — argparse rejects anything outside the list above and prints the full subcommand help on rejection, so a wrong guess costs one wasted call but the right verb is always one of the ten names just enumerated.

- **`next`** — atomically claim and print the next task. Resumes in-progress, else flips first pending → in-progress. Exits 14 if no tasks remain. Exits 11 if any task is currently `drafted` — resolve via `publish` or `set-status failed` first.
- **`start --id <N>`** — flip task N from pending → in-progress.
- **`draft --id <N> --commit-msg "<subject>" --log-file <path|->`** — flip in-progress task N to `drafted`; writes the log into the task and parks the commit subject in a per-task `/tmp` staging file. **Does NOT touch git.** This is the implementation agent's terminal step within an iteration — the runner takes over and dispatches the validation agent before either `publish` or `set-status failed` resolves the draft. Same `--log-file -` + quoted-heredoc convention as `set-status` (use stdin to keep it one Bash call).
- **`publish --id <N>`** — flip drafted task N to `complete` by running `git commit` against the already-staged git index using the staged subject. Verifies the index is non-empty before committing. On success, removes the staging file. On commit failure (e.g., a pre-commit hook rejects), the task stays `drafted` and the staging file stays put — the runner can fix the underlying cause and re-run `publish --id N`. **Only the runner calls this** (post-validation), never the implementation agent.
- **`set-status --id <N> --status complete|failed --log-file <path|->`** — flip task N to a terminal status without touching git. Allowed transitions: `in-progress → complete` (no-code completion, e.g., investigation tasks), `in-progress → failed` (implementation gave up), `drafted → failed` (validation rejected the draft after retries). **`drafted → complete` is forbidden** — force the happy path through `publish` so a task cannot reach `complete` without a commit. Same `--log-file -` + quoted-heredoc convention as `draft` (the stdin path is preferred in the dispatched-agent flow because it's one Bash call, not two tool calls each gated separately by the auto-mode classifier).
- **`get --id <N>`** — print one task as pretty JSON.
- **`list [--status <s>]`** — print all tasks (or filtered) as a JSON array.
- **`status`** — print task counts (including a `drafted` count) + a precomputed `remaining` integer (`pending + in_progress + drafted`, the halt-gate's one number) + `plan` path. Use this for Phase 5 summary displays AND as the between-iteration halt-gate (it runs `load_and_validate` like every other command, so a non-zero exit means the file is corrupt). Drafted is non-terminal and counts toward `remaining` — a draft awaiting publish-or-fail still owes the runner work.
- **`remaining`** — print non-terminal tasks (pending + in-progress + drafted) as a compact JSON array — each entry has just `id`, `title`, `effort`, `status`. Use for Phase 3's user-facing summary table. The hot-path halt-gate uses `status.remaining` (the integer) instead so a 30–50-task file doesn't pay an O(N) array on every iteration.
- **`verify --id <N>`** — execute the resolved `verifySteps` for task N in order, capturing each step's stdout+stderr to `/tmp/verify-<id>-step<i>-<slug>.log`, stopping on the first failure with that step's exit code, and printing one `verify[i/n] <slug> exit=<EX> log=<path>` line per executed step. **Resolution rule:** if task N declares its own `verifySteps` array, those run (total replacement, not a merge); otherwise the top-level `verifySteps` runs. So `--id` selects both the log-file slug and the resolved-steps source — different tasks may run different steps. Auto-approved through the harness PreToolUse hook, so per-task verification runs without per-call prompts; trust for verifySteps content is upstream (task-list-builder).

**Exit codes:** 0 success · 1 IO error · 2 argparse · 10 task id not found · 11 invalid state transition · 12 schema validation · 13 JSON parse · 14 no remaining tasks · 15 git operation failed (publish only).

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

**Two-phase iteration.** Each task goes through two runner-dispatched agents, both at depth-1 from the runner (never nested):

1. **Implementation agent** — claims the task via `next`, makes the code change, runs `verify`, stages source files via `git add`, then calls `draft` (which parks the commit subject without touching git). Returns.
2. **Validation agent** — fresh-context, read-only (`subagent_type: Explore`), evaluates the task's `agentValidations` against the now-drafted code. Returns `RESULT: PASS` or `RESULT: FAIL`.

The runner then resolves the draft: `publish --id N` on PASS (commits the staged index, flips to `complete`), `set-status --id N --status failed` on FAIL (no commit, staging file preserved for inspection).

This split is structural, not stylistic: Claude Code's runtime forbids depth-2 subagent dispatch (an agent dispatched from another agent). The previous design had the implementation agent dispatch the validation subagent — which silently fell back to inline inspection because the runtime denied the nested dispatch. Splitting `finish` into `draft` + `publish` gives the runner a safe parking state (`drafted`) between the two agents.

### Mode = `all`

1. Compute `MAX_ITER` = (number of tasks with status `"pending"` or `"in-progress"`) × 1.5, rounded up, plus 1. Example: 10 remaining → `MAX_ITER = 16`.
2. Run the loop. On each iteration:
   1. Run `task_list_cli.py status` to get current counts and confirm the file is still well-formed (any non-zero exit = corruption — halt the loop). Note `prev_remaining = status.remaining` (the integer); you'll compare it after the iteration runs.
   2. If `status.remaining == 0`, the loop is done. Show final status:
      - Any `"failed"` tasks (`status.failed > 0`) → `"Done with failures: X/Y complete, Z failed"`.
      - Otherwise → `"All Y tasks complete"`.
   3. If `MAX_ITER` is reached → `"Max iterations (MAX_ITER) reached"` and stop.
   4. Show progress header: `"Iteration X/MAX_ITER — N tasks remaining"`.
   5. **Issue a single foreground `Agent` tool call** with the **Task Implementation Prompt** below, substituting the task file path. Do NOT issue multiple `Agent` calls in parallel — tasks may depend on prior tasks' edits. Wait for it to return.
   6. **Inspect the implementation agent's outcome** by calling `task_list_cli.py status` and `task_list_cli.py list`. Three terminal-for-the-iteration outcomes are possible:
      - **`drafted` task present** — the implementation agent reached `draft` cleanly. Proceed to step 7 (validation phase).
      - **A task moved straight to `failed`** (via `set-status failed`) — the implementation agent gave up before drafting (e.g., couldn't make the verifySteps pass). No validation phase; the failure log is in the task's `log` field. Proceed to step 9.
      - **No task changed state** (`status.remaining == prev_remaining`) — the implementation agent crashed or returned without acting. Warn `"Agent did not advance task state on iteration X, continuing"` and proceed to step 9 (next iteration's `next` will resume any in-progress task).
   7. **Validation phase** (only when a task is `drafted`). Read the drafted task's `id`, `what`, and `agentValidations` via `task_list_cli.py get --id <N>`, then collect the changed-files list via `git diff --cached --name-only` (the implementation agent staged its files, so the index is the source of truth). Issue a single foreground `Agent` tool call with the **Validation Agent Prompt** (below) plus the task-specific suffix containing `what`, `agentValidations`, and `changedFiles`. **`subagent_type: Explore`** (read-only by design — the runtime denies `Write`/`Edit`/`NotebookEdit`, structurally preventing the validation agent from "fixing" anything). The agent's last line will be `RESULT: PASS` or `RESULT: FAIL`.
   8. **Resolve the draft.** Branch on the validation result:
      - **`RESULT: PASS`** → run `task_list_cli.py publish --id <N>`. The CLI runs `git commit` against the staged index using the parked subject and flips status to `complete`. If `publish` exits non-zero (typically 15: pre-commit hook rejection or empty index), the task stays `drafted`; surface the stderr to the user and proceed to step 9 — the next iteration will see the still-drafted task and `cli next` will refuse to claim new work, forcing manual recovery.
      - **`RESULT: FAIL`** → run `task_list_cli.py set-status --id <N> --status failed --log-file -` with the validation report piped via a quoted heredoc as the log. **Don't dispatch a fixup implementation agent** — the schema doesn't model `drafted → in-progress`, so a failed validation terminates the task; recovery happens via `/harness:task-list-builder` rewrite mode (re-plan), not within this run.
   9. **Re-run `task_list_cli.py status`** as the post-iteration corruption gate. `status` runs `load_and_validate` like every other subcommand, so any structural corruption surfaces as a non-zero exit — halt the loop with `"Task file corrupted on iteration X — see <path>"` and stop. Do NOT continue iterating on a malformed file.
   10. If `status.remaining == prev_remaining`, no task moved to a terminal state — proceed (the next iteration will pick up wherever the loop left off).
   11. Repeat from step 1.

### Mode = `next`

1. Issue a single foreground `Agent` tool call with the Task Implementation Prompt, substituting the task file path. (Phase 3's `status` already confirmed work remains; the dispatched agent's `next` call will claim and run it. If `next` exits 14, the agent will report no work — propagate that to the user.)
2. Run the validation phase + draft resolution exactly as in mode-`all` steps 6–8 above (the architecture is identical, just one iteration).
3. Run `task_list_cli.py status` to show the updated counts (complete/pending/failed/drafted), and stop.

---

## Phase 5 — Final summary

After the loop completes (all tasks done, max iterations reached, or `--next` finished), source data via `task_list_cli.py status` (for the `plan` path + per-status counts) and `task_list_cli.py list` (for the full task table) and show:

- A task status table: each task's `id`, `title`, and `status`.
- Plan markdown path (from `status.plan`, if present).
- Task file path.
- If `status.failed > 0` or any tasks remain pending/in-progress: suggest re-running the skill (with `--all` to continue, or with no flag to revise first).

---

## Validation Agent Prompt

In Phase 4 step 7, the runner dispatches a fresh-context validation agent (`subagent_type: Explore`). The prompt body lives in `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/validation-agent-prompt.md` — `Read` that file and use its raw contents (not the cat-n line-number prefixes the Read tool displays) as the wrapper, then append a task-specific suffix containing `what`, `agentValidations`, and `changedFiles`. Construct `changedFiles` from `git diff --cached --name-only` (the implementation agent staged its files via `git add` before drafting; the staged index is the post-change snapshot). Pass the assembled prompt verbatim.

---

## Task Implementation Prompt

The prompt body lives in `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task-implementation-prompt.md` — `Read` that file and use its raw contents (not the cat-n line-number prefixes the Read tool displays) as the prompt for each `Agent` tool call. Replace every literal `TASK_FILE_PATH` token in the prompt body with the absolute path to the JSON task file before passing.

The CLI exits non-zero on any failure (task id not found → 10; invalid state transition → 11; schema/JSON errors → 12 / 13; no remaining tasks → 14; git operation failed → 15). If a step fails, the implementation agent reads stderr, fixes the cause, and retries. The agent must not work around failures by hand-editing the task file.

---

## Failure modes — prevent these

- **Don't issue parallel `Agent` calls during the loop.** Tasks may depend on prior tasks' edits, and the implementation/validation pair within an iteration is strictly sequential. Always one `Agent` call per response, always foreground.
- **Don't dispatch the validation agent from inside the implementation agent.** The runtime forbids depth-2 subagent dispatch — a nested call silently fails over to inline inspection, defeating the structural read-only guarantee. Validation is dispatched by the runner, between the implementation agent's return and the `publish`/`set-status failed` resolver.
- **Don't skip the post-iteration `status` re-read.** Phase 4 step 9 (`status` as corruption gate) is the canary that caught past silent-corruption bugs only after 19 iterations. Never skip it; never downgrade a non-zero exit to a warning.
- **Don't allow the implementation agent to call `publish`.** `publish` is the runner's contract — calling it from inside the implementation agent skips the validation phase entirely, which is the architectural bug this design exists to prevent. The implementation agent's terminal verb is `draft` (or `set-status failed` if verification couldn't be made to pass); the runner calls `publish` after the validation agent reports PASS.
- **Don't let a drafted task linger across iterations without resolution.** If `publish` exits non-zero (e.g., a pre-commit hook rejects the commit), the task stays drafted and the next iteration's `cli next` will refuse to claim new work (exit 11). Surface the publish failure to the user — they need to fix the underlying cause (hook, missing config, wrong staged content) before the loop can continue.
- **Don't commit the task file or `docs/exec-plans/` files.** The Task Implementation Prompt forbids staging these. If an agent does it anyway, that's a bug — flag it to the user and don't propagate.
- **Don't auto-pick when Phase 2 finds multiple validated matches.** Always ask the user. Never pick by recency or alphabetical order.
- **Don't try to build a missing task list.** This skill consumes an existing JSON. If Phase 2 finds nothing, stop and tell the user — don't shell out to `task-list-builder` or fabricate tasks.
- **Don't treat `--next` as a silent one-shot.** Even in `--next` mode, show the Phase 3 summary first so the user can see which task is about to run.
- **Don't accept verification re-runs with permuted redirection flags.** A dispatched agent that runs `npx tsc --noEmit` (or any verifySteps command) directly, then re-runs it with `| head -50`, then with `2>&1`, then without — that's a re-read loop, not progress. The `verify` subcommand exists precisely so the agent never composes redirection itself; if you see this pattern, the agent has bypassed `verify` and should be steered back to it.
- **Don't add a fixup loop on validation FAIL.** The schema doesn't model `drafted → in-progress`, so a `RESULT: FAIL` from the validation agent terminates the task via `set-status failed`. Recovery happens via `/harness:task-list-builder` rewrite mode (re-plan), not within this run. A retry loop would require an "un-draft" transition the schema deliberately omits.
