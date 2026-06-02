---
name: plan-update
description: Use when the user asks to change the agent on a plan, promote a plan to todo, mark a plan as ready, set a plan's status, or edit plan frontmatter. Mutates `~/plans/<repo>/<file>.md` frontmatter via plan_keeper_cli.py file-meta update.
---

# plan-update

Edit the frontmatter of an existing plan in `~/plans/<repo>/`. The bundled `plan_keeper_cli.py file-meta update --field Key=value` does the atomic write; this skill's job is to identify the plan, identify which field(s) to change, and route to the CLI after user confirmation.

## Quick reference

- **Target:** `~/plans/<repo>/<filename>` (active state — not `done/` or `deferred/`).
- **Whitelisted fields:** `Agent`, `Status`, `Ticket`, `Ticket System`, `Completed on`, `Kind`.
- **Status vocabulary:** `backlog` (default; fetched but not dispatched — confirm via `crew status <id>`), `todo` (eligible for dispatch), `in-progress` (set by groundcrew's markInProgress hook), `in-review` (manual), `done` (set by plan-done when archiving). The middle values (`in-progress`, `done`) are normally written by the system — set them by hand only if you know why.
- **Kind vocabulary:** `idea` / `prd` / `design` / `spec` / `exec-plan` — the document type, validated against this closed set (see [../../plan-kinds.md](../../plan-kinds.md)). Set by `plan-save`; correct it here if it was inferred wrong.
- **Common edits:**
  - Promote: `--field Status=todo` (makes the plan eligible for groundcrew dispatch).
  - Change model: `--field Agent=codex`.
  - Reset: `--field Status=backlog`.
  - Reclassify: `--field Kind=design` (changes how `plan-do` routes the plan).
- **Confirmation:** required before any mutation.

## Procedure

### 1. Identify the plan

If the user just referenced a specific file ("this plan", "the one I just saved"), use that.

Otherwise, list active plans:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list
```

Present numbered to the user; they pick.

### 2. Identify the field(s) to change

Match the user's invocation:

- "promote to todo" / "mark ready" / "set status to todo" → `--field Status=todo`
- "change agent to codex" / "use codex" → `--field Agent=codex`
- "back to backlog" / "reset" → `--field Status=backlog`
- "it's actually a spec/design/prd/idea/exec-plan" / "reclassify" → `--field Kind=<value>`
- Anything else: ask which field, which value.

Multiple fields → repeat `--field` flag.

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
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta update \
  --file "$HOME/plans/<repo>/<filename>" \
  --field "<Key>=<value>"
```

Add additional `--field` flags as needed.

### 5. Confirm the result

Show the user the updated frontmatter:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta get \
  --file "$HOME/plans/<repo>/<filename>"
```

## Edge cases

- **Plan has no frontmatter** — CLI exits 2 with "no frontmatter". Tell the user to re-save via plan-save (which injects defaults) and retry.
- **Unknown field** — CLI rejects (whitelist). If the user wants a custom field, point them at the spec for extending `_FRONTMATTER_FIELDS`.
- **Value contains `=`** — pass it as-is; the CLI splits only on the first `=`.

## Notes

- This skill never moves files between active/done/deferred — that's plan-done's job.
- This skill never creates files — that's plan-save's job.
- The mutation is atomic (tmp file + fsync + os.replace) so a crash mid-update can't corrupt the plan.
- For promoting many plans at once, or browsing the queue across all repos, use the `plan-crew` skill — it's the cross-repo, multi-select counterpart. plan-update stays the targeted single-plan / current-repo editor (and the way to set `Agent`, `Ticket`, etc.).
