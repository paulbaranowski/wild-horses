# babysit

Watch a PR through CI and review feedback. Auto-fix high-confidence failures and address review comments.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md), which passes through to [`../../commands/babysit.md`](../../commands/babysit.md) - that command file carries the actual workflow. This README is a pointer for people browsing the repo.

## Invoke

```text
/wild-pr:babysit                 # the PR for the current branch
/wild-pr:babysit 42              # PR #42
/wild-pr:babysit <github-url>    # by full URL
```

Also model-invoked - trigger phrases include "babysit a PR", "respond to PR comments". Called automatically as Phase 4 of [`wild-pr`](../wild-pr/) (create-then-babysit), which runs it up to three times in a row.

This is a **single pass** - it never loops or waits internally. A `progressing` or `stuck` result tells the caller to re-run it (or wrap it with `/loop`); only `wild-pr`'s own outer loop runs it repeatedly automatically.

## What it does

1. **Preflight.** Checks out the target PR; stashes pre-existing unrelated dirty files (never ones overlapping the PR's changed paths) and restores them at the end.
2. **Locates the PR** and resolves a trivial merge conflict locally (lockfile regen, additive non-overlapping edits) or aborts and reports for anything semantic.
3. **Waits for CI** with a bounded `gh pr checks --watch` timeout.
4. **Fetches review data** (unresolved threads, automated review bodies, top-level conversation comments) via the bundled `pr_babysit_cli.py`, using sentinel comments to dedupe what a prior pass already handled.
5. **Handles CI failures.** Applies high-confidence fixes inside the PR's changed surface (compile/type errors, deterministic lint, tests the PR broke); reports a diagnosis without guessing for flaky/infra/auth/external failures.
6. **Assesses every thread and comment** against a strict changed-line scope rule, verdict per item: Agree (fix it), Disagree, Already fixed, or Defer (real but out-of-scope - tagged with a follow-up sentinel, never silently expanded).
7. **Commits and pushes** only the files this pass touched, then **posts replies** to each thread plus one PR-level summary comment covering review-body findings and conversation comments.
8. **Summarizes** and picks exactly one stop condition: `clean` (nothing left to address), `progressing` (made real progress, more may remain), or `stuck` (blocked on something outside its scope - reports the specific blocker).

## Install

The skill ships with the `wild-pr` plugin:

```text
/plugin install wild-pr@wild-horses
```
