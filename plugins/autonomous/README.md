# autonomous

Take a task — an issue/ticket **link** or a **plan/spec file** — all the way to an opened pull request, with no human in the loop. Resolve the task, then run an implement → test → review → PR → tend loop entirely on the agent's own judgment.

Install:

```text
/plugin install autonomous@wild-horses
```

## Skill

| Skill                                  | Role  | What it does                                                                                                                                                                                                                                                                                                                  |
| -------------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **[`autonomous`](skills/autonomous/)** | Ships | Resolves a task from an issue URL, a plan-file path, or an in-context plan, then implements, tests, simplifies the diff, runs a bounded reasoning-gaps review (critical only), runs an independent sub-agent review to convergence, opens a PR, and tends it through CI — making every design and implementation call itself. |

The skill is model-invoked by description and also available as `/autonomous` in the slash menu (skills aren't shown with their plugin namespace; the fully-qualified `autonomous:autonomous` is only for internal routing/tooling). Trigger phrases: "work this issue autonomously", "take this ticket end-to-end", "do this AFK", or pointing it at a plan file and asking it to just build it.

## Input resolution

Resolve the task target in priority order:

1. **URL in the arguments** — GitHub (`gh`), Linear (`linear` CLI / WebFetch), or any other host (WebFetch).
2. **File path in the arguments** — a plan, spec, or issue file; its full content is the Task.
3. **A task already in the conversation** — e.g. handed off by [`plan-keeper:plan-do`](../plan-keeper/skills/plan-do/), which reads the plan and invokes this skill.
4. **Nothing resolvable** — the one allowed stop: it states it needs an issue link or plan file, and halts.

## The autonomy contract

Once a task is resolved, the skill makes every design and implementation decision itself — it does not stop to ask clarifying questions. Ambiguity is resolved by picking the simplest interpretation consistent with the issue and the codebase's existing patterns, then recording the call (and alternatives considered) in the PR's "Decisions" section so a reviewer can push back. The only sanctioned question is the precondition failure above — raised before work begins, never mid-task.

## Workflow

1. Implement the change.
2. Run the project's tests; fix every failure (pre-existing, "unrelated", and flaky all count).
3. Simplify the diff (reuse, quality, efficiency lenses).
4. Reasoning-gaps review on changed files — fix critical findings; defer the rest.
5. Commit, then independent review (`core:cb-review` or sub-agent fallback); iterate to convergence.
6. Open a PR following the target repo's own conventions, with a "Decisions" section.
7. Tend the PR (CI + review threads) over a bounded number of rounds.
8. Stop — the human review loop happens out-of-session.

It applies the [superpowers](https://github.com/obra/superpowers) suite (brainstorming, writing-plans, test-driven-development, systematic-debugging, requesting-code-review, verification-before-completion, finishing-a-development-branch) as the discipline throughout.
