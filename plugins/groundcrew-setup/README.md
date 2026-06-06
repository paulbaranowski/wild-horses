# groundcrew-setup

An interactive setup wizard for `@clipboard-health/groundcrew` that installs the npm package + the `eugene1g/agent-safehouse` Homebrew formula, writes the clearance allowlist + env sidecar, writes the safehouse env sidecar, scaffolds `~/.config/groundcrew/config.ts`, and finishes by running `crew doctor` — as a single Claude Code command.

## What it does

Replaces the "install groundcrew, copy `crew.config.example.ts`, edit by hand, paste exports into `~/.zshrc`, install safehouse, configure clearance" first-run friction. The `/groundcrew-setup:setup` command walks you through every prerequisite conversationally and atomically writes every file. The user answers Yes/No to a small handful of questions (install-if-missing, overwrite-existing, set-session-cap, etc.) and types one workspace dir + one repo-picker reply. Everything else is mechanical: scripts that probe state, install missing prereqs, and render config files into XDG dirs. Shell rc files are never edited.

It seeds defaults from any existing config it finds via groundcrew's own `loadConfig()`, so re-running on an already-configured machine pre-fills your answers.

## Install

```text
/plugin marketplace add paulbaranowski/wild-horses
/plugin install groundcrew-setup@wild-horses
```

## Usage

One command, with an optional scope argument:

| Invocation                          | What runs                                                                                                                                                                         |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/groundcrew-setup:setup`           | The full ten-phase wizard. Use this on a fresh machine.                                                                                                                           |
| `/groundcrew-setup:setup clearance` | Only the clearance bits: probe → write `personal-allow-hosts` → write `env.sh` sidecar → daemon-stale prompt. Use this when groundcrew is already configured but clearance isn't. |
| `/groundcrew-setup:setup safehouse` | Only the safehouse bits: probe → install if missing → write `env.sh` sidecar. Use this when groundcrew + clearance are already configured but safehouse isn't.                    |

The two scopes are slices of the full wizard — their mechanical steps are single-sourced from Phases 6 (clearance) and 7 (safehouse), so there's no second copy to drift.

## What the full wizard does

Ten phases (0–9), matching `commands/setup.md`. Phase 0 silently runs seven discovery / install-check scripts in parallel before any prompt is shown.

- **Phase 0 — Pre-flight discovery** — existing-config scan, repo discovery, installed-skill detection, clearance probe, safehouse probe, groundcrew install-check, safehouse install-check.
- **Phase 1 — Install prerequisites** — `npm install -g @clipboard-health/groundcrew` (brings clearance along) and `brew install eugene1g/safehouse/agent-safehouse`, one Yes/No question each on missing.
- **Phase 2 — Existing-config check** — detects any existing config and asks before overwriting; seeds defaults from it if you say yes.
- **Phase 3 — Workspace** — workspace kind (`auto` / `cmux` / `tmux`), project directory, and a repo picker built from GitHub (`gh`) + local clone discovery.
- **Phase 4 — Claude permissions and model commands** — bypass-permissions flag, and optional custom `claude` / `codex` command strings.
- **Phase 5 — Initial-prompt features** — additive snippets on top of the always-on baseline: `superpowers` skill invocation, `core:babysit-pr` after PRs, code-style pointer. Pre-selects features it detects as already installed.
- **Phase 6 — Clearance egress allowlist** — writes/appends `~/.config/clearance/personal-allow-hosts`, then writes `~/.config/clearance/env.sh` sidecar with smart-merge against your existing rc-file exports.
- **Phase 7 — Safehouse env sidecar** — writes `~/.config/agent-safehouse/env.sh` with `SAFEHOUSE_APPEND_PROFILE` + `safe()` / `safe-claude()` wrapper functions, plus an empty `local-overrides.sb` stub.
- **Phase 8 — Orchestrator and logging** — optional session-usage cap and custom log-file path.
- **Phase 9 — Render config + crew doctor** — pipes the collected answers to `render_config.py`, writes `config.ts` (and `initial-prompt.md` if features were chosen), invokes `crew doctor`, and prints next steps including the one-line snippet you add to your rc to source both sidecars.

## What the wizard produces

Atomically written, idempotent, never owned by the wizard at runtime (you can edit any of these and the next wizard run will respect what's there):

- `~/.config/groundcrew/config.ts` — a functional groundcrew config (not a template-with-comments).
- `<configDir>/initial-prompt.md` — only when initial-prompt features are picked; lands as a sibling of `config.ts`.
- `~/.config/clearance/personal-allow-hosts` — when clearance setup runs.
- `~/.config/clearance/env.sh` — the clearance env sidecar.
- `~/.config/agent-safehouse/env.sh` — the safehouse env sidecar (exports + `safe()` / `safe-claude()` functions).
- `~/.config/agent-safehouse/local-overrides.sb` — empty stub for machine-local sandbox rules; see [agent-safehouse.dev/docs](https://agent-safehouse.dev/docs).

After the wizard finishes, add ONE line to your shell rc:

```bash
for f in ~/.config/clearance/env.sh ~/.config/agent-safehouse/env.sh; do
  [ -f "$f" ] && . "$f"
done
```

Start a new shell or `source` your rc, then `crew doctor` should exit 0.

## Bundled scripts

| Script                        | Purpose                                                                                                                                                      |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `discover_existing_config.py` | Find an existing groundcrew config (`groundcrew.config.*` in CWD, or `config.*` in `~/.config/groundcrew/`); prints its path or nothing.                     |
| `load_existing.py`            | Load an existing config via groundcrew's `loadConfig()` and print it as JSON for seeding.                                                                    |
| `discover_repos.py`           | Discover repos via `gh` and local clone scan; prints a JSON array of `{owner, repo, sources}`.                                                               |
| `detect_installed_skills.py`  | Detect whether `superpowers` and `core:babysit-pr` skills are installed; prints JSON booleans.                                                               |
| `discover_clearance_setup.py` | Inspect the clearance egress setup (personal file, env vars, daemon); prints a JSON status blob.                                                             |
| `discover_safehouse_setup.py` | Inspect the safehouse setup (binary on PATH, brew formula installed, env exported, sidecar present); prints a JSON status blob.                              |
| `install_groundcrew.py`       | Probe + install `@clipboard-health/groundcrew` via npm. Idempotent; emits JSON `{action, version, details}`. `--check` probes only.                          |
| `install_safehouse.py`        | Probe + install `eugene1g/agent-safehouse` via Homebrew (auto-taps `eugene1g/safehouse` on first use). Same JSON shape as `install_groundcrew.py`.           |
| `compose_initial_prompt.py`   | Compose the `initial-prompt.md` body from a comma-separated list of feature keys.                                                                            |
| `render_clearance_hosts.py`   | Write or append the two Claude hosts to `~/.config/clearance/personal-allow-hosts`.                                                                          |
| `render_clearance_env.py`     | Atomically write `~/.config/clearance/env.sh` (exports + smart-merge against existing rc-file exports).                                                      |
| `render_safehouse_env.py`     | Atomically write `~/.config/agent-safehouse/env.sh` (export + `safe()` / `safe-claude()` functions + smart-merge). Also creates `local-overrides.sb` stub.   |
| `render_config.py`            | Read an `Answers` JSON object on stdin and render it to a `config.ts` file at `--target`. Atomic write; conditional emission (omits keys at default values). |

## Troubleshooting

**Seeding fallback is expected on source-only installs.** `load_existing.py` calls groundcrew's `loadConfig()`, which requires `@clipboard-health/groundcrew` to be npm-installed globally (a source clone alone won't work for `import('...')`). If you only have a source clone the wizard prints one line and falls back to static defaults — this is normal. Re-enter your values and the output will be identical.

**Sidecars + one rc line.** The wizard never edits `~/.zshrc` / `~/.bashrc`. It writes one sidecar per component (`~/.config/clearance/env.sh`, `~/.config/agent-safehouse/env.sh`) and prints one snippet for you to add to your rc (a for-loop that sources whichever sidecars exist). This is safe under chezmoi / yadm / stow.

**Smart-merge against existing rc exports.** If you already have `CLEARANCE_ALLOW_HOSTS_FILES` or `SAFEHOUSE_APPEND_PROFILE` exported in `~/.zshrc:169` (or wherever), the sidecar emits a commented-out copy of that export with a `# Already exported in <rc>:<line>` note — your rc keeps owning the var, no duplicate exports. Same for `safe()` / `safe-claude()` function definitions.

**No in-place edits.** The wizard never silently patches an existing config. It either overwrites the whole `config.ts` (with confirmation) or stops.

**`crew doctor` non-zero is not fatal.** The wizard surfaces doctor's output verbatim but does not abort — you may want to inspect the rendered files even if doctor flags something unrelated (e.g. `GROUNDCREW_LINEAR_API_KEY` not set yet).
