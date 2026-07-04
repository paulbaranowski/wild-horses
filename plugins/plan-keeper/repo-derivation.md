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

Use whichever of these succeeds first, **verbatim** — do NOT slugify:

1. Run `git remote get-url origin 2>/dev/null`. If it succeeds, take `basename "$URL" .git`.
2. Otherwise, fall back to `basename "$PWD"`.

The git remote name is the canonical repo identifier. Rewriting underscores to hyphens would create a folder that diverges from the actual repo (e.g., `herds_mobile_app` must stay `herds_mobile_app`, not become `herds-mobile-app`).

## Why override-and-auto-derive use different normalization rules

Note the asymmetry between step 2 (override: lowercased, whitespace → hyphen) and step 3 (auto-derive: verbatim). It is deliberate.

- A user typing "save in General Folder" expects `general-folder`, not the literal capitalized phrase.
- A git remote name (`herds_mobile_app`) is already canonical. Applying slug normalization to it would create a folder name that doesn't match the actual repo.

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

## Determine the root (multiple plan roots)

A `<repo>` folder lives under a **root** tree. Most installs have exactly one root (`~/plans`, named `default`) and you can ignore this section entirely: reads see everything and saves land in the one root. Once a user has more than one root (e.g. a `work` and a `personal` tree, listed by `pk root list`), the `<root>` dimension sits _above_ `<repo>`.

The division of labor is deliberate and asymmetric:

- **Reads never need a root.** `plan-do`, `plan-list`, the queue, and ticket resolution **union across every root** automatically. When more than one root exists, each plan is labelled `root/...` so two same-named plans from different trees stay distinguishable. Never ask the user which root to read from; show them all. A user who wants to narrow can pass `--root <name>`.
- **Only `plan-save` picks a root**, and it does so by routing, not by asking:
  1. If the repo already has a folder in **exactly one** root, save there.
  2. If the repo is new to **every** root, save to the **default** root.
  3. If the repo **straddles two or more** roots, save to the **default** root (no prompt).

### Root override in the invocation

The user can name a root explicitly. Recognize a root when the named destination matches a configured root name (check `pk root list`):

- "save this to personal" (and `personal` is a root) → pass `--root personal`; the repo is still auto-derived.
- "save to personal/herds" (slash form) → pass `--root personal --override herds`.
- "save to herds" where `herds` is **not** a root name → it's a repo, as before (`--override herds`), no `--root`.

A bare token is a root **only** when it matches a registered root name; otherwise it is a repo, exactly as it was before multiple roots existed. When in doubt, resolve the token against `pk root list` first.

### Fixing a mis-routed save

If a save lands in the wrong root, relocate it with `pk move --file <path> --root <dest>` (or `--ticket <id>`). Move preserves the plan's id, its `done/`/`deferred/` subdir, and any paired `.json`/`.md` sibling. Do not hand-`mv` a plan across roots - that can orphan a paired file or resurrect an archived one.
