# plan-list

List the saved plans for a repo (or a named bucket) as a read-only inventory of what's in `~/plans/<repo>/`, without picking one to work on.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo. The read-only member of the plan-keeper family - to act on a plan, hand off to [`plan-do`](../plan-do/), [`plan-crew`](../plan-crew/), [`plan-update`](../plan-update/), or [`plan-done`](../plan-done/).

## Invoke

```text
/plan-list
/plan-list herds     # a named repo instead of the current one
```

Also model-invoked - trigger phrases include "list", "show", or "see the saved plans for a repo".

## What it does

1. **Determines the repo** - auto-derived from the current directory, or an explicit override the user named.
2. **Lists the plans**, grouped by status (`in-progress`, `in-review`, `todo`, `backlog`, in that order) and newest-first within each group; `--state done`/`--state deferred` list the archived/shelved plans instead.
3. **Presents the result** as a numbered, grouped list. Reports when active plans exist with an off-list status, and offers to show them. If the current repo has no active plans at all, lists the other repos under `~/plans/` so the user can pick one.
4. **Optionally groups by project** (`--group`): clusters a project's stages (idea → spec → exec-plan, sharing a slug) together instead of by status, for seeing how one project's plans relate.

Never reads a plan's body, never changes status, never moves or deletes a file - purely a listing.

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
