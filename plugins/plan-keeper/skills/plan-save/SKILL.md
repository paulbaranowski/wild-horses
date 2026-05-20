---
name: plan-save
description: Use when the user asks to save a plan, save the plan, persist the plan, capture planning notes for future reference, or store a plan for later.
---

# plan-save

Save a plan from the current conversation to `~/plans/<repo>/<YYYY-MM-DD>-<topic>.md`. The bundled `plan_keeper_cli.py` handles the actual I/O (slugify, date, `mkdir -p`, atomic write, collision detection). This skill's job is to identify the plan, extract the topic, and route to the CLI.

## Quick reference

- **Target:** `~/plans/<repo>/<YYYY-MM-DD>-<topic>.md`
- **`<repo>`:** auto-derived from `git remote`/`pwd`, or override from the user's invocation — see [../../repo-derivation.md](../../repo-derivation.md).
- **`<topic>`:** first H1/H2 of the plan, used as the CLI's `--topic` (CLI slugifies).
- **Date:** today, in the user's local timezone (CLI handles).
- **Collision:** ask the user; never overwrite silently.
- **Content:** plan body verbatim — no preamble, footer, or commentary.

## Procedure

Follow these steps in order. Do not skip steps.

### 1. Identify the plan to save

Scan recent conversation messages — from both the user and the assistant — for the plan content. Prefer, in order:

- A plan the user just pasted and pointed at in the save invocation ("save this", "save what I just sent", "save the plan I pasted")
- The most recent `ExitPlanMode` plan
- The most recent "Design", "Plan", or "Approach" section the assistant produced
- A substantial numbered or bulleted markdown outline — whoever wrote it

If you cannot confidently identify a single plan, stop and ask the user which one to save. Do not guess between candidates.

### 2. Extract the topic and check for a repo override

**Topic:** Take the first H1 or H2 heading in the plan's text. Pass the raw heading (with punctuation, capitalization, whitespace) to `--topic` — the CLI slugifies. If the plan has no heading, use the first 4–6 meaningful words of the opening paragraph instead.

**Repo override:** Check the user's invocation for one of these phrases. If present, extract `<name>` and pass it as `--override`. Otherwise omit `--override` and the CLI auto-derives per [../../repo-derivation.md](../../repo-derivation.md).

- "save the plan to `<name>`"
- "save (this|it|the plan) as a `<name>` plan"
- "save to `<name>`"
- "put it in `<name>`"
- "in the `<name>` folder/bucket"

### 3. Save via the CLI

Invoke the bundled CLI with the plan body on stdin via a quoted heredoc:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" save \
  --topic "<heading text>" \
  <<'EOF'
<plan body verbatim — no preamble, no footer>
EOF
```

Add `--override <name>` if step 2 found one.

**On exit 0:** the CLI prints the written absolute path on stdout. Use that path verbatim in step 5.

**On exit 2 (collision):** stderr contains:

```text
ERROR: collision
existing: /Users/<you>/plans/<repo>/<date>-<slug>.md
suggestion: /Users/<you>/plans/<repo>/<date>-<slug>-2.md
```

Go to step 4.

### 4. Handle collision (only if step 3 exited 2)

Ask the user:

> File `<path-from-stderr>` already exists. Overwrite, save as `<slug>-2`, or pick a new topic name?

Wait for their answer, then re-invoke the CLI with the appropriate flag:

- **"save as -2" / "use the suggestion" / "suffix":** add `--on-collision suffix` and re-run step 3. (The CLI finds the lowest unused `-N`.)
- **"overwrite":** add `--on-collision overwrite` and re-run step 3.
- **"new name" / a different topic:** rerun step 3 with the new `--topic`.

### 5. Confirm

Tell the user the absolute path the CLI returned in step 3 (or the eventual step-3 retry in step 4). One line is enough:

> Saved to `/Users/<you>/plans/<repo>/<YYYY-MM-DD>-<topic>.md`

## Content discipline

The plan body sent on stdin must be **exactly** the plan as it appeared in the conversation:

- **Don't add a "Saved by Claude" header.**
- **Don't add a timestamp inside the file.**
- **Don't add a summary, preamble, or footer.**
- **Don't include commentary that wasn't in the original plan.**

The CLI writes stdin verbatim (it only appends a trailing newline if missing).

## Common mistakes

- **Pre-slugifying the topic before passing to `--topic`.** The CLI slugifies. Pass the raw heading text — e.g., `--topic "Multi-Event parent_title Design"`, not `--topic "multi-event-parent_title-design"`. (Both work, but raw is the canonical input.)
- **Forgetting `--override` when the user named a destination.** "save this as a general plan" → `--override general`. Without it, the CLI auto-derives from the current repo and the plan lands in the wrong folder.
- **Reading the CLI's stderr as a fatal error.** Exit 2 is a structured collision signal, not a failure to act on. Parse it and ask the user (step 4) — do not abort.
- **Guessing between multiple plan candidates.** Step 1 requires asking the user when more than one plausible plan exists. Don't pick the most recent one to seem helpful.

## Notes

- The `~/plans/` tree is local to the user's machine. This skill never commits anything to any repo.
- Sibling skills in the `plan-` family (`plan-do`, `plan-done`) share the same CLI and the same `~/plans/<repo>/` tree.
