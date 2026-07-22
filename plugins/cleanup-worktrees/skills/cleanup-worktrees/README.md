# cleanup-worktrees

Reclaim disk space from stale git worktrees: scans configured roots, classifies each as cleanable or skipped, shows the cleanable set grouped by reason with sizes, and removes the ones you pick.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

```text
/cleanup-worktrees
```

Also model-invoked - trigger phrases include "clean up git worktrees", "delete merged worktrees", "free disk space from worktrees".

## What it does

1. **First-run confirm.** On an empty config, asks which roots to track (direct repos via `config add-repo`, parent directories to auto-scan via `config add-parent`); re-confirms whenever the resolved roots have changed since last time.
2. **Scans** every configured root and classifies each worktree: cleanable (`pr-merged`, `pr-closed`, `upstream-gone`, `merged-to-default`, `stale`) or skipped (`dirty`, `locked`, `unpushed` - never removed).
3. **Renders a report** grouped by reason, with per-group and total reclaimable size.
4. **Asks for selection** - all cleanable worktrees, or a specific index list to keep/remove.
5. **Removes** the chosen worktrees, re-validating each path immediately before touching it (a worktree that became dirty since the scan is left alone and reported, not forced) - never `--force`, never a hand-rolled `git worktree remove`.
6. **Summarizes** what was actually removed, how much space was reclaimed, and anything skipped or warned about (e.g. an orphaned branch ref that couldn't be pruned because it's checked out elsewhere).

## Install

The skill ships with the `cleanup-worktrees` plugin:

```text
/plugin install cleanup-worktrees@wild-horses
```
