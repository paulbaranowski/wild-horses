# Repo folder derivation

How the `plan-*` skills (`plan-save`, `plan-do`, `plan-done`) decide which `~/plans/<repo>/` folder to use. Each skill's "Determine `<repo>`" step links here instead of re-stating this procedure.

## Algorithm

### 1. Check the user's invocation for an explicit override

Each `plan-*` skill enumerates its own verb-specific override phrases (e.g., `plan-save` recognizes "save the plan to `<name>`"; `plan-do` recognizes "do a plan from `<name>`"). If one is present, extract `<name>` and go to step 2. Otherwise jump to step 3.

### 2. Normalize the override

Lowercase the extracted name, replace runs of whitespace with `-`, and otherwise preserve as-is. **Underscores and existing hyphens are preserved** — repo names like `herds_mobile_app` and `temporal_cloak` exist and must round-trip exactly. Examples:

- "save the plan to herds" → `herds`
- "save this as a general plan" → `general`
- "save the plan in scratch" → `scratch`
- "save to herds_mobile_app" → `herds_mobile_app` (underscores preserved)
- "save in General Folder" → `general-folder` (whitespace → hyphen, lowercased)

Skip step 3.

### 3. Auto-derive from the current directory

Auto-derivation is performed by `plan-keeper repo name` — call the CLI; do NOT re-implement the algorithm in skill prose. The CLI prints the derived name to stdout. For reference, its algorithm is:

1. Run `git remote get-url origin`. If it succeeds, take `basename "$URL" .git` as `remote`. Otherwise jump to step 4.
2. Run `git rev-parse --show-toplevel` to get the monorepo root. Compute `subpath` as the path from the toplevel to `$PWD` (empty string when `$PWD` is the toplevel).
3. Load `~/plans/.plankeeper-global.json`. If present and its `aliases` list is non-empty, walk `subpath` from longest to shortest prefix (path-segment-aligned — `catalog/flawless-inventory` matches `catalog/flawless-inventory/sub` but NOT `catalog/flawless-inventory-archive`), including the empty-string prefix for repo-root aliases. For each prefix, scan `aliases` for an entry whose `remote` equals the computed `remote` and whose `subpath` equals the prefix. First match wins — return `name`.
4. Fall back to `remote` (the bare git-remote basename), or `basename "$PWD"` when no `remote` was resolved.

All names returned by this algorithm are taken **verbatim** — do NOT slugify. Rewriting underscores to hyphens would create a folder that diverges from the actual repo (e.g., `herds_mobile_app` must stay `herds_mobile_app`, not become `herds-mobile-app`); alias names are user-chosen identifiers that are already canonical.

The alias-matching step is transparent to skills — no skill executes `git remote` / `git rev-parse` / config-load itself; calling `plan-keeper repo name` picks up alias resolution automatically. To configure or inspect aliases see [global-config.md](global-config.md).

## Why override-and-auto-derive use different normalization rules

Note the asymmetry between step 2 (override: lowercased, whitespace → hyphen) and step 3 (auto-derive: verbatim). It is deliberate.

- A user typing "save in General Folder" expects `general-folder`, not the literal capitalized phrase.
- A git remote name (`herds_mobile_app`) is already canonical. Applying slug normalization to it would create a folder name that doesn't match the actual repo.
- A monorepo alias name (`maple`) is a user-chosen canonical identifier, picked at `repo alias add` time. Like a git remote name, it is taken verbatim.

If you find yourself slugifying the auto-derived name to "clean it up", stop — the underscores are load-bearing.

## Worktrees

The algorithm works correctly inside git worktrees — the origin remote is shared with the main checkout, so all worktrees of the same project resolve to the same `<repo>` folder.

## Escape hatch

The override in step 1 doubles as an escape hatch. If `git remote get-url origin` returns:

- a name the user doesn't want (forks, mis-named remotes, archived projects), or
- a name that doesn't correspond to an existing `~/plans/<repo>/` folder,

the user can bypass auto-derivation by naming the destination explicitly in their invocation.

## Extended form: `repo name --full`

`plan_keeper_cli.py repo name --full` returns `owner/name` (e.g., `herds-social/herds`) by parsing the `origin` remote URL. Used by the `push` subcommand's "Repo: …" description line.

Supported URL forms:

- `git@github.com:owner/name.git`
- `https://github.com/owner/name.git`
- `https://github.com/owner/name` (no `.git`)
- `ssh://git@github.com/owner/name.git`

Fallback when no remote or unparsable URL: `unknown/<cwd-basename>`. The fallback's `unknown/` prefix is intentional — it makes it visible in the ticket description that the derivation failed.

The `--full` mode is read-only and idempotent. It does not affect or interact with the per-repo plans directory (which still uses the basename-only form from `repo` without `--full`).
