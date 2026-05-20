# plan-do

Pick up a saved plan from `~/plans/<repo>/` and route it to the right next step in the planning pipeline.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

This skill is model-invoked by description — no slash command. Trigger phrases include:

```text
"do a plan"
"pick a plan to work on"
"resume a plan"
"implement one of my saved plans"
"do a plan from herds"               # lists ~/plans/herds/
"pick a plan from general"           # lists ~/plans/general/
```

Pairs with [`plan-save`](../plan-save/) (which wrote the files this skill reads) and [`plan-done`](../plan-done/) (which archives them once finished).

## What it does

```text
idea  ──► superpowers:brainstorming    ──► spec
spec  ──► superpowers:writing-plans    ──► impl plan
impl  ──► superpowers:executing-plans
                                       └──► harness:task-list-builder ──► harness:task-list-runner
```

1. **Lists active plans** via `plan_keeper_cli.py list` (newest-first, `*.md` only, `done/` excluded).
2. **Asks which one.** Displays the list and waits. Reads only the picked plan — never reads multiple candidates ahead of time (wastes context, biases classification).
3. **Classifies** it as **idea**, **spec**, **sequential implementation plan**, or **task-list-shaped plan**. The discriminator between sequential and task-list-shaped is _independence of work units_, not the words used — both use "phase" and "task" vocabulary.
4. **Suggests** the matching next skill, surfacing the alternative when signals are mixed.
5. **Confirms before invoking.** Even when classification feels obvious, the user gets a chance to redirect or steer manually.
6. **Hands off** via the `Skill` tool. The plan content is already in conversation context, so no explicit payload is needed.

## Classification cheatsheet

| Type                      | Signals                                                                                            |
| ------------------------- | -------------------------------------------------------------------------------------------------- |
| **idea**                  | Short, exploratory tone, no clear structure; "what if" / "could we" / "maybe" language             |
| **spec**                  | `## Design` / `## Architecture` / `## Requirements` sections; describes WHAT, not step-by-step HOW |
| **sequential impl plan**  | Numbered phases with explicit review/checkpoint language; linear "first do X, then do Y" flow      |
| **task-list-shaped plan** | Independent tasks, per-task acceptance criteria, dependency notation, parallel/dispatch language   |

`plan-do` is read-only — it never writes to `~/plans/`. Routing decisions always go through a user confirmation gate.

## Empty-repo behavior

If the current repo has no active plans, `plan-do` runs `plan_keeper_cli.py list-repos` (one repo per line with active/done/deferred counts) and asks the user to pick another repo or steer manually. It does **not** silently fall back to a different folder.

## Repo derivation

`<repo>` auto-derives from `git remote get-url origin` (with a `basename $PWD` fallback). Override with phrases like "do a plan from `<name>`" or "pick a plan from `<name>`". See [`../../repo-derivation.md`](../../repo-derivation.md).

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
