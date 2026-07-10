---
name: install-cursor-plugins
description: Copy every plugin from a Cursor marketplace catalog into ~/.cursor/plugins/local as real files (no symlinks). Use when installing or refreshing wild-horses (or any dual-manifest marketplace) for Cursor, when team marketplace refresh is unavailable, or when local plugin installs must not point at a checkout via symlink.
user-invocable: true
disable-model-invocation: true
argument-hint: "[marketplace-root]"
---

# Install Cursor Plugins (local copy)

Install all plugins listed in a marketplace's `.cursor-plugin/marketplace.json` into Cursor's local plugin directory as **real file trees**. Never symlink.

**Arguments:** `$ARGUMENTS` — optional path to the marketplace root (directory that contains `.cursor-plugin/marketplace.json`). Defaults to the current workspace root when that file exists there.

## Why

Cursor team-marketplace refresh lives on the web dashboard, not in the IDE, and `cursor-agent` has no marketplace update command. Copying catalog plugins into `~/.cursor/plugins/local/<name>/` is the reliable local install path. Symlinks into worktrees break when the worktree goes away.

## Workflow

1. Resolve the marketplace root:
   - If `$ARGUMENTS` is a non-empty path, use it.
   - Else if `./.cursor-plugin/marketplace.json` exists, use `.`.
   - Else ask the user for the marketplace root path.
2. Confirm the manifest exists: `<root>/.cursor-plugin/marketplace.json`.
3. Run the bundled installer (one Bash call — do not hand-roll `cp`/`ln`):

```bash
bash "<skill-or-plugin-root>/skills/install-cursor-plugins/scripts/install-cursor-plugins.sh" "<marketplace-root>"
```

When this skill is running from an installed `marketplace` plugin, prefer:

```bash
bash "${CURSOR_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/skills/install-cursor-plugins/scripts/install-cursor-plugins.sh" "<marketplace-root>"
```

If neither plugin-root env is set (dev checkout), resolve the script relative to this skill's directory.

1. Report the script's `copied=` / `skipped=` summary to the user.
2. Tell the user to run **Developer: Reload Window**, then enable plugins under **Customize** and confirm hooks under **Settings → Hooks**.

## Rules

- **Don't create symlinks** into `~/.cursor/plugins/local/` (or anywhere else for this install).
- **Don't** point local installs at ephemeral worktrees; copy from a durable marketplace checkout (or the repo root you intend to keep).
- **Don't** invent a second copy destination — default is `~/.cursor/plugins/local` (override only if the user explicitly asks).
- The script skips entries whose source dir is missing or that lack `.cursor-plugin/plugin.json`, and replaces an existing destination symlink with a real tree.

## Verify

After a successful run:

```bash
ls -la ~/.cursor/plugins/local/
# each entry should be a directory (drwx...), not a symlink (l...)
test -f ~/.cursor/plugins/local/pr-status-hook/.cursor-plugin/plugin.json
test -f ~/.cursor/plugins/local/pr-status-hook/hooks/cursor-hooks.json
```
