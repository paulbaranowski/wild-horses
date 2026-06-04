---
description: Set up eugene1g/agent-safehouse on macOS — the sandbox-exec wrapper groundcrew uses for unattended runs. Installs the Homebrew formula if missing, then writes a ~/.config/agent-safehouse/env.sh sidecar exporting SAFEHOUSE_APPEND_PROFILE and defining safe()/safe-claude() wrapper functions.
argument-hint: (no arguments)
allowed-tools: Bash, AskUserQuestion
---

# safehouse

Set up `eugene1g/agent-safehouse` so groundcrew can launch sandboxed agents on macOS. Two steps — install the Homebrew formula if missing, then atomically write a sidecar with the recommended env var and wrapper functions. The user answers Yes/No to one question (install? if missing); everything else is mechanical.

agent-safehouse is a third-party Apache-2.0 macOS sandbox-exec wrapper hosted at `https://github.com/eugene1g/agent-safehouse`. It is NOT a `@clipboard-health/*` npm package — it's a Homebrew formula in the `eugene1g/safehouse` tap. Groundcrew's own README ([line 40](https://github.com/clipboard-health/groundcrew#prerequisites)) points users at `https://agent-safehouse.dev/` as the canonical macOS sandbox.

## Quick reference

All scripts live at `${CLAUDE_PLUGIN_ROOT}/scripts/`. Reference them via that env var — never hardcode `~/.claude/plugins/cache/...`.

- **`discover_safehouse_setup.py`** (no args) → JSON `{binaryAvailable, binaryPath, brewFormulaInstalled, envExported, sidecarPresent, sidecarHasFunctions}`. Always exit 0.
- **`install_safehouse.py [--check]`** → detect via `brew list --versions agent-safehouse`; install via `brew install eugene1g/safehouse/agent-safehouse` if missing. Emits JSON `{action, version, details}`. The tap is auto-added by `brew install` on first use — no separate `brew tap` step. Exit 1 if `brew` is not on PATH; propagates brew's exit code on install failure.
- **`render_safehouse_env.py [--target <path>] [--overrides-file <path>] [--no-overrides-stub]`** → atomically write `~/.config/agent-safehouse/env.sh` with `SAFEHOUSE_APPEND_PROFILE` and the `safe()` / `safe-claude()` shell functions. Also writes an empty `local-overrides.sb` stub if missing (so the wrapper's `--append-profile=...` flag points at a real file from day one). Smart-merges against the user's shell rc: any var/function already defined there is emitted commented-out in the sidecar with a `# Already defined in <rc>:<line>` note.

## Procedure

### Step 1 — Probe (silent)

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/discover_safehouse_setup.py"` and capture the JSON. Don't narrate this to the user.

### Step 2 — Install if missing

Only if `brewFormulaInstalled: false`:

1. **AskUserQuestion** "Install eugene1g/agent-safehouse via Homebrew? (it's the macOS sandbox-exec wrapper groundcrew uses for unattended runs)" with **Yes** (Recommended) / **No**.
2. On **Yes**: run:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install_safehouse.py"
   ```

   Parse the JSON. If `action: "failed"`, surface `details` and stop — don't proceed to Step 3 without a working binary.

3. On **No**: print one line — "Skipping safehouse install. Run `brew install eugene1g/safehouse/agent-safehouse` later, then re-run this command." — and stop.

If `brewFormulaInstalled: true` already, skip this step entirely.

### Step 3 — Env sidecar (`env.sh`)

Always run, unconditionally — it's idempotent and the smart-merge handles existing rc-file definitions:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_safehouse_env.py"
```

Parse the JSON output. If `rcConflicts` is non-empty, surface one line per conflict to the user so they know which items stayed under the rc-file's ownership rather than being moved to the sidecar.

If `overridesStub` is a path (not `null`), that means the wizard created an empty `local-overrides.sb` stub — mention it in the summary so the user knows where to add machine-local sandbox rules later (see [agent-safehouse.dev/docs](https://agent-safehouse.dev/docs)).

### Step 4 — Summary

Print the paths written and the one-line rc snippet:

```text
Safehouse configured. Files written:
  ~/.config/agent-safehouse/env.sh
  ~/.config/agent-safehouse/local-overrides.sb  (empty stub; add machine-local rules here)

Add this one line to your shell rc (~/.zshrc or ~/.bashrc), then start
a new shell:

  for f in ~/.config/clearance/env.sh ~/.config/agent-safehouse/env.sh; do
    [ -f "$f" ] && . "$f"
  done
```

If Step 3 reported conflicts, append: "(N of the sidecar's items were left commented because they're already in your rc — see the sidecar's inline notes.)"

If safehouse was actually installed (not already-installed), append "Installed agent-safehouse `<version>` via Homebrew."

## Common mistakes

- **Don't run `brew tap eugene1g/safehouse` separately before installing.** `brew install eugene1g/safehouse/agent-safehouse` auto-adds the tap on first use — adding it explicitly first is redundant and slower.
- **Don't edit the user's shell rc files.** The sidecar exists precisely so the wizard never has to touch `~/.zshrc` — that protects users on chezmoi / yadm / stow.
- **Don't proceed to Step 3 if `install_safehouse.py` returns `failed`.** Sourcing the sidecar's `safe()` wrapper without `safehouse` on PATH would just error on every shell start.
- **Don't write the sidecar's `safe-claude()` function and expect strict POSIX `/bin/sh` to source it.** Hyphenated function names are valid in zsh and bash (where the sidecar is actually sourced) but not strict POSIX — the sidecar is meant for shell-rc consumption, not arbitrary `/bin/sh` scripts.
