---
description: Install the software the linting-hooks plugin's PostToolUse hooks depend on (prettier, markdownlint-cli2, pyright, jq). Hooks are registered automatically on plugin enable; this command only manages the per-machine software each hook needs to actually do work.
---

# Linting Hooks — Install

Set up the **PostToolUse hooks** bundled with this plugin. Hook registration is automatic (via `hooks/hooks.json` merged on plugin enable). Scripts silently no-op until their dependencies are installed — so a hook is inert on a fresh install and becomes active once the right software is on `PATH`.

This command is the opt-in surface for **which hooks the user wants functional** and **what software to install** to make that so.

---

## Bundled hooks

| Hook                 | When it fires                                     | What it does                                                                               | Dependencies                          |
| -------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------- |
| `markdown-combo-fix` | PostToolUse on `Edit\|Write\|MultiEdit` for `.md` | Runs `prettier --write` then `markdownlint-cli2 --fix` on the edited file. Non-blocking.   | `jq`, `prettier`, `markdownlint-cli2` |
| `pyright-post-edit`  | PostToolUse on `Edit\|Write\|MultiEdit` for `.py` | Runs `pyright <file>` and prints findings to stderr. Non-blocking — never blocks the edit. | `jq`, `pyright`                       |

Each script self-guards: if required tools are missing, it exits 0 without output.

---

## Phase 1 — Present the options and ask

Show the table above to the user. Then use `AskUserQuestion` with `multiSelect: true`:

- **Question:** "Which linting hooks should be functional on this machine? (Hooks are already registered; this picks which ones get their software installed.)"
- **Header:** "Hooks"
- **Options** (one per bundled hook above) — label is the hook name, description summarises its behavior + deps.

If the user picks nothing, stop cleanly: "No hooks selected — nothing to install. The hooks stay registered but dormant."

---

## Phase 2 — Detect missing tools

For each selected hook, probe deps with `command -v`:

```bash
command -v jq >/dev/null 2>&1
command -v prettier >/dev/null 2>&1
command -v markdownlint-cli2 >/dev/null 2>&1
command -v pyright >/dev/null 2>&1
```

Build a set of **missing tools across all selected hooks**. If the set is empty, skip Phase 3 and go straight to Phase 4 with all-ready status.

---

## Phase 3 — Propose and run installs

Group missing tools by package manager and propose **one install command per manager**. Platform detection: run `uname -s` (`Darwin` = macOS, `Linux` = Linux, else unknown).

### macOS (Darwin)

- If `jq` is missing: propose `brew install jq`.
- If any of `prettier`, `markdownlint-cli2`, `pyright` are missing: propose `npm install -g <missing-packages…>` as a single command.

### Linux / other

Don't attempt auto-install. Print the manual install lines and stop:

```
Missing: jq                    → install via your package manager (apt install jq / dnf install jq / etc.)
Missing: prettier, …           → npm install -g prettier markdownlint-cli2 pyright
```

### Consent gate

Before running **any** install command, show the exact command to the user and ask for confirmation (via `AskUserQuestion` with yes/no options). Never install without consent. If the user declines, skip that command and continue — report its deps as still missing in Phase 4.

---

## Phase 4 — Re-check and report

After any installs, re-probe each dependency with `command -v` and print a status line per selected hook:

```
✓ markdown-combo-fix  ready
✗ pyright-post-edit   missing: pyright
```

Then remind the user:

- "Hooks are registered automatically — no edits to `settings.json` are needed."
- "To disable a hook without uninstalling its deps, edit `hooks/hooks.json` in the plugin or disable the plugin via `/plugin`."
- "If you want the hooks functional on another machine, run `/linting-hooks:install` again there."

Stop. Do not run any hooks directly to "test" them — the user will see them fire on their next edit.

---

## Guardrails

1. **Never install without explicit user consent** per command. One prompt per install command.
2. **Never edit the user's `settings.json`.** Registration is the plugin system's job.
3. **Never touch the hook scripts themselves** from this command. If a hook needs a behavior change, edit its script file directly (and bump the plugin version).
4. **Platform fallback:** on non-macOS systems, print manual install lines instead of running them.
5. **Idempotent:** running `/linting-hooks:install` a second time should do nothing when everything is already installed — report all-green and exit.
