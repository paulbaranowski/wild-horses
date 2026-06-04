---
name: plan-list
description: Use when the user asks to list, show, or see the saved plans for a repo (or a named bucket) without picking one to work on — a read-only inventory of what's in ~/plans/<repo>/.
---

# plan-list

List the saved plans in `~/plans/<repo>/`, newest-first, grouped by `Status`. This is the **read-only** member of the `plan-keeper` family: it shows what's there and stops. It never reads a plan body, never changes `Status`, and never moves or deletes a file. To pick a plan up and route it into the pipeline, that's `plan-do`; to manage the cross-repo groundcrew queue, that's `plan-crew`.

The bundled `plan_keeper_cli.py` does all the work (repo derivation, newest-first sort, status grouping, empty-state detection); this skill just invokes it and presents the result.

## Quick reference

- **Reads:** every `~/plans/<repo>/*.md` with frontmatter (one level deep; `done/`, `deferred/` are separate states).
- **Writes:** nothing. No confirmation gate — there's nothing to confirm.
- **`<repo>`:** auto-derived from the current repo, or an explicit override — see [../../repo-derivation.md](../../repo-derivation.md).
- **Default view:** active plans (`in-progress`, `in-review`, `todo`, `backlog`), grouped in that order, newest-first within each group.
- **Other states:** `--state done` and `--state deferred` list the archived / shelved plans on request.
- **Sibling boundary:** `plan-list` only _shows_. The moment the user wants to act on a plan — start it, queue it, edit frontmatter, archive it — hand off to `plan-do` / `plan-crew` / `plan-update` / `plan-done`.

## Procedure

### 1. Determine the repo

Check the user's invocation for an explicit override. Recognize:

- "list plans in `<name>`"
- "plan-list `<name>`"
- "show plans for/from `<name>`"
- "what plans are in the `<name>` folder/bucket"

If present, normalize it per [../../repo-derivation.md](../../repo-derivation.md) and pass it as `--override <name>`. Otherwise the CLI auto-derives from the current directory — no flag needed.

### 2. List the plans

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list --status in-progress,in-review,todo,backlog
```

Add `--override <name>` if step 1 found one. The CLI groups the active plans in the given status order, newest-first within each group, and prints one `status<TAB>filename` line per plan. Any active plan whose `Status` is _not_ one of those four (e.g. a `Status: deferred` plan still living in the active tree) is excluded from stdout and summarized on **stderr** as a `note: N other active plan(s) hidden (…)` line.

**Run this command every time you reach this step — including on a re-invocation later in the same conversation.** Plans get saved, started, archived, and re-statused between turns, so a list you printed a moment ago may already be stale. The list you show must come from the output you just ran, never from memory.

If the user asked for archived or shelved plans instead, swap the flags:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list --state done      # completed, in done/
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list --state deferred  # shelved, in deferred/
```

### 3. Present the result

**If stdout has lines**, display them as a grouped, numbered list — show each plan's status tag (the token _before_ the tab) so the user sees what's queued vs. in flight vs. untriaged. The filename is the part _after_ the tab. Example:

```text
Plans in ~/plans/wild-horses/:

In progress:
  1. 2026-06-03-pr-status-hook-plugin.md

Backlog:
  2. 2026-06-03-update-git-repos-live-progress.md
  3. 2026-05-12-pyright-skill-coercion-trap.md
```

If stderr carried a `note: N other active plan(s) hidden (…)` line, mention it below the list (with the count) so the user knows there are active plans with an off-list `Status`. Offer to re-run with no `--status` filter — `list` alone prints **every** active plan newest-first (bare filenames, no grouping), hiding nothing.

**If stdout is empty but stderr has the hidden-plans note**, don't say "no plans": there are active plans, just none with one of the four listed statuses. Surface the count and run plain `list` (no `--status`) to show them.

**If stdout is empty and stderr has no note**, the current repo has no active plans at all. List the alternatives so the user can pick another bucket:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" repo list
```

Output is one repo per line with state counts (e.g., `herds: active=15 done=22 deferred=2`). Show it and let the user name a different repo (re-run step 2 with `--override`). If `repo list` is also empty, `~/plans/` doesn't exist yet — tell the user `plan-save` hasn't been used on this machine.

## Common mistakes

- **Don't reprint a previously shown list from memory.** Step 2's command must be re-run on every invocation; the plan set changes between turns, so the list you display must always come from the command you just ran.
- **Don't read or summarize any plan body.** This skill lists filenames and status only. Opening a plan to describe it is `plan-do`'s job (it reads the _one_ plan the user picks).
- **Don't write anything.** `plan-list` never flips `Status`, never archives, never edits frontmatter. If the user wants to act on a plan, hand off to the sibling that does that.
- **Don't say "no plans" when stdout is empty but you haven't run `repo list`.** An empty current repo doesn't mean an empty machine — show the other repos before concluding.

## Notes

- `plan-list` is the read-only inventory view; the rest of the family acts on plans: `plan-do` starts one, `plan-crew` manages the groundcrew queue, `plan-update` edits frontmatter, `plan-done` archives, `plan-save` creates, `plan-linear`/`plan-jira` file a ticket. All share the same CLI and the same `~/plans/<repo>/` tree.
- The `<repo>` derivation works correctly inside git worktrees — all worktrees of a project share the `origin` remote and resolve to the same `~/plans/<repo>/` folder.
