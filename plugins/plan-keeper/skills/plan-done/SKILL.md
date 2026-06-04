---
name: plan-done
description: Use when the user finishes a plan, marks a plan done, archives a plan, says "I'm done with the plan", or asks to clear a completed plan.
---

# plan-done

Archive a completed plan from `~/plans/<repo>/` into `~/plans/<repo>/done/`, with a `Completed on:` date written into the file's frontmatter. The bundled `plan_keeper_cli.py` handles the actual stamp-and-move (atomic write to `done/`, then unlink the source). This skill identifies which plan to archive, confirms with the user, and handles collisions.

## Quick reference

- **Moves:** `~/plans/<repo>/<file>.md` → `~/plans/<repo>/done/<file>.md`.
- **Stamp:** the CLI writes `Completed on: YYYY-MM-DD` into the YAML frontmatter at the top of the archived file.
- **Identifier:** a filename (`--file`) or a ticket id (`--ticket`) — see step 1 and step 3.
- **`<repo>`:** auto-derived or override — see [../../repo-derivation.md](../../repo-derivation.md).
- **Collision in `done/`:** ask the user; never overwrite silently.
- **Confirmation:** required before any file mutation.

## Procedure

Follow these steps in order. Do not skip steps.

### 1. Identify the plan to archive

Prefer conversation context; fall back to a CLI listing.

**First, check the user's invocation for a repo override.** Recognize:

- "done with the `<name>` plan"
- "plan-done `<name>`"
- "archive the plan in `<name>`"
- "in the `<name>` folder/bucket"

If present, extract `<name>` and pass `--override <name>` to all CLI calls below.

**If the user names the plan by its `Ticket:` id** (e.g. `plan-195296912509085`, `ENG-123`) instead of a filename, skip the listing and archive it directly with `--ticket <id>` in step 3. Resolution is global across `~/plans/`, so no `--override` is needed; the CLI exits 3 when no active plan carries that ticket and exits 2 (listing candidates) when more than one does.

**Look for a clear plan candidate from this session:**

- A plan opened by `plan-do` earlier in the conversation.
- A plan referenced by filename in recent messages.
- A plan whose topic clearly matches what we just finished working on (e.g., we just completed implementation of a feature whose plan is sitting in `~/plans/<repo>/`).

If exactly one candidate is identifiable, propose it:

> Mark `<filename>` done? (Y/n, or name a different one.)

**If no clear candidate, or the user rejects the proposed one**, list the plans worth finishing via the CLI — the ones you're actively working (`in-progress`) or have queued (`todo`):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list --status in-progress,todo
```

(Add `--override <name>` if found.) With `--status in-progress,todo` the CLI keeps only those two statuses, lists **in-progress first** (the plan you most likely just finished), then `todo`, newest-first within each, and prints one `status<TAB>filename` line per plan. Any other active plans (backlog, in-review, …) are summarized on **stderr** as a `note: N other active plan(s) hidden (...)` line.

Display the output as a numbered list with each plan's status tag, and ask the user to pick (the filename is the part after the tab). If stderr carried a hidden-plans note, mention it below the list so the user can ask to see the rest.

**Run this command fresh every time you reach this step — including on a re-invocation later in the same conversation.** Never reprint an earlier listing from memory: plans get saved, archived, or change status between turns, so a cached list can be stale. The numbered list you show must come from the output you just ran.

**If stdout is empty:**

- **stderr has a hidden-plans note** → nothing is in-progress or todo, but other active plans exist (e.g. all backlog). Tell the user, and offer `list` with no `--status` to pick from everything.
- **stderr is also empty** → there are no active plans for this repo. Say so and stop. Do not silently fall back to another folder.

Example output to the user:

```text
Plans to finish in ~/plans/wild-horses/:

  1. [in-progress] 2026-05-29-plan-do-design.md
  2. [in-progress] 2026-05-27-task-list-runner-refactor.md
  3. [todo]        2026-05-19-plan-save-design.md

Which one did you finish?
```

### 2. Confirm before mutating

Show the user the source and destination paths and the action:

> Will move `~/plans/<repo>/<file>.md` → `~/plans/<repo>/done/<file>.md` and record today's date as `Completed on:` in the frontmatter. Proceed?

Wait for the user's response. Do not proceed without an answer.

### 3. Invoke the CLI

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta set --status done \
  --file ~/plans/<repo>/<filename>
```

`--file` takes the **full path** (`~/plans/<repo>/<filename>`), where `<repo>` is the folder shown in step 1's listing. The CLI does: read source, write `Status: done` + `Completed on: <today>` into the YAML frontmatter, atomic-write to `~/plans/<repo>/done/<filename>`, unlink the source. Today's date is in the user's local timezone (override with `--completed-on YYYY-MM-DD`).

When the user named the plan by its ticket id, pass `--ticket <id>` instead of `--file` (the two are mutually exclusive — supply exactly one). `--ticket` resolves the plan across all repos by its `Ticket:` frontmatter; the destination `done/` is derived from the plan's own repo:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta set --status done \
  --ticket <ticket-id>
```

**On exit 0:** the CLI prints the archived absolute path on stdout. Go to step 5.

**On exit 2 (collision):** stderr contains `existing:` and `suggestion:` lines. Go to step 4.

### 4. Handle collision (only if step 3 exited 2)

Ask the user:

> File `<existing-path-from-stderr>` already exists in `done/`. Overwrite, save as `<filename>-2.md`, or cancel?

Re-invoke step 3 with the appropriate flag:

- **"save as -2" / "suffix":** add `--on-collision suffix`. (The CLI finds the lowest unused `-N`.)
- **"overwrite":** add `--on-collision overwrite`.
- **"cancel":** stop. The source plan is untouched.

### 5. Confirm

Tell the user the archived path that the CLI returned in step 3. One line is enough:

> Archived to `/Users/<you>/plans/<repo>/done/<file>.md`.

## Common mistakes

- **Auto-archiving without confirmation.** Step 2 requires showing source/destination paths and asking before invoking the CLI. The skill is destructive (file moves), not read-only.
- **Reading the CLI's stderr as a fatal error.** Exit 2 is a structured collision signal, not a failure to act on. Parse it and ask the user (step 4) — do not abort.
- **Falling back to a different repo's plans when the current one is empty.** Step 1 says: tell the user, stop. Don't archive someone else's plan because the current repo has none.
- **Re-archiving an already-archived plan.** The CLI errors with `plan not found` (exit 3) when the source isn't in the repo's top level. If you see that, the plan is likely already in `done/` — check with `list --state done`.

## Edge cases

- **No plans for the current repo** — say so, stop. Do not silently fall back to `~/plans/general/` or any other folder.
- **Conversation context proposes a plan but the user rejects it** — fall through to the listing flow in step 1.
- **Filename collision in `done/`** — ask, do not auto-resolve.
- **The plan file is open in an editor / locked** — the CLI's atomic-write + unlink will succeed on Unix even if open; no special handling needed.

## Notes

- This skill is the only `plan-*` skill that mutates the `~/plans/` tree by **moving files**. `plan-save` creates; `plan-do` only flips a started plan's `Status` to `in-progress` (no move). The status field is what makes this skill's `--status in-progress,todo` list surface the plan you were just working on first.
- The completion date is stored as `Completed on: YYYY-MM-DD` in the YAML frontmatter, keeping the plan body intact and making the date machine-readable without disturbing markdown rendering.
- Archived plans live in `~/plans/<repo>/done/`. `plan-do`'s `list` only enumerates direct children of the repo dir — `done/` files are excluded from the active-plans list automatically.
- Sibling skills in the `plan-` family (`plan-save`, `plan-do`) share the same CLI and the same `~/plans/<repo>/` tree.
