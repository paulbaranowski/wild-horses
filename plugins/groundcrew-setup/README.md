# groundcrew-setup

An interactive first-run wizard for `@clipboard-health/groundcrew` that scaffolds `~/.config/groundcrew/config.ts` conversationally, as a Claude Code skill.

## What it does

Replaces the "copy `crew.config.example.ts` and edit by hand" friction. The `config` skill walks you through workspace setup, repo discovery (GitHub via `gh` + local clone scan), Claude/model command config, initial-prompt customization, and optional `@clipboard-health/clearance` egress-allowlist setup. It seeds defaults from any existing config it finds, so re-running on an already-configured machine pre-fills your answers.

## Install

```text
/plugin marketplace add paulbaranowski/wild-horses
/plugin install groundcrew-setup@wild-horses
```

## Usage

Invoke with `/groundcrew-setup:config`, or use a natural-language phrase: "set up groundcrew", "configure crew", "first-run config".

The wizard runs through seven interactive phases. Before asking anything, it silently runs its discovery scripts (existing-config scan, repo discovery, installed-skill detection, clearance-setup probe).

1. **Existing-config check** — detects any existing config and asks before overwriting; seeds defaults from it if you say yes.
2. **Workspace** — workspace kind (`auto` / `cmux` / `tmux`), project directory, and a repo picker built from GitHub (`gh`) + local clone discovery.
3. **Claude permissions and model commands** — bypass-permissions flag, and optional custom `claude`/`codex` command strings.
4. **Initial-prompt features** — additive snippets on top of the always-on baseline: `superpowers` skill invocation, `core:babysit-pr` after PRs, code-style pointer. Pre-selects features it detects as already installed.
5. **Clearance egress allowlist** — optionally writes/appends `~/.config/clearance/personal-allow-hosts` and prints the shell-rc `export` lines you need to paste.
6. **Orchestrator and logging** — optional session-usage cap and custom log-file path.
7. **Render and write** — pipes the collected answers to `render_config.py`, writes `config.ts` (and `initial-prompt.md` if features were chosen), and prints next steps.

## What it produces

- `~/.config/groundcrew/config.ts` — a valid groundcrew config ready for `crew doctor`.
- `initial-prompt.md` — written alongside `config.ts` (in the same directory), only if you pick initial-prompt features.
- `~/.config/clearance/personal-allow-hosts` — only when Phase 5 clearance setup runs.

After the wizard finishes, `crew doctor` should exit 0.

## Bundled scripts

| Script                        | Purpose                                                                                                                                  |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `discover_existing_config.sh` | Find an existing groundcrew config (`groundcrew.config.*` in CWD, or `config.*` in `~/.config/groundcrew/`); prints its path or nothing. |
| `load_existing.sh`            | Load an existing config via groundcrew's `loadConfig()` and print it as JSON for seeding.                                                |
| `discover_repos.sh`           | Discover repos via `gh` and local clone scan; prints a JSON array of `{owner, repo, sources}`.                                           |
| `detect_installed_skills.sh`  | Detect whether `superpowers` and `core:babysit-pr` skills are installed; prints JSON booleans.                                           |
| `discover_clearance_setup.sh` | Inspect the clearance egress setup (personal file, env vars, daemon); prints a JSON status blob.                                         |
| `compose_initial_prompt.py`   | Compose the `initial-prompt.md` body from a comma-separated list of feature keys.                                                        |
| `render_clearance_hosts.py`   | Write or append the two Claude hosts to `~/.config/clearance/personal-allow-hosts`.                                                      |
| `render_config.py`            | Read an `Answers` JSON object on stdin and render it to a `config.ts` file at `--target`.                                                |

## Troubleshooting

**Seeding fallback is expected on source-only installs.** `load_existing.sh` calls groundcrew's `loadConfig()`, which requires `@clipboard-health/groundcrew` to be npm-installed (a global install, not just a source clone). If you only have a source clone the wizard prints one line and falls back to static defaults — this is normal. Re-enter your values and the output will be identical.

**Clearance rc lines: paste, don't assume.** The wizard prints the `export` lines for `CLEARANCE_ALLOW_HOSTS_FILES` and `CLEARANCE_PERSONAL_HOSTS` but never edits your shell rc files. Paste them into `~/.zshrc` (or equivalent) and start a new shell — or `source` the rc — before running `crew` again.

**No in-place edits.** The wizard never silently patches an existing config. It either overwrites the whole file (with confirmation) or stops.
