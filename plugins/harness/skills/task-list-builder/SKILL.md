---
name: task-list-builder
description: Build or rewrite a structured task list (JSON + paired markdown report) matching the harness task-list schema. Accepts free-form text, an existing reasoning-gaps/feedback-blockers report, an existing JSON task file (in-place rewrite), or recent conversation context. Use when the user says "build the task list using task-list-builder", "rewrite the plan file using task-list-builder", or otherwise asks to convert or update a chunk of work into the harness task-list format.
user-invocable: true
disable-model-invocation: false
argument-hint: "[free-form description | path to .md report | path to .json task file (rewrite) | empty for conversation context] [--slug <name>] [--md-body-from-context] [--autofix [N]]"
---

# task-list-builder

Build a paired `.json` + `.md` task list in the format the harness loop runner consumes.

**The schema is defined in `${CLAUDE_PLUGIN_ROOT}/task-list-schema.md`.** That file is the source of truth — do not duplicate the schema here, read it.

**Arguments:** `$ARGUMENTS`

---

## Phase 0 — Parse meta-flags

Three optional flags can appear anywhere in `$ARGUMENTS`. Strip them before any other phase parses arguments.

- **`--slug <name>`** — overrides the default `task-list-builder` filename suffix. The output paths become `…<short-description>.<name>.{json,md}` instead of `…<short-description>.task-list-builder.{json,md}`. Validate that `<name>` matches `[a-z][a-z0-9-]*` (lowercase letters, digits, hyphens; must start with a letter). If validation fails, refuse and ask the user for a valid slug.
- **`--md-body-from-context`** — when writing the MD file in Phase 6, use the most recent rendered analysis report from the current conversation as the MD body (instead of synthesizing a generic body). Carries through to Phase 6.
- **`--autofix [N]`** — non-interactive build. Always suppresses the Phase 5 preview confirmation prompt; the preview itself still renders to the conversation as a visible audit trail. The optional integer N additionally truncates the task array (after Phase 4 builds it) to the first N intervention tasks plus any paired test tasks immediately following each kept intervention, renumbering `id`s sequentially. Without N, the full task array is written non-interactively. When N is provided it must be a positive integer; non-integer or non-positive values are a parse error and the skill refuses with `"--autofix N requires a positive integer"`. When N exceeds the number of intervention tasks the builder generated, the truncation is a no-op (the full array is written). Carries through to Phase 4.5 (truncation) and Phase 5 (prompt suppression). The flag name is the same one the orchestrator commands `/harness:reasoning-gaps` and `/harness:feedback-blockers` accept; it is passed through verbatim from those callers.

After stripping, what remains is the input source for Phase 1.B (path / free-form / empty).

**Typical caller patterns:**

- User running standalone: no flags. Default slug, generated MD body.
- `/harness:feedback-blockers` Phase 4: `--slug feedback-blockers --md-body-from-context`. The merged Phase 3 report is in conversation; the slug preserves provenance.
- `/harness:reasoning-gaps` Phase 4: `--slug reasoning-gaps --md-body-from-context`. Same shape.

---

## Phase 1 — Detect the output target and the content source

These are two independent decisions. Make both before continuing.

### A. Output target: fresh build vs. in-place rewrite

It's an **in-place rewrite** if any of these are true:

- `$ARGUMENTS` contains a path ending in `.json` AND the file exists.
- The user's phrasing includes "rewrite", "update", or "regenerate" + a reference to an existing plan/task file (e.g., "rewrite the plan file using task-list-builder").

If it's a rewrite but no path was given, find the existing file (where `<slug>` is the slug captured in Phase 0, defaulting to `task-list-builder`):

1. List `docs/exec-plans/active/*.<slug>.json`.
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

Determine the slug suffix:

- If Phase 0 captured a `--slug <name>`, use that name.
- Otherwise, use the default: `task-list-builder`.

Build the two paths using the chosen slug:

```text
docs/exec-plans/active/<DATE>-<RUN_ID>-<short-description>.<slug>.json
docs/exec-plans/active/<DATE>-<RUN_ID>-<short-description>.<slug>.md
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
2. **Paired test tasks.** For every task with `createsNewCode: true`, the next task in the array must be a test task: title starts with `"Write tests for "`, `createsNewCode: false`, `resolves: []`, `effort: "low"`, `agentValidations` like `"Test file follows project test conventions"` and `"At least N test cases covering …"` (inspection-verifiable structural facts only — see `task-list-schema.md`'s `agentValidations` definition for the no-duplication-with-verifySteps rule).
3. **`createsNewCode` discipline.** `true` only when the task creates new callable code (functions, classes, methods, services, models, protocols). `false` for restructuring, annotations, documentation, config edits.
4. **Defaults.** Every task starts with `status: "pending"` and `log: null`. Don't pre-fill these.
5. **`agentValidations` is the input array for the per-task validation prompt.** Every task has at least one entry — a factual statement about the post-change code state that the validation subagent confirms by reading code. The schema's structural rule: **if you can write a shell command that answers the question, it belongs in `verifySteps`, not here.** Entries like `"Tests pass"`, `"No type errors"`, `"No lint errors"`, `"Compiles"` are forbidden — the validation subagent has no way to evaluate them except by re-running the commands `verifySteps` already ran (the exact duplicate-work pattern this design exists to prevent). Good entries name structural facts (`"validate_session is defined at module scope"`), behavioral facts (`"AuthMiddleware delegates to the helper"`), or documentation facts (`"module docstring lists the public API"`). Avoid vague entries like `"looks good"` or `"code is clean"`. Full contract in `task-list-schema.md`.
6. **Repo-relative paths.** All paths in `scope` and in `resolves` must be repo-relative. No local prefixes.
7. **Per-finding `**VerifySteps:**` ingestion.** When a finding/intervention in the input report carries a `**VerifySteps:**` subsection (or the heading variant `#### VerifySteps`), the resulting task **must** include a per-task `verifySteps` array transcribed verbatim from it. Format mirrors the top-level array (Phase 2's YAML-bullet shape: `- name: <slug>` / `command: <shell>`). Apply these:
   - **Don't merge per-task steps with the top-level array** — the override is total replacement. The runner's `verify --id <N>` runs **only** the per-task array when present and ignores the top-level entirely.
   - **Don't synthesize per-task overrides the report didn't request** — copy the report's block verbatim, no additions or substitutions. Tasks whose finding has no `**VerifySteps:**` field don't get a per-task array; absence is the inheritance signal. Never copy the top-level steps into a task's `verifySteps` "to be explicit" — that locks the task to a snapshot of the top-level (if the top-level changes later, this task still runs the old copy).
   - **Don't reorder the steps** — the report's order is the runner's execution order, and the runner fail-fasts on the first failure. Reordering changes which step gates the rest.
   - **Don't emit an empty per-task array** — the validator rejects it (same minimum-one-step rule as the top-level). If the finding's `**VerifySteps:**` block has no entries, omit the field entirely on the task.

   When in doubt about whether a finding's verification requirement is task-specific vs. project-wide, omit the per-task array — the top-level default is the safer fallback.

A reference example with one paired implementation+test pair lives at `${CLAUDE_PLUGIN_ROOT}/skills/task-list-builder/example.json`. The schema definition for the per-task `verifySteps` field lives in `${CLAUDE_PLUGIN_ROOT}/task-list-schema.md` under "Per-task `verifySteps` override".

---

## Phase 4.5 — Truncate to top-N interventions (only when `--autofix N` was passed with an integer)

When Phase 0 captured `--autofix N` with a positive integer N, slice the `tasks` array Phase 4 produced down to the first N intervention tasks plus any paired test tasks immediately following each kept intervention. When `--autofix` was passed without N, skip this phase entirely (no truncation; the prompt-suppression effect of `--autofix` is handled in Phase 5).

Algorithm:

1. Walk `tasks` left-to-right.
2. An **intervention task** is one whose `resolves` array is non-empty. (Test tasks have `resolves: []` per the paired-test-task rule.)
3. A **paired test task** is one matching the schema's paired-test-task rule (title starts with `"Write tests for "`, `createsNewCode: false`, `resolves: []`, `effort: "low"`) AND placed at index `i + 1` where index `i` is an intervention task already kept.
4. Keep tasks in encounter order until N intervention tasks have been kept. For each kept intervention at index `i`, also keep `tasks[i + 1]` if it satisfies the paired-test-task rule.
5. Discard everything else.
6. **Renumber `id`s sequentially** (`1, 2, 3, ...`) per the schema's "Sequential ids" hard rule. The builder's `id` is purely positional — no other field references it.

Edge cases:

- N >= number of intervention tasks generated → keep the whole array unchanged (no warning; the orchestrator passes a user-supplied number that may overshoot a small plan, and the user's intent is "fix at least this many").
- The truncated array contains zero intervention tasks → bail with a clear error: `"--autofix N truncated to zero intervention tasks; nothing to do"`. This should be unreachable (Phase 4 always emits at least one intervention task when there are findings), but guard it anyway.

In rewrite mode, `--autofix N` operates on the rebuilt task array, not on the existing one. The semantics are the same: keep the first N interventions and their pairs, discard the rest, renumber.

Concrete walkthrough — given a 9-task list where odd-indexed entries are interventions and even-indexed ones are paired tests:

```text
[I1, T1, I2, I3, T3, I4, I5, T5, I6]   # I = intervention, T = test for preceding I
```

`--autofix 3` keeps `[I1, T1, I2, I3, T3]` (first 3 interventions; T1 and T3 are kept because they immediately follow kept interventions; I2's next task is I3, an intervention, so I2 has no pair to bring along). After renumbering: ids 1..5.

---

## Phase 5 — Preview to the user

Before writing anything, show the user a compact preview. The "Files" section depends on the output target:

**Fresh build:**

```text
Task list preview (<N> tasks):

  1. <title>           [createsNewCode: true,  effort: medium]
  2. Write tests for … [createsNewCode: false, effort: low]
  3. <title>           [createsNewCode: false, effort: low, verifySteps: per-task]
  ...

Files to write (new):
  - docs/exec-plans/active/<…>.<slug>.json
  - docs/exec-plans/active/<…>.<slug>.md

verifySteps (top-level default):
  1. <name>: <command>
  2. <name>: <command>
scope: <N files>

Proceed? (yes / edit / cancel)
```

Annotate any task with its own `verifySteps` override using `[verifySteps: per-task]` next to its title-line metadata, as task 3 above shows. Tasks that inherit the top-level default carry no such annotation — absence of the annotation means the task uses the top-level steps. The top-level array is shown once at the bottom; per-task overrides are not expanded inline (the JSON has them; the preview just flags which tasks are involved).

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

When Phase 0 captured `--autofix` (with or without N), render the preview block above to the conversation (so the user can see what's about to be written) but **omit the `Proceed? (yes / edit / cancel)` prompt**. Treat the preview as auto-confirmed and proceed directly to Phase 6. The preview render itself is preserved — it remains a visible audit trail of what's about to be written. Only the interactive prompt is suppressed.

When `--autofix` is not set, await the user's response and branch:

- **yes** → continue to Phase 6.
- **edit** → ask the user what to change (titles, splits, merges, `agentValidations` entries), apply changes, re-show the preview.
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

The MD body comes from one of two sources, decided by Phase 0:

**Default — synthesize from the JSON.** The body mirrors the JSON in human-readable form:

- A short `## Context` paragraph explaining what this task list is for.
- A `## Scope` section listing the files in `scope` (repo-relative).
- A `## Tasks` section with one subsection per task: title as `### N. <title>`, then `**What:**`, `**Resolves:**`, `**Effort:**`, `**Creates new code:**`, `**Acceptance criteria:**` (bulleted list).

**`--md-body-from-context` — copy a pre-rendered analysis report verbatim.** When the caller passed this flag, find the most recent rendered analysis report in the current conversation (typical sources: the merged report from `/harness:feedback-blockers` or `/harness:reasoning-gaps` Phase 3) and use it verbatim as the body — exactly as it appeared, headings and all. Do **not** edit, summarize, or re-format. The frontmatter above is still added by the builder so the file shape stays uniform across callers.

If `--md-body-from-context` is set but no rendered analysis report is found in conversation, refuse and ask the caller for the body — do **not** silently fall back to the synthesized body, since callers using this flag are committing to a specific deliverable shape that the synthesized body would not satisfy.

The markdown is for humans to read — the loop runner does not modify it.

---

## Phase 7 — Report and hand off

Pick the message that matches the output target:

**Fresh build:**

```text
Wrote task list:
  JSON: <path>.<slug>.json   (<N> tasks)
  MD:   <path>.<slug>.md
```

**Rewrite, MD preserved:**

```text
Rewrote task list:
  JSON: <path>.<slug>.json   (<N> tasks, OVERWRITTEN)
  MD:   <path>.<slug>.md     (preserved — not modified)
```

**Rewrite, MD created (because it was missing):**

```text
Rewrote task list:
  JSON: <path>.<slug>.json   (<N> tasks, OVERWRITTEN)
  MD:   <path>.<slug>.md     (created — none existed)
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
- **Synthesizing the MD body when `--md-body-from-context` was passed.** The flag is a contract: the caller has a specific deliverable shape (typically the merged Phase 3 analysis report from `/harness:feedback-blockers` or `/harness:reasoning-gaps`) that the synthesized body would not satisfy. If the conversation does not contain a rendered analysis report, halt and ask — do not silently fall back.
- **Inventing a slug.** When `--slug` is not passed, the slug is `task-list-builder` (the default). Don't infer a slug from the input description or context. Slugs are explicit caller-supplied provenance markers.
- **Don't silently coerce `--autofix 0` (or any non-positive integer) to 1.** Refuse with the parse-error message `"--autofix N requires a positive integer"`. The user explicitly asked for zero, which is meaningless; surfacing the typo is the helpful response, and silently coercing would run a fix the user didn't approve.
- **Don't omit the preview render when `--autofix` is set.** The flag suppresses only the `Proceed? (yes / edit / cancel)` prompt. The preview block itself must still be rendered to the conversation — it is the user's only audit trail of what is about to be written, and removing it for non-interactive callers turns the autofix path into a silent overwrite.
