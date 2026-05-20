---
name: plan-done
description: Use when the user finishes a plan, marks a plan done, archives a plan, says "I'm done with the plan", or asks to clear a completed plan.
---

# plan-done

Archive a completed plan from `~/plans/<repo>/` into `~/plans/<repo>/done/`, with a `Completed on:` date written into the file's frontmatter. The bundled `plan_keeper_cli.py` handles the actual stamp-and-move (atomic write to `done/`, then unlink the source). This skill identifies which plan to archive, confirms with the user, and handles collisions.

## Quick reference

- **Moves:** `~/plans/<repo>/<file>.md` → `~/plans/<repo>/done/<file>.md`.
- **Stamp:** the CLI writes `Completed on: YYYY-MM-DD` into the YAML frontmatter at the top of the archived file.
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

**Look for a clear plan candidate from this session:**

- A plan opened by `plan-do` earlier in the conversation.
- A plan referenced by filename in recent messages.
- A plan whose topic clearly matches what we just finished working on (e.g., we just completed implementation of a feature whose plan is sitting in `~/plans/<repo>/`).

If exactly one candidate is identifiable, propose it:

> Mark `<filename>` done? (Y/n, or name a different one.)

**If no clear candidate, or the user rejects the proposed one**, list active plans via the CLI:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list
```

(Add `--override <name>` if found.) Display the output as a numbered list, newest-first, and ask the user to pick. If the output is empty, tell the user there are no active plans for this repo and stop. Do not silently fall back to another folder.

### 2. Confirm before mutating

Show the user the source and destination paths and the action:

> Will move `~/plans/<repo>/<file>.md` → `~/plans/<repo>/done/<file>.md` and record today's date as `Completed on:` in the frontmatter. Proceed?

Wait for the user's response. Do not proceed without an answer.

### 3. Invoke the CLI

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" archive \
  --file <filename>
```

Add `--override <name>` if step 1 found one. The CLI does: read source, write `Completed on: <today>` into the YAML frontmatter, atomic-write to `~/plans/<repo>/done/<filename>`, unlink the source. Today's date is in the user's local timezone.

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

- This skill is the only `plan-*` skill that mutates the `~/plans/` tree by moving files. `plan-save` creates; `plan-do` reads only.
- The completion date is stored as `Completed on: YYYY-MM-DD` in the YAML frontmatter, keeping the plan body intact and making the date machine-readable without disturbing markdown rendering.
- Archived plans live in `~/plans/<repo>/done/`. `plan-do`'s `list` only enumerates direct children of the repo dir — `done/` files are excluded from the active-plans list automatically.
- Sibling skills in the `plan-` family (`plan-save`, `plan-do`) share the same CLI and the same `~/plans/<repo>/` tree.
