# steelman

Argue the strongest good-faith case against the proposed changes in the current conversation or a named design/plan file.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

```text
/steelman                       # red-team the proposal in the conversation
/steelman path/to/design.md     # red-team a specific file
```

Also model-invoked - trigger phrases include "steelman the case against this", "argue against these changes", "red-team this plan".

## What it does

1. **Identifies the proposal** - a named file, or (by default) whatever plan/design/spec/diff is currently on the table in the conversation - and restates it in one or two sentences to confirm the target.
2. **Builds the strongest case against it**, working through whichever angles actually bite: hidden or underestimated cost, load-bearing assumptions and what breaks if they're wrong, a simpler alternative (including doing nothing), second-order effects that make future changes harder, reversibility, who ends up paying the cost, and whether the status quo is genuinely fine.
3. **Delivers it directly in the chat**: the single strongest objection first, then the rest in descending order of force, each backed by a concrete consequence rather than a vague worry. Ends with a one-line bottom line - the condition under which the opposition would drop.

Constructs the objection a well-informed opponent would actually raise (a steelman, not a strawman), concedes real strengths in one line, and never softens into a both-sides summary - the case _for_ the change is not this skill's job.

## Install

The skill ships with the `steelman` plugin:

```text
/plugin install steelman@wild-horses
```
