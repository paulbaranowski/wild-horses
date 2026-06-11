---
name: plan-update
description: Use when the user asks to change the agent on a plan, promote a plan to todo, mark a plan as ready, set a plan's status, or edit plan frontmatter. Mutates `~/plans/<repo>/<file>.md` frontmatter via plan_keeper_cli.py file-meta set.
---

# plan-update

Edit the frontmatter of an existing plan in `~/plans/<repo>/`. The bundled `plan_keeper_cli.py file-meta set` does the atomic write — one self-documenting flag per field; this skill's job is to identify the plan, identify which field(s) to change, and route to the CLI after user confirmation.

## Quick reference

- **Target:** `~/plans/<repo>/<filename>` (active state — not `done/` or `deferred/`).
- **Field → flag:** `Agent` → `--agent`, `Status` → `--status`, `Kind` → `--kind`, `Completed on` → `--completed-on`, `Plan-keeper Ticket` → `--plankeeper-ticket`, `Linear Ticket` → `--linear-ticket`, `Jira Ticket` → `--jira-ticket`. (`--ticket` is **not** a value flag — it _locates_ a plan by any of its id fields, like push; write an id with the matching per-system flag.)
- **`--status` is lifecycle-aware:** active states (`backlog`/`todo`/`in-progress`/`in-review`) rewrite in place, but `--status done`/`--status deferred` **relocate** the plan into `done/`/`deferred/` (and `done` stamps `Completed on`) — exactly what `plan-done` does. Prefer `plan-done` for completing a plan; reach here for `done`/`deferred` only when editing other fields in the same breath.
- **Status vocabulary:** `backlog` (default; fetched but not dispatched — confirm via `crew status <id>`), `todo` (the status gate for dispatch — but **not** sufficient alone: groundcrew also requires an `Agent:` tag and a registered repo, see [../../groundcrew/README.md](../../groundcrew/README.md#what-makes-a-plan-dispatchable)), `in-progress` (set by groundcrew's markInProgress hook), `in-review` (set by groundcrew's markInReview hook when the PR opens), `done` (set by plan-done when archiving). The middle values (`in-progress`, `in-review`, `done`) are normally written by the system — set them by hand only if you know why.
- **Kind vocabulary:** `idea` / `prd` / `design` / `spec` / `exec-plan` — the document type, validated against this closed set (see [../../plan-kinds.md](../../plan-kinds.md)). Set by `plan-save`; correct it here if it was inferred wrong.
- **Common edits:**
  - Promote: `--status todo` sets the status gate; groundcrew won't dispatch until the plan also has an `Agent:` tag, so pair it with `--agent claude` (or queue via `plan-crew`, which stamps the Agent for you).
  - Change model: `--agent codex`.
  - Reset: `--status backlog`.
  - Reclassify: `--kind design` (changes how `plan-do` routes the plan).
- **Confirmation:** required before any mutation.

## Procedure

### 1. Identify the plan

If the user just referenced a specific file ("this plan", "the one I just saved"), use that.

Otherwise, list active plans:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list
```

**Run this command fresh every time you reach this step — including on a re-invocation later in the same conversation.** Never reprint an earlier listing from memory: plans get saved, archived, or change status between turns, so a cached list can be stale. The numbered list you show must come from the output you just ran.

Present numbered to the user; they pick.

### 2. Identify the field(s) to change

Match the user's invocation:

- "promote to todo" / "mark ready" / "set status to todo" → `--status todo`
- "change agent to codex" / "use codex" → `--agent codex`
- "back to backlog" / "reset" → `--status backlog`
- "it's actually a spec/design/prd/idea/exec-plan" / "reclassify" → `--kind <value>`
- "set the linear ticket to ENG-123" → `--linear-ticket ENG-123` (use `--jira-ticket` / `--plankeeper-ticket` for the other systems)
- Anything else: ask which field, which value.

Multiple fields → pass multiple flags in one call.

### 3. Confirm

> About to update `<filename>`:
>
> - `<Key>: <old>` → `<new>`
> - (repeat per field)
>
> Proceed?

Do not skip — even for "obvious" promotions.

### 4. Run the update

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta set \
  --file "$HOME/plans/<repo>/<filename>" \
  --<flag> <value>
```

Pass additional field flags in the same call as needed (e.g. `--status todo --agent codex`). `--ticket <id>` is an alternative to `--file` for **locating** the plan — it finds the plan by any of its id fields (`Plan-keeper Ticket` / `Linear Ticket` / `Jira Ticket`) across all repos (exactly one of `--file`/`--ticket` is required); don't confuse it with the per-system value flags (`--plankeeper-ticket` / `--linear-ticket` / `--jira-ticket`), which _write_ an id.

### 5. Confirm the result

Show the user the updated frontmatter:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta get \
  --file "$HOME/plans/<repo>/<filename>"
```

## Edge cases

- **Plan has no frontmatter** — CLI exits 2 with "no frontmatter". Tell the user to re-save via plan-save (which injects defaults) and retry.
- **Field has no flag** — `set` only edits the known fields (the field→flag list above). A field with no flag isn't editable here; for a genuinely new custom field, point the user at the spec for extending `_FRONTMATTER_FIELDS`.
- **Invalid `--kind` / `--completed-on`** — CLI exits 2 (Kind must be in the closed set; date must be `YYYY-MM-DD`) and the file is left untouched (inputs are validated before any write).

## Notes

- Setting an active status (`backlog`/`todo`/`in-progress`/`in-review`) only rewrites frontmatter in place. Setting `--status done`/`--status deferred` relocates the file into `done/`/`deferred/` (the CLI's lifecycle behavior) — same as `plan-done`. Always confirm before invoking, since that move is destructive; prefer `plan-done` for completing a plan.
- This skill never creates files — that's plan-save's job.
- The mutation is atomic (tmp file + fsync + os.replace) so a crash mid-update can't corrupt the plan.
- For promoting many plans at once, or browsing the queue across all repos, use the `plan-crew` skill — it's the cross-repo, multi-select counterpart. plan-update stays the targeted single-plan / current-repo editor (and the way to set `Agent` and the per-system ticket fields, etc.).
