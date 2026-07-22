# autonomous

Autonomously take an issue/ticket or a plan file from a link (or path) to an opened pull request, with no human in the loop.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

```text
/autonomous https://linear.app/.../ISSUE-123
/autonomous ~/plans/myrepo/feature.md
```

Also model-invoked - trigger phrases include "work this issue autonomously", "take this ticket end-to-end", "do this AFK". Also composed by `plan-keeper:plan-do`, which hands off an in-context plan directly.

## What it does

1. **Resolves the task.** In priority order: a URL in the arguments (GitHub/Linear/other, fetched via `gh`/the `linear` CLI/WebFetch), a file path in the arguments (its full content is the spec), a task already in the conversation, or - the one allowed stop - nothing resolvable at all.
2. **Owns every decision.** Never stops to ask, even for architectural calls or an empty issue body; picks the simplest interpretation consistent with the codebase and records the choice (and alternatives considered) in the PR's Decisions section.
3. **Holds a 10-rule code-style bar** throughout: typed boundaries, no `any`/raw dicts, validate-at-the-edge, explicit control flow, no hidden mutation, injected collaborators, small single-purpose functions, loud failures, why-not-what comments, red-green-refactor.
4. **Implements, tests, and simplifies** - runs the project's tests (fixing any failure, never skipping), then a three-lens review (code reuse, quality, efficiency) and fixes every finding directly.
5. **Runs a bounded reasoning-gaps review** (critical findings only, reusing the `harness` plugin's specialist prompts) on changed files, then commits.
6. **Gets an independent review of the committed diff** - `wild-pr:review` in `--report --effort high` mode by default, falling back to an ad-hoc sub-agent only if unavailable - and iterates implement → review until it converges (no remaining substantive findings, not just "the same findings as last time").
7. **Opens the PR** following the target repo's own conventions (title/description format from its `CLAUDE.md`/`AGENTS.md`/`git log`), with a Decisions section for every ambiguous call. No "Generated with Claude Code" footer, no Co-Authored-By trailer.
8. **Tends the PR** with `wild-pr:babysit`, owning the outer loop itself for up to 5 rounds (stopping early once CI is green and threads are addressed), then stops - the human review loop happens out-of-session.

## Install

The skill ships with the `autonomous` plugin:

```text
/plugin install autonomous@wild-horses
```
