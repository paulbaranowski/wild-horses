# Global plan-keeper config

A single file at `~/plans/.plankeeper-global.json` holding plan-keeper state that isn't tied to one repo. Parallel to the per-repo `~/plans/<repo>/.plankeeper.json` (which holds Linear / Jira credentials and defaults), but global in scope. The current inhabitant is the monorepo-subpath → groundcrew-alias mapping consumed by `repo name`; future state lands at the same top level.

## When does it exist?

It is **created on demand** — the first `pk repo alias add ...` writes it. A user who doesn't work in a monorepo will never see this file. Absent file behaves identically to `{"aliases": []}`.

## Schema

```json
{
  "aliases": [
    {
      "remote": "carrot",
      "subpath": "catalog/flawless-inventory",
      "name": "maple"
    },
    {
      "remote": "carrot",
      "subpath": "frontend/web-app",
      "name": "frontend-web"
    }
  ]
}
```

- **`remote`** — the canonical repo name (the basename of `git remote get-url origin` with `.git` stripped, the same form `pk repo name` would print without aliasing).
- **`subpath`** — the path from the git toplevel to the subdirectory being aliased. Empty string `""` means "the repo root itself".
- **`name`** — the alias plan-keeper routes to. Becomes the `~/plans/<name>/` bucket and the `repository` field on dispatch.

Unknown top-level keys are preserved on a load/save round trip — a newer plan-keeper version writing `defaults` or `hooks` will not have those keys silently erased by an older client.

## CLI surface

```bash
pk repo alias add carrot/catalog/flawless-inventory maple   # register
pk repo alias add carrot maple-root                         # repo-root alias
pk repo alias list                                          # tab-separated rows
pk repo alias remove maple                                  # by alias name
```

The positional argument to `add` is the slash-separated `<remote>[/<subpath>]` form: the first segment is the remote, everything after the first slash is the subpath. Re-adding the same `(remote, subpath)` updates the entry in place — re-running with a different name swaps it. Adding a different `(remote, subpath)` that maps to a name already in use prints a stderr warning but succeeds (a deliberate choice — sometimes two subpaths really should route to the same bucket).

`remove` deletes every entry whose `name` matches (so the same name can't survive). Exits 0 on hit, exits 3 when no alias has that name.

## Validation

`add` rejects malformed input at write time so a dead alias never sits in the config waiting to surprise the user at resolve time:

- `name` follows the same rules as any other `~/plans/<repo>/` folder name — non-empty, no `/`, no `\`, not `.` or `..`. (Exit 2.)
- `subpath` is POSIX-style with no leading/trailing/double slash, no `.` / `..` segments, and no backslashes. The empty string (repo-root alias) is the only special case. (Exit 2.)

The loader also validates shape: a payload that isn't a JSON object, an `aliases` value that isn't a list, or an entry missing required string keys raises a malformed-config error (exit 5) — caught at the boundary rather than crashing somewhere deeper with an opaque `AttributeError`.

When `pk repo name` encounters a corrupted global config, it prints a one-line warning to stderr and falls back to the bare remote (preserving derive's "always return a name" contract). The warning is intentional: silent corruption that routes plans to the wrong bucket is exactly the failure mode this repo has been bitten by before.

## How aliases route plans

When `pk repo name` is invoked:

1. It computes `remote` from `git remote get-url origin`.
2. It computes `subpath` from `git rev-parse --show-toplevel` relative to `$PWD`.
3. It walks `subpath` longest-to-shortest (path-segment-aligned), matching against `aliases` with the same `remote`. First match wins — its `name` is the resolved repo.
4. If no alias matches, `remote` (or `basename "$PWD"` if not in a git repo) is the resolved repo.

See [repo-derivation.md](repo-derivation.md) for the full algorithm.

Once an alias matches, every downstream step uses the alias name:

- `plan-save` writes to `~/plans/<name>/`.
- `plan-do` lists from `~/plans/<name>/`.
- groundcrew dispatch (`crew fetch`) emits each plan with `repository: <name>`. groundcrew matches that name against its own `workspace.knownRepositories` and routes into the corresponding sparse-checkout.

## Relationship to groundcrew's `knownRepositories`

The alias name MUST also be registered in groundcrew's `workspace.knownRepositories` for dispatch to land somewhere useful — plan-keeper has no visibility into `crew.config` and doesn't validate this. `pk crew install` explicitly does NOT touch `knownRepositories` (it's per-machine state — different sparse-checkout paths on different machines).

Plan-keeper aliases, by contrast, live in `~/plans/` so they sync with the rest of the plans tree to other machines.

## Plan-keeper id stability under migration

If the user moves an existing plan between buckets (`mv ~/plans/carrot/foo.md ~/plans/maple/`), the plan's `Plan-keeper Ticket` is preserved (it's frozen in the frontmatter on first save / first fetch). `id_for_path(path)` would now produce a different id from the same plan's path (different `repo` seed), but `ensure_id` only mints when the frontmatter field is absent. This is documented as a known limitation: a manually-moved plan keeps its original id.
