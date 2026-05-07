---
name: task-list-viewer
description: Read-only viewer for harness task-list JSON files. Auto-locates the active task file in docs/exec-plans/active/. With no args, shows counts, the paired markdown plan path, the in-progress task ID (if any), and pending task titles. With a positional task ID, prints that task's full details. Use when the user says "show me the task list", "view the plan", "what tasks are left", or otherwise asks to inspect (not run) an existing harness task list. Pairs with task-list-builder (which creates) and task-list-runner (which executes).
user-invocable: true
disable-model-invocation: false
argument-hint: "[task-id] [--file <path to .json or .md>]"
---

# task-list-viewer

Inspect a harness task list (JSON file matching the `task-list-schema.md` schema) without running, mutating, or building it. Pairs with `task-list-builder` (creates plans) and `task-list-runner` (executes them).

The schema this skill consumes is defined in `${CLAUDE_PLUGIN_ROOT}/task-list-schema.md`. Re-read that file rather than relying on memory.

**Arguments:** `$ARGUMENTS`

---

## CLI reference — `task_list_cli.py`

The bundled CLI at `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py` is the canonical interface to the task file. It lives in the runner's directory, not this skill's, on purpose — a single CLI keeps the viewer's interpretation of the file in lockstep with the runner's. **Don't copy or shadow it here.**

The viewer uses only the read-only verbs:

- **`status`** — file-level metadata: counts (`total`, `pending`, `in_progress`, `complete`, `failed`), `remaining` integer, and `plan` path. Used both for Phase 2 candidate validation and for the Phase 3 summary header.
- **`list [--status <s>]`** — full task array (or filtered by status). The viewer uses `--status pending` for the pending-titles list and `--status in-progress` to find the active task ID.
- **`get --id <N>`** — pretty-printed single task. Used for the Phase 3 single-task detail view.

Every CLI invocation takes `--file <task-file-path>` first.

**Don't invent verbs** like `view`, `inspect`, `show`, or `info` — argparse rejects anything outside `next`, `start`, `finish`, `get`, `list`, `status`, `remaining`, `verify`. The right read verb is always one of those eight names.

**Don't call mutation verbs** (`next`, `start`, `finish`, `verify`) from this skill, even though they're available. Viewing must not change the file. If the user asks "what should I work on next?" or "mark task 3 done" mid-session, point them at `/task-list-runner` (which atomically claims the next task) — don't invoke `next` here as a read.

**Exit codes used by the viewer:** 0 success · 1 IO error · 10 task id not found · 12 schema validation · 13 JSON parse. Treat 10 as a clean "no such id" message back to the user; 1/12/13 mean the file itself is broken — surface the error and stop.

---

## Phase 1 — Parse arguments

From `$ARGUMENTS`, extract:

- **`<task-id>`** — the first positional argument that parses as a positive integer. If present, Phase 3 shows that task's full details; otherwise Phase 3 shows the summary view.
- **`--file <path>`** — optional. Points to a `.json` (used directly) or a `.md` file (read its YAML frontmatter `task_file` field, which points to the JSON). If given, skip Phase 2 entirely.

If the path is a `.md` file, validate the pointer: the JSON it points to must exist and parse. If validation fails, report a clear error (e.g., `"task_file points to X which does not exist"`) and stop.

---

## Phase 2 — Locate the task file (if no `--file` was given)

Auto-locate by content (not filename):

1. Glob `docs/exec-plans/active/*.json`. For each candidate, run `task_list_cli.py --file <path> status`. Treat as valid if exit is 0 (file parses + schema is well-formed). Cache the per-candidate `status` payload — counts and `plan` path are what you'd display in step 3 anyway.
2. If no JSON candidates match, repeat the scan against `docs/exec-plans/active/*.md`. For each, read its YAML frontmatter `task_file` field and run `status` against the JSON it points to (same accept criterion).
3. Resolve:
   - **Exactly one match:** use it.
   - **Multiple matches:** list them with their cached `status` summaries (counts + `plan` path) and ask the user to pick one. Single selection — `task_list_cli.py` only takes one `--file`. Do NOT pick by recency or alphabetical order.
   - **No matches:** report `"No task-list files found in docs/exec-plans/active/"` and stop. Don't offer to create one — that's `task-list-builder`'s job.

**Divergence from `task-list-runner`:** the runner accepts a candidate only if `status.remaining > 0` (its job is to run unfinished work). The viewer accepts `remaining >= 0` so finished plans remain inspectable. This divergence is intentional; don't "fix" it.

From here on, "the task file" means the chosen JSON.

---

## Phase 3 — Display

Branch on whether Phase 1 captured a `<task-id>`.

### If `<task-id>` was given — single-task detail view

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py" \
    --file TASK_FILE_PATH get --id <N>
```

Print the returned JSON to the user. If the CLI exits 10 (task id not found), report that cleanly with the file path so the user knows where to look:

> `Task <N> not found in <file path>. Run /task-list-viewer with no args to see the available task IDs.`

### Otherwise — summary view

Display, in this order:

1. **File path** of the chosen task file.
2. **Counts** (from the cached `status` payload from Phase 2, or one fresh `status` call if `--file` was given): `total / pending / in-progress / complete / failed`.
3. **Plan path** — the `plan` field from `status`, so the user can open the paired markdown report in their editor.
4. **In-progress task ID**, if any. Run `task_list_cli.py --file <path> list --status in-progress` and print the first task's `id` and `title`. If the array is empty, omit this line entirely — don't print "(none)".
5. **Pending task titles** — run `task_list_cli.py --file <path> list --status pending` and print one row per task in the form `<id> · <title> · <effort>`. If the array is empty, print `"All tasks complete."` and skip the rows.

A reasonable rendering:

```text
File:        docs/exec-plans/active/2026-05-07-a3f2-extract-helper.task-list-builder.json
Counts:      6 total · 2 pending · 1 in-progress · 3 complete · 0 failed
Plan:        docs/exec-plans/active/2026-05-07-a3f2-extract-helper.task-list-builder.md
In-progress: #4 — Wire up the new endpoint
Pending:
  5 · Write tests for the new endpoint · low
  6 · Update the API docs · low
```

---

## Failure modes — prevent these

- **Don't mutate the task file.** This skill is read-only. Never call `next`, `start`, `finish`, or `verify` from here, and never `Edit`/`Write` the JSON. If the user wants to advance the plan, send them to `/task-list-runner`; if they want to revise it, send them to `/task-list-builder` in rewrite mode.
- **Don't invent CLI verbs** like `view`, `inspect`, `show`, or `info`. The CLI's verb set is fixed — argparse rejects anything else and prints help on every wrong guess. The right read verb is always `status`, `list`, or `get`.
- **Don't bypass the CLI** with `cat`, `jq`, inline `python3 -c '...'`, or `Read` against the task JSON to extract a single field. Every read goes through `task_list_cli.py`. Bypassing the CLI skips schema validation; a corrupt file should fail loudly here, not be silently parsed.
- **Don't auto-pick when multiple files match.** Phase 2 step 3's "ask the user" branch is non-negotiable. Picking by recency or alphabetical order has burned past iterations.
- **Don't fabricate or build a missing task list.** If Phase 2 finds nothing, stop. Don't fall through to `task-list-builder` and don't write a placeholder JSON.
- **Don't render the in-progress line when there is no in-progress task.** Empty `list --status in-progress` means the line gets omitted, not printed as `"(none)"` or `"-"`. The summary should reflect what's there, not what could be there.
- **Don't widen the file glob.** `docs/exec-plans/active/*.json` is the only directory inspected — finished plans live there until they're moved or deleted, and there is no `archive/` or `done/` location to scan. If a user wants to inspect a file outside that directory, they pass `--file <path>`.
