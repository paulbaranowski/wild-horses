# Migrating from `wrangle` to `update-git-repos`

As of `update-git-repos@1.0.0`, the plugin formerly published as `wrangle@wild-horses`
was renamed to match its sole skill. A marketplace rename has **no automatic redirect**:
the old identifier `wrangle@wild-horses` disappears from the catalog, while any machine
that installed it keeps a stale install record plus a config dir at the old path. Each
install must be migrated by hand.

You only need this doc if you installed `wrangle@wild-horses` before the rename. Fresh
installs of `update-git-repos@wild-horses` need nothing here.

## What the old name touched

On each machine, `wrangle` lived in four places:

| Location                                             | What's there                                 |
| ---------------------------------------------------- | -------------------------------------------- |
| `~/.claude/settings.json`                            | `"wrangle@wild-horses": true` (enabled flag) |
| `~/.claude/plugins/installed_plugins.json`           | the `wrangle@wild-horses` install record     |
| `~/.claude/plugins/cache/wild-horses/wrangle/<ver>/` | cached plugin files                          |
| `~/.config/wild-horses/wrangle/repos.json`           | **your repo list** (user data, not managed)  |

Steps 1–3 below are handled by the plugin CLI and clean up the first three rows. Step 4
is your data — no tooling touches it, so you move it yourself.

## Steps

```bash
# 1. Refresh the marketplace so the new catalog (with update-git-repos, without
#    wrangle) is known locally. Without this, step 3 can't resolve the new name.
claude plugin marketplace update wild-horses

# 2. Remove the old install. This works off the local install record, so it
#    succeeds even though wrangle is already gone from the refreshed catalog.
claude plugin uninstall wrangle@wild-horses

# 3. Install under the new name.
claude plugin install update-git-repos@wild-horses

# 4. Move your repo list to the new config path (skip if already moved).
[ -d ~/.config/wild-horses/wrangle ] && \
  mv ~/.config/wild-horses/wrangle ~/.config/wild-horses/update-git-repos
```

## Notes

- **Order matters.** Refresh the catalog (1) before installing the new name (3), or the
  install can't find `update-git-repos@wild-horses`.
- **Config path is the only manual data move.** The renamed CLI reads
  `~/.config/wild-horses/update-git-repos/repos.json` and nothing else — if you skip
  step 4, the skill starts from an empty config and re-runs bootstrap.
- **No notification for other users.** Anyone who installed `wrangle` gets no prompt;
  their next `claude plugin update` simply won't find it, and the skill keeps working off
  the stale cache until they run the steps above.
- **Env var renamed too.** The git-timeout override is now `UPDATE_GIT_REPOS_TIMEOUT`
  (was `WRANGLE_GIT_TIMEOUT`). Update any shell profile that exported the old name.

## Verifying

```bash
# New install present, old one gone:
claude plugin list | grep -E 'wrangle|update-git-repos'

# Config readable at the new path (prints your repo list as JSON):
python3 ~/.claude/plugins/cache/wild-horses/update-git-repos/*/scripts/update_repos_cli.py list
```

If `list` prints your repos as JSON, the rename migrated cleanly. The `*` glob just
picks up whatever version directory the install created.
