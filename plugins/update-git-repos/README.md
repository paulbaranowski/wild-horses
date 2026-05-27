# update-git-repos

Pull every configured git repo from `origin/<branch>` in one shot.

## Install

```text
/plugin install update-git-repos@wild-horses
```

## Use

```text
/update-git-repos
```

First run, with an empty config, walks you through bootstrap: scan a root directory (e.g. `~/dev`), pick which discovered repos to track, and the config is written to `~/.config/wild-horses/update-git-repos/repos.json`. Every subsequent run reads that config and pulls each repo.

For each repo, the skill:

- runs `git pull --ff-only origin <branch>` when the working tree is clean and the current branch matches the configured one;
- skips and reports when the current branch is different (it never silently switches branches);
- prompts you per dirty repo: **skip** or **stash → pull → pop**.

## How it works

A bundled `scripts/update_repos_cli.py` does all I/O and git calls. Each subcommand prints JSON so the skill can sequence prompts without screen-scraping. A PreToolUse hook (`hooks/hooks.json` + `scripts/update-repos-cli-allow.sh`) auto-approves the CLI so the per-turn flow isn't repeatedly gated by the auto-mode classifier. The CLI writes config under `~/.config/wild-horses/update-git-repos/` and runs `git` calls against configured repo paths.

### Subcommands

| subcommand                      | what it does                                                         |
| ------------------------------- | -------------------------------------------------------------------- |
| `bootstrap-discover --root DIR` | walk `DIR` for `.git`, print discovered repos + their default branch |
| `add PATH [--branch B]`         | add or update one entry (auto-detects branch from `origin/HEAD`)     |
| `remove PATH`                   | drop one entry from the config                                       |
| `list`                          | print the current config as JSON                                     |
| `pull-all`                      | inspect every repo, pull the clean+on-branch ones, report the rest   |
| `pull-one PATH [--stash]`       | pull one repo, optionally stash-pull-pop                             |

## Safety

- `git pull --ff-only` — diverged history fails fast instead of auto-merging.
- Dirty repos are never auto-stashed; you're asked per repo.
- `stash pop` conflicts are reported explicitly so you know the conflict markers are in the working tree.
- `wrong-branch` repos are reported, never silently checked out.
- Config writes are atomic (`tmp + fsync + os.replace`).
