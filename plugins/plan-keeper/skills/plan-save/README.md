# plan-save

Save a plan from the current conversation to `~/plans/<repo>/<YYYY-MM-DD>-<topic>.md`.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

This skill is model-invoked by description — no slash command. Trigger phrases include:

```text
"save this plan"
"save the plan"
"save what I just sent"
"capture these planning notes"
"save this as a herds plan"          # routes to ~/plans/herds/
"save the plan to general"           # routes to ~/plans/general/
"put it in scratch"                  # routes to ~/plans/scratch/
```

Pairs with [`plan-do`](../plan-do/) (which reads what `plan-save` wrote) and [`plan-done`](../plan-done/) (which archives it once finished).

## What it does

1. **Identifies the plan.** Scans recent messages for a user-pasted plan, the latest `ExitPlanMode` plan, a recent "Design"/"Plan"/"Approach" section, or a substantial markdown outline. If multiple plausible candidates exist, asks the user which one — never silently picks the most recent.
2. **Extracts the topic.** Uses the first H1/H2 heading in the plan as the `--topic` (the CLI slugifies). Falls back to the first 4–6 words of the opening paragraph if there's no heading.
3. **Saves via CLI.** Calls `plan_keeper_cli.py save --topic "<heading>"` with the plan body on stdin via a quoted heredoc.
4. **Handles collisions.** On exit 2 (file already exists), asks: overwrite / suffix `-2` / pick a new name. Re-invokes with `--on-collision <choice>`.
5. **Confirms.** Returns the absolute path the CLI wrote.

The CLI owns slugify, dating, `mkdir -p`, atomic write, and collision detection. The skill owns choosing _what_ to save and _how_ to handle conflicts.

## Content discipline

The plan body sent on stdin is **exactly** what was in the conversation:

- No "saved by Claude" header.
- No timestamp inside the file.
- No preamble, summary, or footer.
- No commentary the user didn't write.

The CLI writes stdin verbatim — it only appends a trailing newline if missing.

## Repo derivation

`<repo>` auto-derives from `git remote get-url origin` (with a `basename $PWD` fallback). Override with a natural phrase in the invocation. See [`../../repo-derivation.md`](../../repo-derivation.md) for the algorithm and the override-normalization rules (lowercase + whitespace-to-hyphen for overrides; verbatim for auto-derived names).

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
