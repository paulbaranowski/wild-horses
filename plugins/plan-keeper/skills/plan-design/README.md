# plan-design

Write a structured, code-grounded design document and save it as a `Kind: design` plan file in `~/plans/<repo>/`.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

This skill is model-invoked by description — no slash command. Trigger phrases include:

```text
"write a design doc for this"
"turn this PRD into a design"
"design this change"
"write up the design"          # right after a grill-me session
"document this design"
```

Suggested by [`plan-do`](../plan-do/) when you pick up a `prd` or `reqs` plan.

## What it does

1. **Resolves the brief.** A saved plan routed in by `plan-do`, or the current conversation — typically just after `grill-me` has settled the decisions.
2. **Investigates the current state.** Dispatches an `Explore` agent to map the existing components, data flow, schema, and interface surface, so the "before" halves are real. The agent's digest is all the skill reads.
3. **Drafts the sections.** TLDR first, then **Decisions to make** when anything is open; requirements grouped past ~7 items; DB changes and RPC/API changes present only when the change touches a schema or an interface.
4. **Self-reviews.** Placeholder scan, before/after symmetry, every named touch point exists, deltas complete.
5. **Saves via the CLI.** `save --kind design`, landing the file at `~/plans/<repo>/<date>-<slug>--design.md`.

It is a **documenter**, not a designer: `grill-me` decides, this skill structures. Anything still open becomes an entry in the document's **Decisions to make** section rather than a decision invented here.

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
