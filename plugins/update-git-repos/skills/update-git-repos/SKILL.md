---
name: update-git-repos
description: Pull every repo listed in ~/.config/wild-horses/update-git-repos/repos.json from origin/<branch> via the bundled CLI, including bootstrapping that config (auto-discovery under a root directory, plus manual add/remove). Use when the user asks to update all their git repos, pull main on all repos, sync repos from origin, refresh local clones, or run `git pull` across a known set of repos.
---

# update-git-repos

Pull every repo in the config from `origin/<branch>` in one shot. When a working tree is dirty, `pull-all` applies the configured default action — `ask` (the default), `skip`, or `stash` — resolved per-repo then globally; `ask` reports the repo for the workflow to prompt you to choose stash → fetch/merge → pop, or skip.

## Quick reference

- **Config:** `~/.config/wild-horses/update-git-repos/repos.json` — `{"default_dirty_action": "ask|skip|stash", "repos": [{"path": "...", "branch": "main", "dirty_action": "ask|skip|stash"}, ...]}`. Both action keys are optional; `default_dirty_action` defaults to `ask`, and a per-repo `dirty_action` overrides it. **Resolution:** per-repo `dirty_action` → top-level `default_dirty_action` → `ask`.
- **CLI:** `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" <subcommand>`
- **Subcommands:** `bootstrap-discover --root DIR`, `add PATH [--branch B]`, `remove PATH`, `set-action <ask|skip|stash|inherit> [--repo PATH]`, `list`, `pull-all`, `pull-one PATH [--stash]`, `fix-bare PATH`
- **`list`** prints the full config as JSON. Use it only when the user wants to _preview_ what's configured without pulling — it is **not** part of the pull flow (step 1 below uses `pull-all`'s own empty-config signal instead).
- **Every subcommand prints JSON on stdout.** Parse it; do not screen-scrape.
- **Exit codes:** 0 means the command itself succeeded (per-repo errors live inside the JSON's `status` field); non-zero means the command itself failed (bad path, corrupt config, etc).

## Procedure

Start with the pull (step 1) — `pull-all` is the authoritative existence check. It returns `{"empty": true, ...}` (exit 0) when the config has no repos; only then fall back to bootstrap (step 2). **Don't precheck with a separate `list` call** — that dumps the entire config into context to answer a yes/no `pull-all` already answers, and `pull-all` re-reads the same config from disk anyway, so you pay for the config twice.

### 1. Pull everything

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" pull-all
```

**Empty config:** if the output is `{"empty": true, ...}`, there are no repos configured — go to step 2 (bootstrap), then re-run this command.

Otherwise the CLI inspects every repo, fast-forwards the clean+on-branch ones (a `git fetch` of the one ref, then `git merge --ff-only` against the stable `refs/remotes/origin/<branch>` tracking ref — never `FETCH_HEAD`, so a concurrent fetch in a sibling worktree can't make the ff step spuriously fail), and applies each dirty repo's configured action inline (`stash` does a stash-pull-pop, `skip` leaves it untouched, `ask` defers to step 3); the remaining repos are reported without mutation. The output is `{"results": [...], "up_to_date": N}`. **`up_to_date` is the count of already-current repos — they are deliberately omitted from `results` to save tokens; just report the number in step 4.** Parse the `results` array for everything else. Each entry has a `status` field:

| `status`                   | meaning                                                                                                                                                                                                                                                                                                                   | next action                                                                                             |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `pulled`                   | fast-forward succeeded; carries a `stat` (git `--shortstat`) when the diff is non-empty                                                                                                                                                                                                                                   | report in step 4 — show the `stat`                                                                      |
| `up-to-date`               | already current — **never appears in `results`**; surfaced only as the top-level `up_to_date` count                                                                                                                                                                                                                       | render the `up_to_date` count as the one-line `Up to date (no change): N repos` summary in step 4       |
| `dirty`                    | working tree has tracked-file changes; effective action is `ask`, so not pulled                                                                                                                                                                                                                                           | step 3 (prompt)                                                                                         |
| `skipped`                  | working tree was dirty and the effective action is `skip`; carries `reason: "dirty"`, repo untouched, no prompt                                                                                                                                                                                                           | report under "Skipped:" in step 4                                                                       |
| `wrong-branch`             | current branch ≠ configured branch; not pulled                                                                                                                                                                                                                                                                            | report and skip (don't touch — user may be mid-work on a feature branch)                                |
| `missing`                  | path doesn't exist anymore                                                                                                                                                                                                                                                                                                | report; offer to `remove`                                                                               |
| `not-a-repo`               | path exists but isn't a git repo                                                                                                                                                                                                                                                                                          | report; offer to `remove`                                                                               |
| `bare-misconfig`           | a stray `core.bare=true` is set on a real working tree (some worktree tooling re-sets it); git refuses every work-tree op, so it wasn't pulled; carries `error`                                                                                                                                                           | step 3.5 (offer the one-line `fix-bare`)                                                                |
| `pull-failed`              | the `git fetch` failed (no `origin`, network error, or a remote that needed credentials — prompts are disabled, so auth fails fast instead of hanging) **or** the `--ff-only` fast-forward hit genuinely diverged history; the transient multi-branch `FETCH_HEAD` race is handled internally and no longer surfaces here | report with the `error` field                                                                           |
| `stash-failed`             | the configured `stash` action's `git stash push` failed before any pull; repo left untouched; carries `error`                                                                                                                                                                                                             | report with the `error` field                                                                           |
| `pulled-with-pop-conflict` | the configured `stash` action fast-forwarded but `git stash pop` hit a merge conflict; conflict markers are now in the working tree and the stash is gone; carries `pop_error`                                                                                                                                            | tell the user clearly and surface `pop_error` so they know what to resolve                              |
| `timed-out`                | the `git fetch` exceeded the timeout (slow/unreachable remote); the whole git process group — `fetch`/`index-pack` included — was killed and its partial pack cleaned up, repo left untouched                                                                                                                             | report with the `error` field; suggest checking the remote/network, or raise `UPDATE_GIT_REPOS_TIMEOUT` |
| `low-disk`                 | free space on the repo's filesystem is under the floor (default 5 GB, `UPDATE_GIT_REPOS_MIN_FREE_GB`); refused before fetching so a giant pack can't half-write and strand a `tmp_pack_*`; repo untouched                                                                                                                 | report with the `error` field; tell the user to free disk space, then re-run                            |

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

Then re-run step 1.

### 3. Handle dirty repos (only those with `status: dirty`)

A repo only comes back `dirty` when its effective action is `ask` — `skip` repos already came back `skipped`, and `stash` repos were already pulled inline. So for each `dirty` repo, ask the user via AskUserQuestion: **"Stash & pull"** or **"Skip"**.

To stop being asked, the user can store a default with `set-action`:

```bash
# Always skip dirty repos from now on:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" set-action skip
# Always stash-pull-pop dirty repos from now on:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" set-action stash
# Per-repo override (wins over the global default):
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" set-action stash --repo /Users/paul/dev/foo
# Clear a per-repo override (fall back to the global default):
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" set-action inherit --repo /Users/paul/dev/foo
# Go back to being asked each time:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" set-action ask
```

When the user says things like "always skip dirty repos", "stash repo X by default", or "go back to asking", call `set-action` accordingly before (or after) the pull.

For "Stash & pull":

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" pull-one <PATH> --stash
```

The result `status` is one of:

- `pulled` / `up-to-date` — stash popped cleanly. Report in step 4.
- `pulled-with-pop-conflict` — fast-forward succeeded but `stash pop` hit a merge conflict. **The conflict markers are in the working tree now; the stash is gone.** Tell the user clearly and surface `pop_error` so they know what to resolve.
- `stash-failed` / `pull-failed` — surface the `error` field; the working tree is unchanged.

### 3.5 Handle misconfigured-bare repos (only those with `status: bare-misconfig`)

A `bare-misconfig` repo is a real working tree with a stray `core.bare=true` — git refuses every work-tree operation until it's unset, so the repo wasn't pulled. For each such repo, ask the user via AskUserQuestion: **"Unset core.bare and pull"** or **"Skip"**.

For "Unset core.bare and pull":

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/update_repos_cli.py" fix-bare <PATH>
```

The result carries `action: "unset-bare"` and `status_after` (the repo's status once the flag is gone). Act on `status_after`:

- `ready` — the flag is gone and the repo is clean+on-branch. Run `pull-one <PATH>` and report the result in step 4.
- `dirty` / `wrong-branch` — the flag is gone but the repo now needs the normal dirty/wrong-branch handling. Fall through to step 3 (dirty prompt) or report and skip (wrong-branch), as appropriate.
- `bare-misconfig` — the unset didn't stick: the worktree tooling re-set `core.bare=true` between the unset and the re-check. The result carries `error`. Surface it and tell the user the flag is being re-set by an external tool (re-running `fix-bare` will just lose the same race) — the durable fix is upstream, outside this skill's scope.
- `fix-failed` — `git config core.bare false` itself failed; surface the `error` field, repo left as-is.

On "Skip": report it under "Skipped:" in step 4 and leave the flag in place.

### 4. Summary

Keep it terse — fewer tokens is better. **Only list repos that need the user's attention**: those that were `pulled` (so they see what changed) and those with a problem (`skipped`, `wrong-branch`, `dirty`, errors). **Render the `up_to_date` count as a single line** — `Up to date (no change): N repos` (the CLI already excluded those repos from `results`, so there are no paths to enumerate). If a whole group is empty, omit its header entirely.

For each `pulled` repo, append its `stat` field verbatim after the path; if a `pulled` entry has no `stat` (a commit with no textual diff), just show the path. Use simple ASCII prefixes (no emoji unless the user opted in).

```text
Pulled:
  + /Users/paul/dev/foo (main): 5 files changed, 120 insertions(+), 30 deletions(-)
  + /Users/paul/dev/bar (master): 2 files changed, 8 insertions(+), 1 deletion(-)

Up to date (no change): 18 repos

Skipped:
  ! /Users/paul/dev/qux (dirty)
  ! /Users/paul/dev/quux (wrong-branch: on feature, expected main)

Errors:
  x /Users/paul/dev/old (missing) — suggest `remove`
  x /Users/paul/dev/slow (timed-out) — check the remote/network
  x /Users/paul/dev/big (low-disk: 3.1 GB free) — free disk space, then re-run
```

When everything was already current and nothing else happened, the entire summary is just `Up to date (no change): N repos` — nothing more.

## Common mistakes

- **Don't run raw `git pull` in a loop.** The CLI sequences status-check → conditional pull with `--ff-only` per repo and reports a structured outcome. Hand-rolled loops bypass dirty-tree safety and lose the JSON contract step 3 depends on.
- **Don't precheck with `list` before pulling.** `pull-all` already reports `{"empty": true, ...}` when the config is empty, so a `list` precheck is a strictly more expensive duplicate of that signal — it pulls the whole config into context for a yes/no. Start with `pull-all`; reach for `list` only when the user wants to preview the config without pulling.
- **Don't override the configured dirty action with a prompt.** Honor the resolved action: `skip` repos come back `skipped` (just report them), `stash` repos are already pulled, and you only prompt for repos that come back `dirty` (effective action `ask`). Never auto-stash a repo whose effective action is `ask`, and never re-prompt for one whose stored action is `skip`/`stash`.
- **Don't try to "fix" `wrong-branch` repos by checking out the configured branch.** The user may be intentionally on a feature branch. Report it and move on.
- **Don't skip the empty-config case.** `pull-all` returns `{"empty": true, ...}` (exit 0) when the config has no repos — handle that by routing into step 2, not by treating it as an error.
- **Don't include `node_modules` or build dirs in `bootstrap-discover` results manually.** The CLI already skips them; if you see surprising paths, report them so we can extend the skip list, don't filter on the agent side.
- **Don't pass `--branch` to `add` unless the user asked for a non-default branch.** The CLI's auto-detection from `origin/HEAD` is what makes the config self-maintaining.
- **Don't run `fix-bare` on a genuinely bare repo.** `fix-bare` guards itself: it refuses (exit 2, "not a misconfigured-bare repo") anything that isn't a real working tree wrongly flagged `core.bare=true`. That refusal is correct, not an error to work around — never try to force `core.bare false` on a repo the guard rejects.
