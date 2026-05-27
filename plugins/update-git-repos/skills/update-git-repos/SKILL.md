---
name: update-git-repos
description: Use when the user asks to update all their git repos, pull main on all repos, sync repos from origin, refresh local clones, or run `git pull` across a known set of repos. Pulls every repo listed in ~/.config/wild-horses/update-git-repos/repos.json from origin/<branch> via the bundled CLI. Also handles bootstrapping that config (auto-discovery under a root directory, plus manual add/remove).
---

# update-git-repos

Pull every repo in the config from `origin/<branch>` in one shot. Prompts the user per repo when a working tree is dirty so they pick stash-pull-pop or skip.

## Quick reference

- **Config:** `~/.config/wild-horses/update-git-repos/repos.json` — `{"repos": [{"path": "...", "branch": "main"}, ...]}`
- **CLI:** `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" <subcommand>`
- **Subcommands:** `bootstrap-discover --root DIR`, `add PATH [--branch B]`, `remove PATH`, `list`, `pull-all`, `pull-one PATH [--stash]`
- **Every subcommand prints JSON on stdout.** Parse it; do not screen-scrape.
- **Exit codes:** 0 means the command itself succeeded (per-repo errors live inside the JSON's `status` field); non-zero means the command itself failed (bad path, corrupt config, etc).

## Procedure

Follow the steps in order. Skip step 2 if the config already has repos.

### 1. Check the config

Run `list`. If `repos` is non-empty, go straight to step 3. If `repos` is empty, go to step 2.

### 2. Bootstrap (only when config empty)

Ask the user for a root directory to scan (suggest `~/dev` or `~` as starting points; honor whatever they say). Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" bootstrap-discover --root <DIR>
```

The output is a JSON array of `{path, default_branch, in_config}` for every `.git` found under `DIR` (does not descend into a found repo, skips `node_modules`/`.venv`/etc).

Show the user the list. Ask which repos to include — use AskUserQuestion with multiSelect when there are ≤ 4 candidates; otherwise show the list inline and ask "all of them?" / "exclude any?".

For each chosen repo, call:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" add <PATH>
```

Omit `--branch` unless the user wants a non-default branch — the CLI auto-detects from `origin/HEAD`.

Then continue to step 3.

### 3. Pull everything

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" pull-all
```

The CLI inspects every repo, pulls the clean+on-branch ones with `git pull --ff-only`, and reports the rest without mutating them. Parse the `results` JSON array. Each entry has a `status` field:

| `status`       | meaning                                                                         | next action                                                              |
| -------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `pulled`       | fast-forward succeeded                                                          | report in step 5                                                         |
| `up-to-date`   | already current                                                                 | report in step 5                                                         |
| `dirty`        | working tree has tracked-file changes; not pulled                               | step 4                                                                   |
| `wrong-branch` | current branch ≠ configured branch; not pulled                                  | report and skip (don't touch — user may be mid-work on a feature branch) |
| `missing`      | path doesn't exist anymore                                                      | report; offer to `remove`                                                |
| `not-a-repo`   | path exists but isn't a git repo                                                | report; offer to `remove`                                                |
| `pull-failed`  | `git pull --ff-only` failed (diverged history, no `origin`, network error, etc) | report with the `error` field                                            |

### 4. Handle dirty repos (only those with `status: dirty`)

For each dirty repo, ask the user via AskUserQuestion: **"Stash & pull"** or **"Skip"**. (The user picked "ask me each time" — never auto-stash and never skip silently.)

For "Stash & pull":

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" pull-one <PATH> --stash
```

The result `status` is one of:

- `pulled` / `up-to-date` — stash popped cleanly. Report in step 5.
- `pulled-with-pop-conflict` — fast-forward succeeded but `stash pop` hit a merge conflict. **The conflict markers are in the working tree now; the stash is gone.** Tell the user clearly and surface `pop_error` so they know what to resolve.
- `stash-failed` / `pull-failed` — surface the `error` field; the working tree is unchanged.

### 5. Summary

Print one line per repo, grouped by outcome when there are many. Use simple ASCII prefixes (no emoji unless the user opted in):

```text
Pulled:
  + /Users/paul/dev/foo (main): 3 files changed
  + /Users/paul/dev/bar (master): 1 file changed

Up to date:
  . /Users/paul/dev/baz (main)

Skipped:
  ! /Users/paul/dev/qux (dirty)
  ! /Users/paul/dev/quux (wrong-branch: on feature, expected main)

Errors:
  x /Users/paul/dev/old (missing) — suggest `remove`
```

## Common mistakes

- **Don't run raw `git pull` in a loop.** The CLI sequences status-check → conditional pull with `--ff-only` per repo and reports a structured outcome. Hand-rolled loops bypass dirty-tree safety and lose the JSON contract step 4 depends on.
- **Don't auto-stash without asking.** The user picked "ask me each time" deliberately. Always prompt per dirty repo.
- **Don't try to "fix" `wrong-branch` repos by checking out the configured branch.** The user may be intentionally on a feature branch. Report it and move on.
- **Don't skip the empty-config case.** `pull-all` returns `{"empty": true, ...}` (exit 0) when the config has no repos — handle that by routing into step 2, not by treating it as an error.
- **Don't include `node_modules` or build dirs in `bootstrap-discover` results manually.** The CLI already skips them; if you see surprising paths, report them so we can extend the skip list, don't filter on the agent side.
- **Don't pass `--branch` to `add` unless the user asked for a non-default branch.** The CLI's auto-detection from `origin/HEAD` is what makes the config self-maintaining.
