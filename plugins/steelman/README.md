# steelman

Argue the strongest good-faith case against the proposed changes in the current conversation or a named design/plan file. A built-in red-team voice that surfaces hidden costs, wrong assumptions, simpler alternatives, second-order effects, and the do-nothing option, so a plan gets stress-tested before it ships.

Install:

```text
/plugin install steelman@wild-horses
```

## Skill

| Skill                              | Invoke             | What it does                                                                                                                                                                |
| ---------------------------------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **[`steelman`](skills/steelman/)** | `/steelman [path]` | Restates the target proposal in one or two sentences, then builds the strongest good-faith case against it and delivers it directly in the chat, strongest objection first. |

Also model-invoked - trigger phrases include "steelman the case against this", "argue against these changes", "red-team this plan".

## How it works

Works through whichever angles actually apply - hidden or underestimated cost, load-bearing assumptions, a simpler alternative (including doing nothing), second-order effects, reversibility, who pays, and whether the status quo is genuinely fine - and skips the ones that don't bite. Quality over coverage: three sharp objections beat ten weak ones.

Each objection is stated plainly and backed by a concrete consequence, not a vague worry, and the response ends with a one-line bottom line: the condition under which the opposition would drop. This is a steelman, not a strawman - the goal is the objection a well-informed opponent would actually raise, after granting the proposal every reasonable assumption, not a quota of complaints.
