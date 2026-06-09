# linting-hooks

PostToolUse hooks that lint Markdown and Python files immediately after Claude edits them. Hook registration happens automatically on plugin enable; one command (`/linting-hooks:install`) handles the per-machine software each hook depends on.

Install:

```text
/plugin install linting-hooks@wild-horses
```

## Bundled hooks

| Hook                 | When it fires                                     | What it does                                                                               | Dependencies                          |
| -------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------- |
| `markdown-combo-fix` | PostToolUse on `Edit\|Write\|MultiEdit` for `.md` | Runs `prettier --write` then `markdownlint-cli2 --fix` on the edited file. Non-blocking.   | `jq`, `prettier`, `markdownlint-cli2` |
| `pyright-post-edit`  | PostToolUse on `Edit\|Write\|MultiEdit` for `.py` | Runs `pyright <file>` and prints findings to stderr. Non-blocking — never blocks the edit. | `jq`, `pyright`                       |

Each hook script self-guards: if its required tools aren't on `PATH`, it exits 0 silently. So a fresh install is inert until you opt in to specific software.

## Command

### `/linting-hooks:install`

Interactive: pick which hooks should be functional on this machine, then install only the missing tools (with consent before each `brew install` / `npm install -g`).

```text
/linting-hooks:install
```

The command will:

1. Ask which hooks to enable (multi-select from the bundled list).
2. Probe `PATH` for each dep with `command -v`.
3. Group missing tools by package manager and propose one install command per manager. On macOS, `brew install jq` and `npm install -g <missing>` are offered. On Linux, the manual install lines are printed instead — no auto-run.
4. Re-probe after install and report `✓ ready` / `✗ missing: <tool>` per hook.

Idempotent — running it again when everything is installed reports all-green and exits.

## Guardrails

- **No edits to the user's `settings.json`.** Hook registration is the plugin system's job, via `hooks/hooks.json` merged on plugin enable.
- **One install command, one consent prompt.** Nothing installs without explicit per-command approval.
- **No blocking.** Both hooks are non-blocking — a failing prettier or pyright run will not stop an edit. Findings go to stderr for visibility.

## Why it's a separate plugin

These hooks were originally bundled with the `refactor` plugin but were split out so the refactor analysis commands don't carry a dependency on `prettier` / `markdownlint` / `pyright` being installed. Install this plugin only if you want the post-edit linting feedback.
