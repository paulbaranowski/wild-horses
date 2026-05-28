---
name: config
description: Set up ~/.config/groundcrew/config.ts interactively. Use when the user wants to scaffold a new groundcrew config, configure crew on a fresh machine, or replace an existing config with a wizard-driven one. Triggers on phrases like "set up groundcrew", "configure crew", "first-run config", "crew setup config".
---

# config

Walk the user through creating `~/.config/groundcrew/config.ts` conversationally — the first-run replacement for "copy `crew.config.example.ts` and edit it by hand." Eight bundled scripts do the discovery and file rendering; you are the orchestrator: run them, ask the questions, build a JSON `Answers` object, and pipe it to the renderer.

The whole flow is Phases 0–7 below. Run them in order. Phase 0 runs silently up front; Phases 1, 5, and parts of 6 are conditional.

## Quick reference

All scripts live at `${CLAUDE_PLUGIN_ROOT}/scripts/`. Reference them via that env var — never hardcode `~/.claude/plugins/cache/...`.

- **`discover_existing_config.sh`** (no args) → prints the absolute path of the first existing config: `groundcrew.config.{ts,js,json,yaml,yml}` in CWD, or `config.{ts,js,json,yaml,yml}` in `$XDG_CONFIG_HOME/groundcrew/` (default `~/.config/groundcrew/`), first match wins. Empty if none. Always exit 0.
- **`load_existing.sh <config-path>`** → on success prints the loaded config as JSON on stdout (exit 0); on any failure prints a reason to stderr and exits non-zero. Non-zero = "no seeding available — use static defaults."
- **`discover_repos.sh [--workspace-dir <path>]`** → prints a JSON array `[{"owner","repo","sources":["gh"|"local",...]}, ...]`, sorted by `owner/repo`. Always exit 0.
- **`detect_installed_skills.sh`** (no args) → prints `{"superpowers": <bool>, "babysitPr": <bool>}`. Always exit 0.
- **`discover_clearance_setup.sh`** (no args) → prints `{"personalFileExists","personalFileHasClaudeHosts","envExported","daemonPid":<int>|null,"daemonAgeSeconds":<int>|null}`. Always exit 0.
- **`compose_initial_prompt.py --features <csv>`** → prints the composed prompt body on stdout. Valid keys: `superpowers`, `babysitPr`, `codeStylePointer`. Empty/absent → baseline only. Unknown key → exit 2.
- **`render_clearance_hosts.py [--target <path>] [--append]`** → writes `~/.config/clearance/personal-allow-hosts`. Default: create, or exit 1 if the file exists (refuse overwrite). `--append`: add the two Claude hosts if missing, idempotent.
- **`render_config.py --target <path>`** ← reads `Answers` JSON on STDIN → writes the rendered `config.ts` to `--target`, prints the written path. Exit 2 on validation errors.

**Config target:** Define `targetPath` once and use it everywhere:

- `targetPath` = the verbatim `existingConfigPath` returned by `discover_existing_config.sh` IF the user chose to overwrite in Phase 1; else `$HOME/.config/groundcrew/config.ts` (fresh-install default).
- `configDir` = `$(dirname "$targetPath")` — this is where `initial-prompt.md` lands (as a sibling of the config file).

## Procedure

### Phase 0 — Pre-flight (silent; no prompts)

Run these four discovery scripts via Bash (in parallel where possible) and capture their output. Do not narrate this to the user — just gather the facts.

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/discover_existing_config.sh"
"${CLAUDE_PLUGIN_ROOT}/scripts/discover_repos.sh"
"${CLAUDE_PLUGIN_ROOT}/scripts/detect_installed_skills.sh"
"${CLAUDE_PLUGIN_ROOT}/scripts/discover_clearance_setup.sh"
```

Capture: `existingConfigPath` (stdout of `discover_existing_config.sh`, possibly empty), the repo JSON array, the installed-skills JSON, and the clearance JSON. Defer `load_existing.sh` to Phase 1 — only run it if a config was found AND the user opts to overwrite. Defer re-running `discover_repos.sh --workspace-dir` to Phase 2, once you know the project dir.

### Phase 1 — Existing-config confirm (conditional)

Only if `existingConfigPath` is non-empty. Use **AskUserQuestion**: "Found an existing config at `<path>`. Overwrite it?" with options **Yes** / **No**.

- **No** → tell the user where their existing config is and stop. This is a chat session; there's no exit code.
- **Yes** → run `load_existing.sh "<existingConfigPath>"`. If it exits 0, parse the JSON into `existingConfigJson` and use its values as the defaults you offer in later phases. If it exits non-zero, print one line: "Couldn't load the existing config for seeding (this is expected if you only have a groundcrew source clone, not a global npm install) — falling back to static defaults." Then proceed with static defaults.

If `existingConfigPath` was empty, skip this phase entirely and use static defaults throughout.

### Phase 2 — Workspace

1. **Workspace kind** — **AskUserQuestion** "Workspace kind?" with three options: **auto** (Recommended) / **cmux** / **tmux**. Default = `existingConfigJson.workspace?.kind` if seeded, else `auto`. Record as `workspaceKind`. (`auto` will be omitted from the rendered config — that's intentional.)
2. **Project directory** — ask in chat (free-form, not AskUserQuestion): "Workspace project directory? [default: `~/work`]" (or the seeded value). Empty reply = keep the default. Record as `workspaceProjectDir`. This is **required and non-empty** for the renderer.
3. **Repo picker** — now that you know the project dir, re-run `discover_repos.sh --workspace-dir "<workspaceProjectDir>"` to fold any clones under it into the list. Render the merged array as a **numbered chat list**, one repo per line, with source annotations. Pre-mark any already in `existingConfigJson.workspace?.knownRepositories`.

   ```text
   1. foo/bar              (gh + local)
   2. foo/baz              (gh)              [already configured]
   3. quux/zonk            (local)
   ...
   ```

   Ask: "Pick repos by number (comma-separated, e.g. `1, 3, 7`; blank to skip)." Then a free-form follow-up: "Any repos not in the list? (owner/repo, comma-separated; blank for none)." Combine into `knownRepositories` (may be an empty array — that's valid).

   Use a numbered chat list, **not** AskUserQuestion, here: the merged gh+local list routinely runs 10–100 entries, and AskUserQuestion caps at 4 options.

### Phase 3 — Claude permissions and model commands

1. **Bypass permissions** — **AskUserQuestion** "Skip Claude tool-permission prompts (`--permission-mode bypassPermissions`)?" with **Yes** (default; recommended for unattended runs) / **No**. Record as `claudeBypassPermissions`.
2. **Custom claude command** — **AskUserQuestion** "Customize the `claude` command?" **No** (default) / **Yes**. On Yes, ask in chat for the command string; seed the default from the bypass answer (`claude --permission-mode bypassPermissions`) or the existing value. Record under `modelClaude.cmd`.
3. **Custom codex command** — **AskUserQuestion** "Customize the `codex` command?" **No** (default) / **Yes**. On Yes, ask in chat for the command string. Record under `modelCodex.cmd`.

If the user keeps the bypass default and customizes nothing, leave `modelClaude`/`modelCodex` unset — the renderer derives the claude bypass command from `claudeBypassPermissions` on its own.

### Phase 4 — Initial-prompt features (additive)

The built-in default already ships an always-on baseline (placeholders + autonomy guidance + workflow). This phase only offers additive snippets on top. Render a **numbered chat list**, pre-selecting the rows that `detect_installed_skills.sh` found:

```text
The built-in default ships placeholders + autonomy guidance + workflow (always-on; not editable here).

Additional features (pick by number, comma-separated; blank = omit prompts.initial, runtime falls back to built-in default):
  1. Invoke `superpowers` skills for non-trivial work   (detected ✓)
  2. Invoke `core:babysit-pr` after opening the PR      (detected ✓)
  3. Read `CLAUDE.md` / `AGENTS.md` before writing code
```

Annotate `(detected ✓)` on row 1 when `superpowers` is true and row 2 when `babysitPr` is true, and treat those as pre-selected (the default if the user hits enter). Map selections to feature keys: 1 → `superpowers`, 2 → `babysitPr`, 3 → `codeStylePointer`. Record the chosen keys as `promptFeatures`.

- **Empty selection** → `promptFeatures: []`. The renderer omits the `prompts.initial` block entirely and the runtime falls back to its default. Write no `initial-prompt.md`.
- **Non-empty** → defer the actual `compose_initial_prompt.py` call and the `initial-prompt.md` write to Phase 7 (so it happens alongside `config.ts`).

### Phase 5 — Clearance egress allowlist (optional, recommended)

Skip this phase if the Phase 0 clearance JSON reported `personalFileHasClaudeHosts: true` AND `envExported: true`. In that case print one line — "Clearance personal-allow-hosts already configured." — and move on.

Otherwise, **AskUserQuestion** "Set up the clearance personal-allow-hosts file? Safehouse-wrapped launches otherwise log DENY entries for `downloads.claude.ai` (claude updater) and `mcp-proxy.anthropic.com` (MCP proxy) every session." **Yes** (Recommended) / **No**.

On **Yes**:

- If `personalFileExists: false` → create it:

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_clearance_hosts.py" --target "$HOME/.config/clearance/personal-allow-hosts"
  ```

- If `personalFileExists: true` but `personalFileHasClaudeHosts: false` → print the two host lines and **AskUserQuestion** "Append these to your existing personal-allow-hosts file?" **Yes** / **No**. On Yes, run:

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_clearance_hosts.py" --target "$HOME/.config/clearance/personal-allow-hosts" --append
  ```

  On No, leave the file alone.

- If `envExported: false` → **print** (never edit any rc file) the shell-rc lines for the user to paste themselves:

  ```bash
  # If you don't already have CLEARANCE_ALLOW_HOSTS_FILES set, add both:
  export CLEARANCE_ALLOW_HOSTS_FILES="$(npm root -g)/@clipboard-health/groundcrew/clearance-allow-hosts${CLEARANCE_PERSONAL_HOSTS:+:$HOME/.config/clearance/personal-allow-hosts}"
  export CLEARANCE_PERSONAL_HOSTS=1
  ```

  Add one line explaining the `${...:+...}` toggle (the personal file is appended only when `CLEARANCE_PERSONAL_HOSTS` is set). The `$(npm root -g)` path assumes a global npm install; if you can, probe it with `npm root -g` + `test -f` and warn when the baseline file isn't there rather than silently print a path that doesn't resolve.

- If `daemonPid` is set AND `daemonAgeSeconds > 3600` → **AskUserQuestion** "A clearance daemon has been running for `<formatted age>`; it won't pick up the new env until it restarts. Kill it now? (The next `crew` run respawns it.)" **Yes** / **No**. On Yes, run `kill <daemonPid>` and confirm. On No, print a reminder. Do not offer the kill when the daemon is younger than an hour.

On **No**: skip all writes and print one line pointing at `~/.cache/clearance/clearance.log` so the user can find DENY entries later.

Track what happened (file written/appended, rc lines printed, daemon killed) for the Phase 7 summary. None of this goes into `config.ts`.

### Phase 6 — Orchestrator and logging (both optional)

1. **Session cap** — **AskUserQuestion** "Set a session-usage cap?" **No** (default) / **Yes**. On Yes, ask in chat "Session limit percentage (1–100)?" and record an integer as `sessionLimitPercentage`.
2. **Log file** — **AskUserQuestion** "Set a custom log file location?" **No** (default) / **Yes**. On Yes, ask in chat "Log file path?" and record as `loggingFile`.

### Phase 7 — Render, write, and exit

1. If Phase 4 picked features, compose the prompt and write it next to the config:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/compose_initial_prompt.py" --features superpowers,babysitPr > "${configDir}/initial-prompt.md"
   ```

   (Substitute the actual comma-separated keys.)

2. Build the `Answers` JSON object from everything collected. Omit keys the user didn't set rather than sending nulls. Example:

   ```jsonc
   {
     "workspaceKind": "auto",
     "workspaceProjectDir": "~/work",
     "knownRepositories": ["foo/bar", "foo/baz"],
     "promptFeatures": ["superpowers"],
     "claudeBypassPermissions": true,
     "sessionLimitPercentage": 85,
     "loggingFile": "~/logs/groundcrew.log",
   }
   ```

3. Pipe it to the renderer in one Bash call via a quoted heredoc (so the shell passes the JSON byte-verbatim — no `$VAR` expansion). Use `$targetPath` as defined in the Quick Reference (the discovered path on overwrite, else `$HOME/.config/groundcrew/config.ts`):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_config.py" --target "$targetPath" <<'EOF'
   {"workspaceProjectDir":"~/work","knownRepositories":["foo/bar"],"promptFeatures":[],"claudeBypassPermissions":true}
   EOF
   ```

   The renderer prints the written path on stdout. Exit 2 means a validation error — read its stderr, fix the `Answers` object, and re-run.

4. Print the absolute path(s) written (`config.ts`, plus `initial-prompt.md` if Phase 4 picked features) and a numbered Next-Steps block. Surface the Phase 5 rc lines again here if they were printed, so the user has one artifact to act on:

   ```text
   Next steps:
     1. Run `crew setup repos` to clone your known repositories into the workspace.
     2. In Linear: Settings → Integrations → GitHub, connect your workspace
        (required for crew to map Linear tickets to GitHub PRs).
     3. (If clearance rc lines were printed) Paste the exports above into your
        shell rc, then start a new shell or `source` it so future `crew` runs
        spawn clearance with the right env.
     4. Run `crew doctor` to verify the full setup.
   ```

## Common mistakes

- **Don't use AskUserQuestion for the repo picker or the Phase 4 feature picker.** Both are multi-select over lists that can exceed AskUserQuestion's 4-option cap. Always render them as numbered chat lists and parse the comma-separated reply.
- **Don't hardcode `~/.claude/plugins/cache/...` paths to the scripts.** Always invoke them through `${CLAUDE_PLUGIN_ROOT}/scripts/<name>` — that env var resolves to the installed plugin dir at runtime and survives version bumps.
- **Don't edit the user's shell rc files in Phase 5.** Print the `export` lines for the user to paste. Auto-appending to `~/.zshrc` / `~/.bash_profile` breaks dotfile managers (chezmoi, yadm, stow) that would clobber or duplicate the entry.
- **Don't offer to kill the clearance daemon unless `daemonAgeSeconds > 3600`.** A fresh daemon already has current config; killing it is pointless churn.
- **Don't treat a non-zero `load_existing.sh` exit as fatal.** It means "no seeding available" — print the one-line fallback note and continue with static defaults. The user can always re-enter values.
- **Don't run `render_clearance_hosts.py` without `--append` against a file that already exists.** Create mode exits 1 (refuses overwrite). When `personalFileExists: true`, only the `--append` path is safe.
- **Don't send `null` for unset optional keys in the `Answers` object.** Omit them entirely — the renderer treats absent keys as "use the shipped default," but a `null` value can trip type validation and exit 2.
- **Don't forget that `workspaceProjectDir` and `knownRepositories` are required.** The renderer exits 2 without them. `knownRepositories` may be an empty array, but it must be present.
- **Don't write `initial-prompt.md` when Phase 4 selected nothing.** An empty `promptFeatures` means the renderer omits the `prompts.initial` block; a stray `initial-prompt.md` with no reference to it is dead weight.
- **Don't pass the default path to `render_config.py --target` when overwriting a discovered config.** If Phase 1 found an existing config at a non-default location (e.g. `${PWD}/groundcrew.config.ts`), using `$HOME/.config/groundcrew/config.ts` as the target writes a stray file and leaves the real config untouched. Always use `$targetPath` (the verbatim discovered path on overwrite, else the fresh-install default).

## Notes

- **Seeding fallback is expected on source-only installs.** `load_existing.sh` shells out to groundcrew's `loadConfig()`, which needs `@clipboard-health/groundcrew` installed via npm. Users who only have a source clone (no global install) hit the fallback — that's normal, not a bug. Say so in the warning text so they don't think the wizard is broken.
- **`workspaceKind: "auto"` is not emitted.** The renderer drops it (the runtime default is `auto`), so an `auto` answer produces a config with no `workspaceKind` key. Only `cmux` / `tmux` get written.
- **Phase 5 never touches `config.ts`.** Clearance setup is a separate, network-egress concern. It only affects the Phase 7 summary text, not the rendered config.
- **The `clearanceSetup` key in `Answers` is ignored by the renderer.** If you include it for your own bookkeeping, that's harmless — `render_config.py` ignores any key it doesn't recognize, so it never reaches `config.ts`.
- **One Bash call per render.** Pipe the `Answers` JSON via a quoted heredoc rather than writing a temp file and reading it back; it's one auto-approvable invocation instead of two.
