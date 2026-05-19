---
name: plan-done
description: Use when the user finishes a plan, marks a plan done, archives a plan, says "I'm done with the plan", or asks to clear a completed plan. Moves the chosen plan from ~/plans/<repo>/<file>.md to ~/plans/<repo>/done/<file>.md and appends a one-line completion stamp at the bottom of the file.
---

# plan-done

Archive a completed plan from `~/plans/<repo>/` into `~/plans/<repo>/done/`, with a completion-date stamp appended to the file.

## Procedure

Follow these steps in order. Do not skip steps.

### 1. Determine `<repo>`

Use the same logic as `plan-save` and `plan-do`:

**First, check the user's invocation for an explicit override.** Patterns:

- "done with the `<name>` plan"
- "plan-done `<name>`"
- "archive the plan in `<name>`"
- "in the `<name>` folder/bucket"

If an override is present, normalize `<name>` lightly: lowercase, replace whitespace with `-`, and otherwise preserve as-is. **Underscores and existing hyphens are preserved** so repo names like `herds_mobile_app` and `temporal_cloak` round-trip exactly.

**Otherwise, auto-derive — use the result verbatim, do NOT slugify:**

1. Run `git remote get-url origin 2>/dev/null`. If it succeeds, take `basename "$URL" .git`. Use the result as-is.
2. If no git remote, fall back to `basename "$PWD"`, also verbatim.

### 2. Identify the plan to archive

Prefer conversation context; fall back to a numbered list.

**First, look for a clear candidate from this session:**

- A plan opened by `plan-do` earlier in the conversation.
- A plan referenced by filename in recent messages.
- A plan whose topic clearly matches what we just finished working on (e.g., we just completed implementation of a feature whose plan is sitting in `~/plans/<repo>/`).

If exactly one candidate is identifiable, propose it:

> Mark `<filename>` done? (Y/n, or name a different one.)

**If no clear candidate, or the user rejects the proposed one**, list active plans the same way `plan-do` does:

```
ls -1 ~/plans/<repo>/*.md 2>/dev/null | sort -r
```

Display as a numbered list with filenames only, newest first. User picks by number or filename fragment.

**If the directory doesn't exist or is empty**, tell the user there are no plans for this repo and stop. Do not silently fall back to another folder.

### 3. Confirm before mutating

Show the user the source and destination paths and the action:

> Will move `~/plans/<repo>/<file>.md` → `~/plans/<repo>/done/<file>.md` and append a completion stamp. Proceed?

Wait for the user's response. Do not proceed without an answer.

### 4. Check for collision in `done/`

If `~/plans/<repo>/done/<file>.md` already exists, stop and ask the user:

> File `~/plans/<repo>/done/<file>.md` already exists. Overwrite, save as `<file>-2.md`, or cancel?

If the user picks a numeric suffix, find the lowest unused integer (`-2`, `-3`, ...) for the destination filename.

Do not auto-resolve collisions silently.

### 5. Append the completion stamp

Before moving the file, append two lines to its body:

```

---
*Completed: YYYY-MM-DD*
```

That is: a blank line, then a horizontal rule (`---`), then the italicized completion stamp. Use `date +%Y-%m-%d` for today's date in the user's local timezone.

Implementation: `Read` the file, append the stamp to the content, `Write` the file back with the new content. Then proceed to step 6.

### 6. Move the file

```
mkdir -p ~/plans/<repo>/done/
mv ~/plans/<repo>/<file>.md ~/plans/<repo>/done/<file>.md
```

(`mkdir -p` is safe whether the directory exists or not.)

### 7. Confirm

Tell the user the new absolute path. One line is enough:

> Archived to `/Users/<you>/plans/<repo>/done/<file>.md`.

## Edge cases

- **No plans for the current repo** — say so, stop. Do not silently fall back to `~/plans/general/` or any other folder.
- **Conversation context proposes a plan but the user rejects it** — fall through to the listing flow in step 2.
- **Filename collision in `done/`** — ask, do not auto-resolve.
- **The plan file is open in an editor / locked** — the `mv` will succeed on Unix even if open; no special handling needed.

## Notes

- This skill is the only `plan-*` skill that mutates the `~/plans/` tree by moving files. `plan-save` creates; `plan-do` reads only.
- The completion stamp uses a horizontal rule + italic to render cleanly when the archived plan is viewed in any markdown reader, without disturbing the original plan body.
- Archived plans live in `~/plans/<repo>/done/`. `plan-do`'s listing uses `ls -1 ~/plans/<repo>/*.md`, which only matches direct children of the repo dir — so `done/` files are correctly excluded from the active-plans list without `plan-do` needing to know about archival.
- The override in step 1 also serves as an escape hatch: if `git remote get-url origin` returns a name that doesn't have a `~/plans/` folder, the user can name the destination explicitly.
