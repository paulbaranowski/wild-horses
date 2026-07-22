# update-git-repos

Pull every configured git repo from `origin/<branch>` in one shot.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

```text
/update-git-repos
```

Also model-invoked - trigger phrases include "update all my git repos", "pull main on all repos", "sync repos from origin".

## What it does

1. **Pulls everything** (`pull-all` is the authoritative existence check - it reports `{"empty": true}` when nothing is configured, so there's no separate precheck). For each repo it's clean and on-branch, fast-forwards via `git fetch` + `git merge --ff-only` against the stable tracking ref (never `git pull`, never `FETCH_HEAD`), and applies each dirty repo's configured action inline (`stash`/`skip`) or defers it (`ask`).
2. **Bootstraps** when the config is empty: scans a root directory the user names (auto-detecting each found repo's default branch), lets them pick which to track, then re-runs the pull.
3. **Handles dirty repos** whose effective action is `ask` - stash, fetch/merge, then pop, or skip, per-repo. The user can set a lasting default (or a per-repo override) with `set-action` so they stop being asked.
4. **Handles a misconfigured-bare repo** (a real working tree with a stray `core.bare=true`) by offering the one-line `fix-bare` repair, then falling through to the normal pull/dirty handling once it's fixed.
5. **Summarizes** tersely: only repos that changed or need attention (pulled, dirty, wrong-branch, errors) get listed by name; the rest are rolled into a single "Up to date: N repos" count.

## Install

The skill ships with the `update-git-repos` plugin:

```text
/plugin install update-git-repos@wild-horses
```
