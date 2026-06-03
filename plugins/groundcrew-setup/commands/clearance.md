---
description: Set up @clipboard-health/clearance so unattended Claude/groundcrew sessions stop logging DENY entries for the Claude updater and MCP proxy hosts. Detects existing setup, writes ~/.config/clearance/personal-allow-hosts if needed, and writes a ~/.config/clearance/env.sh sidecar the user sources from their shell rc.
argument-hint: (no arguments)
allowed-tools: Bash, AskUserQuestion
---

# clearance

Set up `@clipboard-health/clearance` so safehouse-wrapped launches stop logging DENY entries for `downloads.claude.ai` (Claude updater) and `mcp-proxy.anthropic.com` (MCP proxy) every session. Three steps total — probe, then two atomic file renders. The user answers Yes/No to one question; everything else is mechanical.

Clearance ships transitively with `@clipboard-health/groundcrew` (it's a dependency), so a user with groundcrew installed already has the runtime — only the config files need writing.

## Quick reference

All scripts live at `${CLAUDE_PLUGIN_ROOT}/scripts/`. Reference them via that env var — never hardcode `~/.claude/plugins/cache/...`.

- **`discover_clearance_setup.py`** (no args) → JSON `{personalFileExists, personalFileHasClaudeHosts, envExported, daemonPid, daemonAgeSeconds}`. Always exit 0.
- **`render_clearance_hosts.py [--target <path>] [--append]`** → create `~/.config/clearance/personal-allow-hosts` (refuses overwrite) or append the two Claude hosts idempotently with `--append`.
- **`render_clearance_env.py [--target <path>]`** → atomically write `~/.config/clearance/env.sh` exporting `CLEARANCE_PERSONAL_HOSTS=1` and `CLEARANCE_ALLOW_HOSTS_FILES`. Smart-merges against the user's shell rc: any of these vars already exported there is commented out in the sidecar with a `# Already exported in <rc>:<line>` note.

## Procedure

### Step 1 — Probe (silent)

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/discover_clearance_setup.py"` and capture the JSON. Don't narrate this to the user.

If both `personalFileHasClaudeHosts: true` AND `envExported: true`, clearance is already fully configured. Print one line — "Clearance is already set up." — and stop.

### Step 2 — Allowlist file (`personal-allow-hosts`)

Three cases based on Step 1's probe:

- `personalFileExists: false` → no question; just run:

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_clearance_hosts.py"
  ```

  This creates `~/.config/clearance/personal-allow-hosts` with the two Claude hosts.

- `personalFileExists: true` AND `personalFileHasClaudeHosts: false` → **AskUserQuestion** "Your existing `~/.config/clearance/personal-allow-hosts` is missing `downloads.claude.ai` and `mcp-proxy.anthropic.com`. Append them?" with options **Yes** (Recommended) / **No**. On Yes:

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_clearance_hosts.py" --append
  ```

- `personalFileExists: true` AND `personalFileHasClaudeHosts: true` → skip; nothing to do.

### Step 3 — Env sidecar (`env.sh`)

Always run, unconditionally — it's idempotent and the smart-merge handles existing rc-file exports:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_clearance_env.py"
```

Parse the JSON output. If `rcConflicts` is non-empty, surface one line per conflict to the user so they know which vars stayed under the rc-file's ownership rather than being moved to the sidecar.

### Step 4 — Daemon-stale check

If Step 1's probe reported `daemonPid` is set AND `daemonAgeSeconds > 3600` (one hour), the running daemon won't see the new env until it restarts. **AskUserQuestion** "A clearance daemon has been running for `<formatted age>`; it won't pick up the new env until it restarts. Kill it now? (The next `crew` run respawns it.)" with **Yes** / **No**. On Yes, run `kill <daemonPid>` and confirm. On No, print a short reminder.

Skip this whole step if the daemon is younger than an hour — killing a fresh daemon is pointless churn.

### Step 5 — Summary

Print the paths written and the one-line rc snippet:

```text
Clearance configured. Files written:
  ~/.config/clearance/personal-allow-hosts
  ~/.config/clearance/env.sh

Add this one line to your shell rc (~/.zshrc or ~/.bashrc), then start
a new shell:

  for f in ~/.config/clearance/env.sh ~/.config/agent-safehouse/env.sh; do
    [ -f "$f" ] && . "$f"
  done
```

If Step 3 reported conflicts, append: "(N of the sidecar's exports were left commented because they're already in your rc — see the sidecar's inline notes.)"

## Common mistakes

- **Don't run `render_clearance_hosts.py` without `--append` against a file that already exists.** Create mode exits 1 (refuses overwrite). Use the probe output to choose between create and append.
- **Don't edit the user's shell rc files.** The sidecar exists precisely so the wizard never has to touch `~/.zshrc` — that protects users on chezmoi / yadm / stow.
- **Don't offer the daemon kill when `daemonAgeSeconds <= 3600`.** A fresh daemon already has current config; restarting it is pointless churn.
- **Don't print "configured" if the env sidecar render returned non-zero.** Surface the error JSON and stop.
