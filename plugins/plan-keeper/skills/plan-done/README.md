# plan-done

Archive a completed plan from `~/plans/<repo>/` into `~/plans/<repo>/done/`, with `Status: done` and a `Completed on: YYYY-MM-DD` stamp written into the file's frontmatter.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

This skill is model-invoked by description — no slash command. Trigger phrases include:

```text
"I'm done with the plan"
"mark the plan done"
"archive this plan"
"clear the completed plan"
"done with the herds plan"           # archives in ~/plans/herds/
"archive the plan in general"        # archives in ~/plans/general/
```

Pairs with [`plan-save`](../plan-save/) (which wrote it) and [`plan-do`](../plan-do/) (which opened it).

## What it does

1. **Identifies the plan.** If the user names it by ticket id (e.g. `plan-195296912509085`, `ENG-123`), skips straight to archiving it via `--ticket <id>` (no listing needed). Otherwise, prefers a candidate visible in this session — a plan opened by `plan-do` earlier, a filename referenced in recent messages, or one whose topic clearly matches the work just completed. Falls back to a numbered listing via `plan_keeper_cli.py list --status in-progress,todo` — the plans worth finishing, **in-progress first** (the one you most likely just completed), then `todo`. Other active plans (backlog, in-review) are hidden, with a count on stderr.
2. **Confirms** the source → destination paths with the user. Always — the skill mutates the tree (file move), so it never auto-archives.
3. **Invokes the CLI:** `plan_keeper_cli.py file-meta set --status done --file ~/plans/<repo>/<filename>` (or `--ticket <id>` in place of `--file` when the plan was named by ticket id, which resolves across all repos by any of the plan's id fields). The CLI reads the source, writes `Status: done` + `Completed on: <today>` into the frontmatter, atomically writes to `~/plans/<repo>/done/`, then unlinks the source.
4. **Handles collisions.** On exit 2 (same-name file already in `done/`), asks: overwrite / suffix `-2` / cancel.
5. **Confirms.** Returns the archived absolute path.

## Why a frontmatter stamp

The CLI writes `Status: done` and `Completed on: <date>` into the plan's YAML frontmatter (it does **not** append anything to the body). Keeping the stamp in frontmatter leaves the plan body untouched, makes the completion date machine-readable alongside the other managed fields (`Agent`, `Status`, `Created`), and keeps `Status` and the `done/` location in agreement.

## Guardrails

- **Confirmation always required.** `plan-done` mutates the tree and never auto-archives. Source/destination paths are shown before the CLI is invoked.
- **Source repo is honored.** If the current repo has no active plans, `plan-done` says so and stops. It does not silently archive a plan from a different repo.
- **Already-archived plans error explicitly.** Re-archiving fails with `plan not found` (exit 3); the user is pointed at `list --state done` to verify.
- **Open files are fine.** Unix atomic-write + unlink succeeds even when the source plan is open in an editor — no special handling needed.

## Repo derivation

`<repo>` auto-derives from `git remote get-url origin` (with a `basename $PWD` fallback). Override with phrases like "done with the `<name>` plan" or "archive the plan in `<name>`". See [`../../repo-derivation.md`](../../repo-derivation.md).

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
