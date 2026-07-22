# review

Code review of a diff, branch, or PR against one rubric, with findings posted as anchored PR comments.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md), which passes through to [`../../commands/review.md`](../../commands/review.md). Reference material - the rubric, the multi-agent protocol, and the posting mechanics - lives under [`references/`](./references/). This README is a pointer for people browsing the repo.

## Invoke

```text
/wild-pr:review                        # current branch (its open PR, or diff vs default branch)
/wild-pr:review 42                     # PR #42, without checking it out
/wild-pr:review --effort high          # force the deep engine
/wild-pr:review --report               # findings only, no gates, no posting (for agent callers)
```

Also model-invoked - trigger phrases include "review this diff/branch/PR" and "check this change against its spec/ticket".

## What it does

1. **Resolves scope.** A PR argument reviews that PR directly (locked to reviewer mode, no checkout). No argument reviews the current branch: its open PR if one exists (author or reviewer mode depending on who opened it), otherwise the diff against the default branch.
2. **Picks an engine.** `low` (one reviewer subagent, single pass) is the default; `high` (parallel per-lens agents, one debate round, moderator filter) auto-selects past 20 changed files or 600 changed lines, or via `--effort`/phrases like "thorough".
3. **Runs a freshness preflight.** Verifies the ref being read is current before reading any code - stops and asks if the fetch failed, the tree is dirty and overlaps the diff, or local `HEAD` is behind the base.
4. **Classifies the diff** to activate lenses: Security, Database, Frontend, and Spec trigger on file-path patterns; Engineering, Minimalism, Conventions, and AntiSlop are always on.
5. **Reviews against the rubric** ([`references/review-rubric.md`](./references/review-rubric.md)) - exhaustive reading (every hunk, every active lens), selective output (only high-signal findings).
6. **Filters** before synthesis: drops hypothetical findings, do-not-raise matches, and NITs that don't clear the bar; merges near-duplicates; caps at 6 actionable findings and 8 NITs.
7. **Synthesizes** a Summary, an Actionable list (severity-ordered, each with a before/after suggested fix), Disagreements (high effort only), hidden-by-default Nits, and a Withdrawn list for transparency.
8. **Gates with the user** (skipped entirely in `--report` mode): select which findings to act on, then branch on mode - author mode gets an implementation plan and a choice to implement locally / post as review / both; reviewer mode goes straight to post / edit / cancel.
9. **Posts a single anchored review** via the GitHub Reviews API (`COMMENT`, never `APPROVE`/`REQUEST_CHANGES`) per [`references/posting-pr-review.md`](./references/posting-pr-review.md), one inline comment per selected finding.

## Install

The skill ships with the `wild-pr` plugin:

```text
/plugin install wild-pr@wild-horses
```
