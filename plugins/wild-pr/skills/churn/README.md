# churn

Diagnose why a PR keeps going through review rounds without converging, and recommend one way forward.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md), which passes through to [`../../commands/churn.md`](../../commands/churn.md). The five lens prompts live in [`references/lenses.md`](./references/lenses.md). This README is a pointer for people browsing the repo.

## Invoke

```text
/wild-pr:churn                  # the PR for the current branch
/wild-pr:churn 42               # PR #42 in the current repo
/wild-pr:churn <pr-url>         # any PR the gh CLI can read
```

Also model-invoked. Trigger phrases include "why won't this PR converge" and "this PR keeps getting review comments".

## What it does

1. **Collects** the review history with `scripts/pr_churn_cli.py collect`. The CLI writes a timeline of commits, review rounds, and findings, and prints the churn metrics: findings per round, fix commits, and hot files.
2. **Stops early** when the PR has had fewer than three review rounds.
3. **Triages** the findings. It drops reviewer noise and recovers fixes from reviews that were never posted. It marks each finding new or fix-on-fix, and groups findings by root cause.
4. **Diagnoses** each cluster with five parallel lens agents: state ownership, encapsulation, requirements, scope, and simplification.
5. **Recommends** one verdict: keep patching, refactor in this PR, split the PR, or restart with new requirements. It names the runner-up and why it lost.
6. **Saves** the report with `plan-keeper:plan-save` as `Kind: design`.

It never edits code, pushes, or replies to review comments. Requirement changes appear in the report as proposals.

## Install

The skill ships with the `wild-pr` plugin:

```text
/plugin install wild-pr@wild-horses
```
