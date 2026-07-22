# plan-linear

File a plan as a Linear ticket, or update an existing one from a plan; supports first-time Linear setup inline.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo. See [`../../ticket-systems.md`](../../ticket-systems.md) for the per-repo config shape. Sibling skill [`plan-jira`](../plan-jira/) does the same for Jira.

## Invoke

```text
/plan-linear
/plan-linear last     # push the most recent plan from this conversation
/plan-linear file      # pick a saved file
```

Also model-invoked - trigger phrases include "push a plan to Linear", "file a Linear ticket from a plan".

## What it does

1. **Checks Linear config** for the current repo (`~/plans/<repo>/.plankeeper.json`); if unconfigured, walks an inline setup wizard - collect the API key, validate against the API, refresh a cache of teams/projects/labels/users, and pick defaults (team → project → assignee → labels, in that dependency order).
2. **Resolves the target plan** - the most recent one in conversation (saving it first if it isn't on disk yet) or a picked file from the active list.
3. **Reads any existing ticket reference** in the plan's frontmatter (`Linear Ticket`, or a different system's ticket id if it was filed elsewhere).
4. **Confirms the push** - create a new ticket, update the existing one, or (if it's tracked in a different system) offer to overwrite the reference.
5. **Executes the push** via the CLI, handling auth/network/API errors distinctly (including offering `--force-new` if a referenced ticket no longer exists).
6. **Writes the new ticket id back** into the plan's frontmatter on create (never on update), so re-pushing the same plan updates the same ticket.

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
