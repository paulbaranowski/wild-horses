# wild-pr

Open a PR for the current branch using **summary-writer** for the title/body, then run **babysit** up to three times (stop early on clean).

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

```text
/wild-pr                      # create a PR for the current branch, then babysit it
/wild-pr develop               # base the PR against `develop` instead of the default branch
/wild-pr --draft                # pass extra gh pr create flags through
```

Also model-invoked - trigger phrases include "create a PR and babysit" and "open a PR and babysit". Composes two sibling skills in-session: [`summary-writer`](../summary-writer/) for the description, [`babysit`](../babysit/) for tending - both are read and executed in full, never paraphrased from memory.

## What it does

1. **Preflight.** Resolves the repo's actual default branch (never hard-coded); stops if an open PR already exists for the branch and asks whether to skip straight to babysitting it or abort. Commits any uncommitted work first (except suspected secrets, which it stops and asks about), then pushes if the branch has no upstream or is ahead of it.
2. **Writes the description** via `summary-writer`, taking its title and body as-is - no edit-confirmation loop, since `/wild-pr` means create now.
3. **Creates the PR** with a shell-safe `gh pr create` invocation (heredoc-quoted title and body, so apostrophes and metacharacters in conventional-commit titles survive intact).
4. **Babysits up to three passes**, owning the outer loop itself: on a `progressing` or soft-`stuck` result from `babysit`, it starts the next pass immediately rather than telling the user to re-run or wrap it with `/loop`. Stops early on a `clean` pass, or on a hard `stuck` (nothing actionable left).
5. **Summarizes** the PR URL, how many passes ran and each one's stop condition, every commit and reply made, and anything still open.

## Install

The skill ships with the `wild-pr` plugin:

```text
/plugin install wild-pr@wild-horses
```
