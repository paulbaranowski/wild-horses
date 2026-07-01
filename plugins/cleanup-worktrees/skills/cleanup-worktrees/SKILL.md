---
name: cleanup-worktrees
description: Use when the user asks to clean up git worktrees, delete merged worktrees, prune stale worktrees, free disk space from worktrees, or list cleanable worktrees. Scans configured roots, classifies each worktree as cleanable (merged PR, upstream gone, merged into the default branch, stale) or skipped (dirty, locked, unpushed), shows the cleanable set grouped by reason with sizes, and removes the ones the user picks via the bundled CLI.
---

# cleanup-worktrees

Reclaim disk space from git worktrees that are safe to delete. The bundled CLI discovers worktrees under configured roots, classifies each, and removes the ones you select - re-validating before every removal, never `--force`, and never touching a worktree with uncommitted, locked, or unpushed work.

## Quick reference

- **Config:** `~/.config/wild-horses/cleanup-worktrees/config.json` - `{"repos": [{"path": "..."}], "parents": [{"path": "...", "depth": 1}], "stale_days": 30, "last_confirmed_at": "..."}`. `repos` are direct repo paths; `parents` are dirs whose subdirs are auto-scanned (`depth` = levels to descend, default 1).
- **CLI:** `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cleanup_worktrees_cli.py" <subcommand>`
- **Subcommands:** `scan`, `remove --paths P1 [P2 ...]`, `config list`, `config add-repo PATH`, `config add-parent PATH [--depth N]`, `config remove PATH`, `config set-stale-days N`, `config confirm`
- **Every subcommand prints JSON on stdout.** Parse it; do not screen-scrape.
- **Exit codes:** 0 means the command itself succeeded (per-worktree outcomes live inside the JSON); non-zero means a CLI-level error (bad path, corrupt config, a path outside `$HOME`).
- **Cleanable reasons:** `pr-merged`, `pr-closed`, `upstream-gone`, `merged-to-default`, `stale`. **Skip reasons:** `dirty`, `locked`, `unpushed` (never removed).

## Procedure

### 1. First-run confirm

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cleanup_worktrees_cli.py" config list
```

- **Empty config** (no `repos` and no `parents`): there is nothing to scan yet. Tell the user and ask which roots to track. Add direct repos with `config add-repo PATH` and parent dirs with `config add-parent PATH [--depth N]` (use `--depth 3` for nested layouts like `emdash/worktrees/<repo>/<harness>/<branch>`). Then run `config confirm` and continue to step 2.
- **`last_confirmed_at` is null, or the resolved roots changed since you last confirmed:** print the `repos` and `parents` paths and ask the user to confirm them with a single yes/no. On yes, run `config confirm`. On no, adjust with `config add-*` / `config remove`, then `config confirm`.
- **Already confirmed and roots unchanged:** skip straight to step 2.

### 2. Scan

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cleanup_worktrees_cli.py" scan
```

The output is `{scanned, cleanable: [...], skipped: [...], errors: [...]}`. Each `cleanable` entry carries `{index, path, repo, branch, reason, reason_detail, size_bytes, size_human}`. `scanned` is the count of candidate worktrees inspected (main worktrees and bare repos are already excluded).

**Empty `cleanable`:** report `Nothing to clean. Scanned N worktrees.` Summarize the `skipped` count if non-zero (e.g. `3 skipped: 2 dirty, 1 unpushed`). Surface any `errors` briefly. Done.

`errors[]` carries non-fatal problems (a missing parent dir, a `gh` lookup that failed, a path refused for being outside `$HOME`). A `gh-failed` / `gh-unavailable` error means PR signals were unknown for some worktrees: a worktree still classifies under any non-PR reason that applies, but one whose only cleanable signal would have been a merged PR (a squash/rebase merge with local-only commits) drops out of `cleanable` and is reported as `skipped: unpushed` until `gh` is reachable. So treat a `gh-unavailable` scan as **possibly incomplete**, not just a harmless banner - tell the user some merged worktrees may be missing and a re-run once `gh` works could surface more. It still never blocks the flow.

### 3. Render the report

Group cleanable worktrees by `reason`. For each group, show a header with the count and group size, then one line per worktree: its `index`, `path`, `size_human`, and `reason_detail` when present. Show the total reclaimable size at the top. Plain ASCII, no emoji unless the user opted in.

```text
Cleanable worktrees (total reclaimable: 31.4G):

PR merged (4 worktrees, 12.1G):
  [1] /Users/you/grafts/carrot/CAT-39996-...  4.2G  (PR #12345 merged 2026-05-10)
  [2] /Users/you/grafts/carrot/CAT-65950-...  2.4G  (PR #14102 merged 2026-06-01)

Upstream gone (2 worktrees, 6.8G):
  [5] /Users/you/groundcrew/worktrees/maple-...  3.4G

Stale >30d (3 worktrees, 12.5G):
  [7] /Users/you/grafts/carrot/old-spike-...  5.0G  (last commit 2026-01-12)

Skipped: 4 dirty, 1 locked, 2 unpushed.
```

### 4. Ask for selection

Ask a single yes/no: `Remove all 9 cleanable worktrees (reclaim ~31.4G)? (no = tell me which indices to keep or remove)`. On "no", let the user say "remove 1,3,5" or "keep 2,4"; resolve their answer to a concrete list of indices, then map those indices to the worktrees' `path` values from the scan output.

### 5. Remove

Call `remove` with the chosen worktrees' canonical paths. **Single-quote every path.** These paths come from the filesystem, so a path could contain spaces (which would split into the wrong arguments) or shell metacharacters like `$(...)`, backticks, or `;`. Single quotes are required, not double quotes: `"$(...)"` and ``"`...`"`` still run command substitution inside double quotes, so a crafted worktree path could inject a command. Single quotes suppress all expansion. If a path itself contains a single quote, escape it as `'\''`.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cleanup_worktrees_cli.py" remove --paths '/Users/you/a' '/Users/you/b'
```

The output is `{removed, skipped, errors, total_bytes_reclaimed, total_human}`:

| field       | meaning                                                                                                                                                                                                                                                                                                                                                                        |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `removed[]` | `{path, branch, bytes_reclaimed}`; may carry `warning: "branch-prune-skipped"` + `warning_detail`. A removed worktree was always safe to remove (worktrees with unique local commits are skipped as `unpushed`, never removed); the warning only means the orphaned branch ref could not be deleted afterward (e.g. it is checked out in another worktree), so the ref is kept |
| `skipped[]` | `{path, reason, detail?}`; `reason` is `now-dirty` / `unpushed` / `locked` (state changed since the scan), `already-gone` (path vanished), or `main-worktree` (refused)                                                                                                                                                                                                        |
| `errors[]`  | `{path, error, detail?}`; `error` is `remove-failed`, `not-a-worktree`, or `outside-home`                                                                                                                                                                                                                                                                                      |

`remove` re-validates every path before touching it, so a worktree that became dirty or gained unpushed commits between the scan and now is left untouched and reported under `skipped`. This is expected, not a failure.

**If `gh` is unavailable at remove time** (network down, rate-limited), a worktree that `scan` had classified `pr-merged` via a squash/rebase merge (its commits durable only via the PR head) can come back `skipped` with reason `unpushed` - the CLI can no longer confirm the merge, so it fails safe and defers the removal. Tell the user it's a transient gh issue, not lost work; re-running once `gh` is reachable will remove it. Likewise, a branch whose remote was deleted and whose only PR was **closed unmerged** is treated as `unpushed` (its commits live nowhere durable), not `pr-closed` - that narrowing is intentional, since closed-unmerged local commits are the user's to keep.

### 6. Summary

Terse report - only what changed or needs attention:

```text
Removed 6 worktrees, reclaimed 18.0G.
Skipped 1: /Users/you/foo (now-dirty since scan).
Branch-prune warnings: 1 (paulb/CAT-X - branch checked out elsewhere).
```

## Common mistakes

- **Don't remove a worktree the scan didn't mark cleanable.** Only pass `remove` the `path` values from the scan's `cleanable` array. Hand-picking arbitrary paths bypasses the reason taxonomy the user reviewed.
- **Don't pass `--force` or hand-roll `git worktree remove`.** The CLI removes without `--force` by design, so a worktree that is unexpectedly dirty is refused rather than blown away. Never substitute a raw git command to "make it work".
- **Don't treat `skipped` removals as errors.** `now-dirty`, `unpushed`, `locked`, and `already-gone` mean the CLI protected work (or the path was already gone). Report them plainly; do not retry with force.
- **Don't skip the first-run confirm.** When `last_confirmed_at` is null or the roots changed, surface the resolved `repos`/`parents` and get a yes before scanning - the user needs to see which trees will be walked.
- **Don't claim "nothing to clean" when `cleanable` is empty but `skipped` or `errors` are not.** Summarize the skip counts and any errors so the user knows worktrees were found but protected.
- **Don't re-run `scan` to refresh sizes after a removal.** The `remove` output already reports `total_bytes_reclaimed`; a fresh scan is only needed if the user wants to clean more.
- **Don't auto-confirm removals.** Step 4 requires the user to approve the set (all, or a chosen index list) before any `remove` call.
