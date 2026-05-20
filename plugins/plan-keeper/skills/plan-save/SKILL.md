---
name: plan-save
description: Use when the user asks to save a plan, save the plan, persist the plan, capture planning notes for future reference, or store a plan for later.
---

# plan-save

Save a plan from the current conversation to `~/plans/<repo>/<YYYY-MM-DD>-<topic>.md`.

## Procedure

Follow these steps in order. Do not skip steps.

### 1. Identify the plan to save

Scan recent conversation messages — from both the user and the assistant — for the plan content. Prefer, in order:

- A plan the user just pasted and pointed at in the save invocation ("save this", "save what I just sent", "save the plan I pasted")
- The most recent `ExitPlanMode` plan
- The most recent "Design", "Plan", or "Approach" section the assistant produced
- A substantial numbered or bulleted markdown outline — whoever wrote it

If you cannot confidently identify a single plan, stop and ask the user which one to save. Do not guess between candidates.

### 2. Determine `<repo>`

Follow the algorithm in [../../repo-derivation.md](../../repo-derivation.md). Override phrases to recognize in this skill's invocation:

- "save the plan to `<name>`"
- "save (this|it|the plan) as a `<name>` plan"
- "save to `<name>`"
- "put it in `<name>`"
- "in the `<name>` folder/bucket"

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

Write the plan content to the target path using the `Write` tool. Save **only the plan body** — exactly as it appeared in the conversation:

- **Don't add a "Saved by Claude" header.**
- **Don't add a timestamp inside the file.**
- **Don't add a summary, preamble, or footer.**
- **Don't include commentary that wasn't in the original plan.**

### 7. Confirm

Tell the user the absolute path of the written file. One line is enough:

> Saved to `/Users/<you>/plans/<repo>/<YYYY-MM-DD>-<topic>.md`

## Notes

- The `~/plans/` tree is local to the user's machine. This skill never commits anything to any repo.
- Sibling skills in the `plan-` family (e.g., `plan-do`, `plan-done`) operate on the same `~/plans/<repo>/` tree.
