# cleanup-worktrees

Reclaim disk space from git worktrees that are safe to delete. Long-running work across multiple harnesses (raw worktrees, emdash, groundcrew, grafts) piles up dozens of worktrees - each potentially gigabytes. This plugin finds the ones that are merged, abandoned, or stale, shows them to you grouped by reason with sizes, and removes the ones you pick without ever deleting unpushed or uncommitted work.

## Install

```text
/plugin install cleanup-worktrees@wild-horses
```

## Skills

### `/cleanup-worktrees`

Scan configured roots, classify every worktree, and remove the cleanable ones you select.

First run, with an empty config, asks which roots to track and writes them to `~/.config/wild-horses/cleanup-worktrees/config.json`. Roots come in two flavors:

- **repos** - a direct path to a git checkout; every worktree sharing its object store is enumerated.
- **parents** - a directory whose subdirectories are scanned for checkouts (with a configurable `depth` for nested layouts like `emdash/worktrees/<repo>/<harness>/<branch>`).

Every subsequent run reads that config, scans, and presents the cleanable set.

## How it works

A bundled `scripts/cleanup_worktrees_cli.py` does all I/O and git/`gh`/`du` calls. Each subcommand prints JSON so the skill can sequence the report and the removal without screen-scraping. A PreToolUse hook (`hooks/hooks.json` + `scripts/cleanup-worktrees-cli-allow.sh`) auto-approves the CLI so the per-turn flow isn't repeatedly gated by the auto-mode classifier. The CLI writes config under `~/.config/wild-horses/cleanup-worktrees/` (atomic writes) and refuses to operate on any path outside `$HOME`.

### Subcommands

| subcommand                   | what it does                                                          |
| ---------------------------- | --------------------------------------------------------------------- |
| `scan`                       | discover + classify every worktree under configured roots             |
| `remove --paths P1 [P2 ...]` | re-validate and remove the given worktree paths, prune their branches |
| `config list`                | print the resolved config as JSON                                     |
| `config add-repo PATH`       | add a direct repo path                                                |
| `config add-parent PATH`     | add a parent dir whose subdirs are auto-scanned (`--depth N`)         |
| `config remove PATH`         | remove a repo or parent entry by path                                 |
| `config set-stale-days N`    | set the stale threshold (default 30)                                  |
| `config confirm`             | record approval of the current resolved roots                         |

### Classification

Per worktree, evaluated in priority order; the first match wins the displayed reason. The three **skip** reasons short-circuit and exclude the worktree from the cleanable set entirely - they are never removed.

| reason              | kind      | meaning                                                                      |
| ------------------- | --------- | ---------------------------------------------------------------------------- |
| `dirty`             | skip      | uncommitted (or untracked) changes in the working tree                       |
| `locked`            | skip      | the worktree is `git worktree lock`ed                                        |
| `unpushed`          | skip      | the branch carries commits preserved by neither a remote ref nor a merged PR |
| `upstream-gone`     | cleanable | the branch's upstream was deleted on the remote                              |
| `pr-merged`         | cleanable | a PR for the branch was merged (via `gh`)                                    |
| `pr-closed`         | cleanable | a PR for the branch was closed unmerged (via `gh`)                           |
| `merged-to-default` | cleanable | the branch tip is an ancestor of `origin/<default-branch>`                   |
| `stale`             | cleanable | the branch's last commit is older than `stale_days` (default 30)             |

A worktree that matches none of these is excluded from the output entirely. The main worktree of every group and bare repos are always excluded.

### Safety

- The main worktree of every repo is never removed.
- Dirty, locked, and unpushed worktrees are never removed. "Unpushed" is judged by `git rev-list <branch> --not --remotes <merged-pr-head>` (commits reachable from neither a remote-tracking ref nor a merged PR's head), so a branch with **no** upstream, or one whose upstream was deleted (`gone`), is still protected if it carries unique commits - the cases a naive `@{upstream}..HEAD` check would silently miss. A squash- or rebase-merged branch whose remote was deleted is correctly treated as cleanable (its commits are covered by the merged PR head), while a commit made **after** the merge keeps the worktree protected.
- `remove` re-validates each path immediately before deletion (state can drift between scan and removal) and uses `git worktree remove` **without** `--force`, so an unexpectedly dirty worktree is refused, not destroyed.
- The orphaned branch is deleted only when every commit on it is on a remote. If it carries commits on no remote, the worktree is still removed (the disk hog) but the branch ref is kept with a `branch-prune-skipped` warning, so the commits stay recoverable via `git checkout <branch>`.
- Any path outside `$HOME` is refused.
