# update-git-repos

A personal git/PR tooling plugin. Today it ships one skill, `/update-git-repos`. Future skills (PR babysitting, etc.) can land here without needing their own plugin.

## Install

```text
/plugin install update-git-repos@wild-horses
```

> Upgrading from the old `wrangle@wild-horses`? See
> [`skills/update-git-repos/MIGRATION.md`](skills/update-git-repos/MIGRATION.md) — the
> rename has no automatic redirect, so existing installs need a few manual steps.

## Skills

### `/update-git-repos`

Pull every configured git repo from `origin/<branch>` in one shot.

First run, with an empty config, walks you through bootstrap: scan a root directory (e.g. `~/dev`), pick which discovered repos to track, and the config is written to `~/.config/wild-horses/update-git-repos/repos.json`. Every subsequent run reads that config and pulls each repo.

For each repo, the skill:

- fetches `origin/<branch>` and fast-forwards with `git merge --ff-only` against the tracking ref when the working tree is clean and the current branch matches the configured one;
- skips and reports when the current branch is different (it never silently switches branches);
- for a dirty repo, applies the configured dirty-tree action: **ask** (the default, prompts you per repo for **skip** or **stash → pull → pop**), **skip**, or **stash**. The action resolves per-repo override first, then the config's global default, then falls back to `ask`; set it with `set-action`.

## How it works

A bundled `scripts/update_repos_cli.py` does all I/O and git calls. Each subcommand prints JSON so the skill can sequence prompts without screen-scraping. A PreToolUse hook (`hooks/hooks.json` + `scripts/update-repos-cli-allow.sh`) auto-approves the CLI so the per-turn flow isn't repeatedly gated by the auto-mode classifier. The CLI writes config under `~/.config/wild-horses/update-git-repos/` and runs `git` calls against configured repo paths.

### Subcommands

| subcommand                                             | what it does                                                                                                                          |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| `bootstrap-discover --root DIR`                        | walk `DIR` for `.git`, print discovered repos + their default branch                                                                  |
| `add PATH [--branch B]`                                | add or update one entry (auto-detects branch from `origin/HEAD`)                                                                      |
| `remove PATH`                                          | drop one entry from the config                                                                                                        |
| `set-action <ask\|skip\|stash\|inherit> [--repo PATH]` | set the default dirty-tree action, or a per-repo override (`--repo`); `inherit` clears a per-repo override back to the global default |
| `list`                                                 | print the current config as JSON                                                                                                      |
| `pull-all`                                             | inspect every repo, pull the clean+on-branch ones, report the rest                                                                    |
| `pull-one PATH [--stash]`                              | pull one repo, optionally stash-pull-pop                                                                                              |
| `fix-bare PATH`                                        | unset a stray `core.bare=true` on a real working tree, then re-report status                                                          |

## Safety

- `git fetch` of the one ref followed by `git merge --ff-only` against the stable tracking ref (never `FETCH_HEAD`): diverged history fails fast instead of auto-merging, and a concurrent fetch in a sibling worktree can't corrupt the fast-forward.
- Dirty repos are never auto-stashed unless you've configured `stash` as the default (or per-repo) action via `set-action`; with the default `ask` action you're prompted per repo.
- `stash pop` conflicts are reported explicitly so you know the conflict markers are in the working tree.
- `wrong-branch` repos are reported, never silently checked out.
- A real working tree wrongly flagged `core.bare=true` (some worktree tooling re-sets it) is detected as `bare-misconfig` rather than an opaque `pull-failed`, and `fix-bare` offers the one-line repair. Detection requires both `git rev-parse --is-bare-repository` == `true` **and** `--git-dir` resolving to a real `.git` subdir, so a genuinely bare repo (`--git-dir` == `.`) is excluded; `fix-bare` re-checks that signature and refuses anything else.
- A repo is skipped before any fetch if its filesystem has less than 5 GB free (`UPDATE_GIT_REPOS_MIN_FREE_GB` to override), so a near-full disk can't strand a half-written pack.
- Every `git` call is bounded by a timeout (default 300s, `UPDATE_GIT_REPOS_TIMEOUT` to override); on timeout, or on a fatal signal to the CLI itself, the whole `git` process group (fetch and any `index-pack` children) is killed so nothing is left writing to disk in the background.
- Config writes are atomic (`tmp + fsync + os.replace`).

## Tests

Stdlib `unittest`, no extra dependencies. Run from the repo root:

```bash
python3 plugins/update-git-repos/scripts/test_update_repos_cli.py
```

Covers config validation, allow-list shell-injection bypass, the `pull-one` preflight gate, the `pull-all` / `pull-one` happy paths (using local bare-repo fixtures so the real `git fetch` + `git merge --ff-only` semantics are exercised), and the timeout/low-disk/signal-teardown safety paths.
