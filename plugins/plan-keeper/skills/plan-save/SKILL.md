---
name: plan-save
description: Use when the user asks to save a plan, save the plan, persist the plan, or capture planning notes for future reference. Writes the latest plan from the conversation to ~/plans/<repo>/<YYYY-MM-DD>-<topic>.md, with an optional natural-language override of the repo folder.
---

# plan-save

Save a plan from the current conversation to `~/plans/<repo>/<YYYY-MM-DD>-<topic>.md`.

## Procedure

Follow these steps in order. Do not skip steps.

### 1. Identify the plan to save

Scan the recent conversation for the plan content. Prefer, in order:

- The most recent `ExitPlanMode` plan
- The most recent "Design", "Plan", or "Approach" section the assistant produced
- A substantial numbered or bulleted markdown outline the assistant produced

If you cannot confidently identify a single plan, stop and ask the user which one to save. Do not guess between candidates.

### 2. Determine `<repo>`

**First, check the user's invocation for an explicit override.** Common patterns:

- "save the plan to `<name>`"
- "save (this|it|the plan) as a `<name>` plan"
- "save to `<name>`"
- "put it in `<name>`"
- "in the `<name>` folder/bucket"

If an override is present, normalize `<name>` lightly: lowercase, replace whitespace with `-`, and otherwise preserve as-is. **Underscores and existing hyphens are preserved** — repo names like `herds_mobile_app` and `temporal_cloak` exist and must round-trip exactly. Examples:

- "save the plan to herds" → `herds`
- "save this as a general plan" → `general`
- "save the plan in scratch" → `scratch`
- "save to herds_mobile_app" → `herds_mobile_app` (underscores preserved)
- "save in General Folder" → `general-folder` (whitespace → hyphen, lowercased)

**Otherwise, auto-derive — use the result verbatim, do NOT slugify:**

1. Run `git remote get-url origin 2>/dev/null`. If it succeeds, take `basename "$URL" .git`. Use the result as-is — the git remote name is the canonical repo identifier, and rewriting underscores to hyphens would create a folder that diverges from the actual repo. Example: `herds_mobile_app` stays `herds_mobile_app`.
2. If no git remote (or not in a git repo), fall back to `basename "$PWD"`, also verbatim.

This works correctly inside git worktrees — the origin remote is shared with the main checkout, so all worktrees of the same project resolve to the same `<repo>` folder.

### 3. Determine `<topic>`

Derive from the plan content:

1. Take the first H1 or H2 heading in the plan.
2. Lowercase; replace runs of non-alphanumeric characters with `-`; trim leading and trailing hyphens.
3. Truncate to ~50 characters at a word boundary.

If the plan has no heading, take the first 4–6 meaningful words (skip articles, prepositions, common filler) of the opening paragraph and slugify the same way.

### 4. Build the target path

```bash
~/plans/<repo>/$(date +%Y-%m-%d)-<topic>.md
```

Use `date +%Y-%m-%d` for today's date in the user's local timezone.

### 5. Check for collision

If the target file already exists, stop and ask the user:

> File `<path>` already exists. Overwrite, save as `<topic>-2`, or pick a new name?

Wait for the user's response. Do not proceed without an answer.

If the user picks a numeric suffix, find the lowest unused integer (`-2`, `-3`, ...) and use that.

### 6. Create the directory and write the file

Run `mkdir -p ~/plans/<repo>/` (safe whether the directory exists or not).

Write the plan content to the target path using the `Write` tool. Save **only the plan body** — exactly as it appeared in the conversation. Do not add:

- A "Saved by Claude" header
- A timestamp inside the file
- A summary, preamble, or footer
- Any commentary that wasn't in the original plan

### 7. Confirm

Tell the user the absolute path of the written file. One line is enough:

> Saved to `/Users/<you>/plans/<repo>/<YYYY-MM-DD>-<topic>.md`

## Notes

- The `~/plans/` tree is local to the user's machine. This skill never commits anything to any repo.
- The override in step 2 doubles as an escape hatch: if `git remote get-url origin` returns a name the user doesn't want (forks, mis-named remotes, archived projects), the user can bypass it by naming the destination explicitly.
- Sibling skills in the `plan-` family (e.g., `plan-do`, `plan-done`) operate on the same `~/plans/<repo>/` tree.
