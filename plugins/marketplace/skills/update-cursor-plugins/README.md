# update-cursor-plugins

Copy every plugin from a Cursor marketplace catalog into `~/.cursor/plugins/local` as real files (no symlinks).

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

```text
/update-cursor-plugins
/update-cursor-plugins /path/to/marketplace-root
```

Also model-invoked - trigger phrases include installing or refreshing a dual-manifest marketplace (like this one) for Cursor.

## What it does

1. **Resolves the marketplace root**: the given argument, or the current workspace if `.cursor-plugin/marketplace.json` exists there, or asks the user.
2. **Confirms the manifest exists** at `<root>/.cursor-plugin/marketplace.json`.
3. **Runs the bundled updater** (`update_cursor_plugins.py`) in one call - it copies each catalog plugin's full tree into `~/.cursor/plugins/local/<name>/`, replacing any existing destination symlink with a real directory, and skips entries whose source is missing or lacks a `.cursor-plugin/plugin.json`.
4. **Reports** the `copied=`/`skipped=` summary and tells the user to run **Developer: Reload Window**, then enable plugins under **Customize** and confirm hooks under **Settings → Hooks**.

Never symlinks into `~/.cursor/plugins/local/` - a symlink into an ephemeral worktree breaks the moment that worktree goes away, which is exactly the failure mode this skill exists to avoid.

## Install

The skill ships with the `marketplace` plugin:

```text
/plugin install marketplace@wild-horses
```
