---
name: task-list-builder
description: Build or rewrite a structured task list (JSON + paired markdown report) matching the harness task-list schema. Accepts free-form text, an existing reasoning-gaps/feedback-blockers report, an existing JSON task file (in-place rewrite), or recent conversation context. Use when the user says "build the task list using task-list-builder", "rewrite the plan file using task-list-builder", or otherwise asks to convert or update a chunk of work into the harness task-list format.
user-invocable: true
disable-model-invocation: false
argument-hint: "[free-form description | path to .md report | path to .json task file (rewrite) | empty for conversation context]"
---

# task-list-builder

Build a paired `.json` + `.md` task list in the format the harness loop runner consumes.

**The schema is defined in `${CLAUDE_PLUGIN_ROOT}/task-list-schema.md`.** That file is the source of truth — do not duplicate the schema here, read it.

**Arguments:** `$ARGUMENTS`

---

## Phase 1 — Detect the output target and the content source

These are two independent decisions. Make both before continuing.

### A. Output target: fresh build vs. in-place rewrite

It's an **in-place rewrite** if any of these are true:

- `$ARGUMENTS` contains a path ending in `.json` AND the file exists.
- The user's phrasing includes "rewrite", "update", or "regenerate" + a reference to an existing plan/task file (e.g., "rewrite the plan file using task-list-builder").

If it's a rewrite but no path was given, find the existing file:

1. List `docs/exec-plans/active/*.task-list-builder.json`.
2. If exactly one matches, use it.
3. If zero match, broaden to any `docs/exec-plans/active/*.json`.
4. If multiple still match, ask the user which one. Don't guess.

Read the existing JSON. Note its `plan` field — that's the path of the paired markdown file (Phase 6 needs this).

If it's not a rewrite, it's a **fresh build** → Phase 3 will generate new file paths.

### B. Content source

Independent of the output target, the new task content comes from one of:

1. **Report-import** — `$ARGUMENTS` contains a path ending in `.md` AND the file exists. Read it. Treat its Interventions/Findings/numbered sections as the source. Preserve any existing `file:line` references for the `resolves` array.
2. **Free-form** — `$ARGUMENTS` (after stripping any path arguments) is non-empty text. Treat it as a description of the work to break down.
3. **Conversation-context** — no description in `$ARGUMENTS`. Use the recent conversation. If conversation context is too thin to extract concrete tasks, ask the user one clarifying question instead of guessing.

In **rewrite mode**, the existing JSON is also a content source: preserve `verifySteps`, `scope`, and any task-level fields the user did not ask to change. The rewrite _intent_ (what to change) comes from $ARGUMENTS or conversation. If the user only pointed at a file with no further instructions, ask what changes they want — don't rewrite blindly.

If the input is genuinely ambiguous (e.g., a single word that could be a path or a description), ask the user. Don't silently pick.

---

## Phase 2 — Discover the verify steps

Build the `verifySteps` array — every step the per-task Agent must run to verify a task is complete. Each step is `{name, command}`. Steps run in order; first failure halts and the Agent reports which step (`name`) failed.

**Always include a `tests` step.** Discover the test command in this order; stop at the first one that yields a value:

1. `CLAUDE.md` — search for an explicit test command, "Tests", or "Run tests" section.
2. `package.json` — read `scripts.test`. If present, the command is `npm test`.
3. `pyproject.toml` or `pytest.ini` — if present, the command is `uv run pytest` (or `pytest` if the project doesn't use `uv`).
4. Fallback: ask the user what their test command is. Don't invent one.

**Add a `typecheck` step when the project has a static type-checker configured.** This is what prevents the agent from improvising `tsc --noEmit | head -80` mid-loop:

- `tsconfig.json` exists → `{ "name": "typecheck", "command": "npx tsc --noEmit" }`
- `pyrightconfig.json` exists → `{ "name": "typecheck", "command": "uv run pyright" }` (or `pyright` if the project doesn't use `uv`)
- `mypy.ini` or `[tool.mypy]` in `pyproject.toml` → `{ "name": "typecheck", "command": "uv run mypy ." }`

Order matters: put the **fastest** step first (typecheck is usually faster than the test suite, so it goes ahead of `tests`). The agent can fail fast on the cheap check before paying for the expensive one.

**Do not** add `lint` steps automatically — lint is rarely an acceptance criterion for a refactor, and adding it noisily slows every iteration. The user can ask for it during the Phase 5 preview if they want.

This matches the convention used by `/harness:reasoning-gaps` and `/harness:feedback-blockers` (see the `verifySteps` field definition in `task-list-schema.md`).

---

## Phase 3 — Compute (or reuse) the output file paths

### Fresh-build mode

Run the following from the repository root:

```bash
RUN_ID="$(openssl rand -hex 2)"
DATE="$(date +%Y-%m-%d)"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

Derive a **short-description slug** from the input: lowercase, hyphen-separated, ≤ 5 words, alphanumerics + hyphens only. Examples: `auth-middleware-refactor`, `extract-billing-service`, `pipeline-cleanup`.

Build the two paths:

```text
docs/exec-plans/active/<DATE>-<RUN_ID>-<short-description>.task-list-builder.json
docs/exec-plans/active/<DATE>-<RUN_ID>-<short-description>.task-list-builder.md
```

If `docs/exec-plans/active/` does not exist, create it. (Normally `/harness:setup` has already created it.)

### Rewrite mode

Do **not** generate a new run-id, date, or slug. Reuse the existing JSON file's path as the JSON output path, and read its `plan` field for the MD path. These paths must not change — the rewrite is in place.

Compute `TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"` only — it's used in Phase 6 if a fresh MD ends up being written (rare in rewrite mode; see Phase 6).

Then check whether the paired MD already exists:

```bash
test -f "<plan-field-from-existing-json>" && echo "MD exists" || echo "MD missing"
```

Carry that yes/no into Phase 6.

---

## Phase 4 — Build the tasks

Use the schema in `${CLAUDE_PLUGIN_ROOT}/task-list-schema.md`. Top-level fields:

- `plan` — absolute-from-repo path to the paired `.md` file (Phase 3).
- `verifySteps` — from Phase 2.
- `scope` — repo-relative paths of files involved. Strip any local-machine prefix (`/Users/...`, `C:\...`) so the file is portable. Empty array is OK if the work doesn't touch specific files yet.
- `tasks` — array of task objects, each matching the schema in `task-list-schema.md`.

**Hard rules** (enforce these — don't skip):

1. **Sequential ids.** Tasks have `id: 1, 2, 3, ...` in order. No gaps, no reordering.
2. **Paired test tasks.** For every task with `createsNewCode: true`, the next task in the array must be a test task: title starts with `"Write tests for "`, `createsNewCode: false`, `resolves: []`, `effort: "low"`, acceptance criteria like `"Test file follows project test conventions"` and `"Tests pass"` (rule documented in `task-list-schema.md`).
3. **`createsNewCode` discipline.** `true` only when the task creates new callable code (functions, classes, methods, services, models, protocols). `false` for restructuring, annotations, documentation, config edits.
4. **Defaults.** Every task starts with `status: "pending"` and `log: null`. Don't pre-fill these.
5. **Non-empty acceptance criteria.** Every task has at least one concrete, verifiable criterion. Most tasks should include `"Tests pass"`. Avoid vague criteria like "looks good" or "code is clean".
6. **Repo-relative paths.** All paths in `scope` and in `resolves` must be repo-relative. No local prefixes.

A reference example with one paired implementation+test pair lives at `${CLAUDE_PLUGIN_ROOT}/skills/task-list-builder/example.json`.

---

## Phase 5 — Preview to the user

Before writing anything, show the user a compact preview. The "Files" section depends on the output target:

**Fresh build:**

```text
Task list preview (<N> tasks):

  1. <title>           [createsNewCode: true,  effort: medium]
  2. Write tests for … [createsNewCode: false, effort: low]
  3. <title>           [createsNewCode: false, effort: low]
  ...

Files to write (new):
  - docs/exec-plans/active/<…>.task-list-builder.json
  - docs/exec-plans/active/<…>.task-list-builder.md

verifySteps:
  1. <name>: <command>
  2. <name>: <command>
scope: <N files>

Proceed? (yes / edit / cancel)
```

**Rewrite:**

```text
Task list preview (<N> tasks) — REWRITE of existing file:

  <same task listing as above>

Files:
  - <existing .json path>     (will be OVERWRITTEN)
  - <existing .md path>       (PRESERVED — will not be modified)
    OR  <existing .md path>   (will be created — no MD exists yet)

verifySteps: <from existing JSON; preserved unless changed>
  1. <name>: <command>
  2. <name>: <command>
scope: <N files>

Proceed? (yes / edit / cancel)
```

- **yes** → continue to Phase 6.
- **edit** → ask the user what to change (titles, splits, merges, acceptance criteria), apply changes, re-show the preview.
- **cancel** → stop. Don't write any files.

---

## Phase 6 — Write the files

**Always write the JSON file** (it is the canonical artifact the loop reads). In rewrite mode this overwrites the existing JSON; in fresh-build mode it creates a new file.

The MD file is conditional:

- **Fresh-build mode** → write the MD.
- **Rewrite mode, MD already exists** → do NOT write the MD. Do NOT modify it. Leave it exactly as-is.
- **Rewrite mode, MD missing** → write the MD (using the path from the existing JSON's `plan` field).

When you do write the MD, use this YAML frontmatter at the top:

```yaml
---
status: in-progress
task_file: "<path to the JSON file>"
generated: "<TIMESTAMP from Phase 3>"
---
```

The markdown body should mirror the JSON in human-readable form:

- A short `## Context` paragraph explaining what this task list is for.
- A `## Scope` section listing the files in `scope` (repo-relative).
- A `## Tasks` section with one subsection per task: title as `### N. <title>`, then `**What:**`, `**Resolves:**`, `**Effort:**`, `**Creates new code:**`, `**Acceptance criteria:**` (bulleted list).

The markdown is for humans to read — the loop runner does not modify it.

---

## Phase 7 — Report and hand off

Pick the message that matches the output target:

**Fresh build:**

```text
Wrote task list:
  JSON: <path>.task-list-builder.json   (<N> tasks)
  MD:   <path>.task-list-builder.md
```

**Rewrite, MD preserved:**

```text
Rewrote task list:
  JSON: <path>.task-list-builder.json   (<N> tasks, OVERWRITTEN)
  MD:   <path>.task-list-builder.md     (preserved — not modified)
```

**Rewrite, MD created (because it was missing):**

```text
Rewrote task list:
  JSON: <path>.task-list-builder.json   (<N> tasks, OVERWRITTEN)
  MD:   <path>.task-list-builder.md     (created — none existed)
```

In all cases, append:

```text
These files are loop metadata, not deliverables — they live in docs/exec-plans/active/
and are NOT meant to be committed. The harness loop runner can pick up the JSON file
and start executing tasks; the MD file is for humans.
```

Do **not** stage or commit either file. Do not run `git add`.

---

## Failure modes — prevent these

- **Schema drift.** If `task-list-schema.md` changes, the skill changes. Always re-read `task-list-schema.md` rather than relying on memory of past output.
- **Unpaired test tasks.** Forgetting to insert a `"Write tests for …"` task after every `createsNewCode: true` task breaks the harness loop's expectations.
- **Absolute paths in `scope` or `resolves`.** Leaks local machine structure if the file is shared.
- **Pre-filled `status` or `log`.** The loop runner expects all tasks to start as `pending` with `log: null`. Anything else looks like a partially-completed run.
- **Writing files without a preview.** Always show the preview in Phase 5; never silently overwrite.
- **Modifying an existing MD in rewrite mode.** When rewriting a JSON task file, if a paired MD already exists, do NOT touch it. Do not overwrite it, do not create a second MD with a different name, do not "refresh" it. The user has explicitly asked for the MD to be left alone. Only write an MD in rewrite mode when one does not already exist at the path recorded in the existing JSON's `plan` field.
- **Generating a new run-id in rewrite mode.** Reuse the existing file's path verbatim. Generating a new path for a rewrite would orphan the old file and break any external references to it.
