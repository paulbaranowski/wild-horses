# summary-writer

Write pull-request descriptions and titles that lead with the one structural idea, not a file-by-file changelog.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

Available two ways:

```text
/wild-pr:summary-writer          # rewrite the PR for the current branch
/wild-pr:summary-writer 42       # rewrite PR #42
```

Also model-invoked by description - no slash command required. Trigger phrases include:

```text
"write the PR description"
"update the PR body"
"the PR description reads like a changelog, clean it up"
```

Called automatically as step 2 of [`wild-pr`](../wild-pr/) (create-then-babysit), which uses it to generate the title and body before opening the PR.

## What it does

1. **Triages.** A trivial PR (dependency bump, one-line fix, copy tweak) gets a one-line description and stops; anything that introduces or shifts structure gets the full method below.
2. **Finds the one idea.** Reads the net diff against the base (`git diff "$(git merge-base HEAD origin/main)"..HEAD`, not `git log` on the branch) and asks: what single structural change makes all these edits necessary? Branch-internal history - reverted commits, superseded approaches, mid-PR pivots - never appears in the description, because the reviewer only ever sees the collapsed diff against the base.
3. **Recovers the constraint and the requirements.** What did the old code assume or hard-wire that the goal couldn't live with (the before/after); what did the change have to satisfy (needs, constraints, invariants, non-goals).
4. **Keeps only the load-bearing decisions** (2-4, each with one line of rationale) and names the at-risk surface that deliberately didn't change.
5. **Detects changed external surfaces** (CLI, API, config, UI) and captures one before/after example each - screenshots/recordings for visual surfaces, saved outside the worktree and handed off as placeholders since a PR body can't accept drag-and-drop uploads via API.
6. **Drafts in prose**, architecture section first: the one-idea sentence, then before/after, then decisions, then what-didn't-change.
7. **Re-derives the title** from the same one idea every run - an existing title is input, never a default, and is only kept when the re-derived title would say the same thing.
8. **Delivers.** Updates an existing PR immediately via `gh pr edit` (no confirmation needed) if one exists for the branch/argument; otherwise hands the title and body to whatever opens the PR.

Task lists, acceptance criteria, and per-file change logs are inputs the skill reads to find the one idea - they never appear in the output themselves.

## Install

The skill ships with the `wild-pr` plugin:

```text
/plugin install wild-pr@wild-horses
```
