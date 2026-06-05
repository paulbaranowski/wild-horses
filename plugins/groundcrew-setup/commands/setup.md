---
description: Set up the full groundcrew stack end-to-end. Installs @clipboard-health/groundcrew via npm if missing, installs eugene1g/agent-safehouse via Homebrew if missing, scaffolds ~/.config/groundcrew/config.ts conversationally, writes the clearance allowlist + env sidecar, writes the safehouse env sidecar, and runs crew doctor at the end. Pass `clearance` or `safehouse` as the argument to (re)run just that slice when groundcrew is already configured.
argument-hint: [clearance | safehouse]
allowed-tools: Bash, AskUserQuestion
---

# setup

Walk the user through installing and configuring the full groundcrew stack — the `@clipboard-health/groundcrew` npm package, the `eugene1g/agent-safehouse` Homebrew formula, the clearance allowlist + env sidecar, the safehouse env sidecar, and `~/.config/groundcrew/config.ts` itself — conversationally. Thirteen bundled scripts do the discovery, install, and file rendering; you are the orchestrator: run them, ask the questions, build a JSON `Answers` object, pipe it to the renderer, and finish with `crew doctor`.

## Scope dispatch

Read the first argument (`$1`) and route accordingly — do this before anything else:

- **empty** (no argument) → run the **full wizard** (Phases 0–9 below) in order.
- **`clearance`** → run only the [Clearance-only flow](#clearance-only-scope) — skip the full wizard.
- **`safehouse`** → run only the [Safehouse-only flow](#safehouse-only-scope) — skip the full wizard.
- **anything else** → print one line — "Unknown scope `<arg>`. Valid scopes: `clearance`, `safehouse` (or no argument for the full wizard)." — and stop.

The full wizard already covers clearance (Phase 6) and safehouse (Phase 7); the two scopes exist so a user with a working `config.ts` can (re)set up just one component without re-running everything.

## Full wizard

The whole flow is Phases 0–9 below. Run them in order. Phase 0 runs silently up front; Phases 1, 2, 6, 7, and parts of 8 are conditional. The user answers Yes/No to a small set of questions and types one workspace dir + (optionally) one repo-picker reply. Everything else is mechanical: file writes, atomic and idempotent, into XDG dirs only. Shell rc files are never edited.

## Quick reference

All scripts live at `${CLAUDE_PLUGIN_ROOT}/scripts/`. Reference them via that env var — never hardcode `~/.claude/plugins/cache/...`.

### Discovery

- **`discover_existing_config.py`** (no args) → prints the absolute path of the first existing config: `groundcrew.config.{ts,js,json,yaml,yml}` in CWD, or `config.{ts,js,json,yaml,yml}` in `$XDG_CONFIG_HOME/groundcrew/` (default `~/.config/groundcrew/`), first match wins. Empty if none. Always exit 0.
- **`load_existing.py <config-path>`** → on success prints the loaded config as JSON on stdout (exit 0); on any failure prints a reason to stderr and exits non-zero. Non-zero = "no seeding available — use static defaults."
- **`discover_repos.py [--workspace-dir <path>]`** → prints a JSON array `[{"owner","repo","sources":["gh"|"local",...]}, ...]`, sorted by `owner/repo`. Always exit 0.
- **`detect_installed_skills.py`** (no args) → prints `{"superpowers": <bool>, "babysitPr": <bool>}`. Always exit 0.
- **`discover_clearance_setup.py`** (no args) → prints `{"personalFileExists","personalFileHasClaudeHosts","envExported","daemonPid":<int>|null,"daemonAgeSeconds":<int>|null}`. Always exit 0.
- **`discover_safehouse_setup.py`** (no args) → prints `{"binaryAvailable","binaryPath","brewFormulaInstalled","envExported","sidecarPresent","sidecarHasFunctions"}`. Always exit 0.

### Install (npm / Homebrew)

- **`install_groundcrew.py [--check]`** → probe via `npm ls -g @clipboard-health/groundcrew`; install via `npm install -g @clipboard-health/groundcrew` if missing. JSON `{action, version, details}` — `action` is `already-installed | installed | missing | failed`. `--check` probes only. Exit 1 when `npm` is absent; propagates npm's exit code on install failure.
- **`install_safehouse.py [--check]`** → probe via `brew list --versions agent-safehouse`; install via `brew install eugene1g/safehouse/agent-safehouse` if missing. Same JSON shape as `install_groundcrew.py`. The tap is auto-added by brew on first install. Exit 1 when `brew` is absent; propagates brew's exit code on install failure.

### Render

- **`compose_initial_prompt.py --features <csv>`** → prints the composed prompt body on stdout. Valid keys: `superpowers`, `babysitPr`, `codeStylePointer`. Empty/absent → baseline only. Unknown key → exit 2.
- **`render_clearance_hosts.py [--target <path>] [--append]`** → writes `~/.config/clearance/personal-allow-hosts`. Default: create, or exit 1 if the file exists (refuse overwrite). `--append`: add the two Claude hosts if missing, idempotent.
- **`render_clearance_env.py [--target <path>]`** → atomically writes `~/.config/clearance/env.sh` exporting `CLEARANCE_PERSONAL_HOSTS=1` and `CLEARANCE_ALLOW_HOSTS_FILES`. JSON `{target, wrote, rcConflicts}`. Smart-merges: any of those vars already exported in `~/.zshrc` / `~/.bash_profile` / `~/.bashrc` / `~/.profile` is emitted as a commented-out line in the sidecar with a `# Already exported in <rc>:<line>` note.
- **`render_safehouse_env.py [--target <path>] [--overrides-file <path>] [--no-overrides-stub]`** → atomically writes `~/.config/agent-safehouse/env.sh` exporting `SAFEHOUSE_APPEND_PROFILE` and defining `safe()` and `safe-claude()` shell functions verbatim from the eugene1g/agent-safehouse README. Also writes an empty `local-overrides.sb` stub alongside on first run. Same smart-merge behavior as the clearance sidecar.
- **`render_config.py --target <path>`** ← reads `Answers` JSON on STDIN → writes the rendered `config.ts` to `--target`, prints the written path. Exit 2 on validation errors.

**Config target:** Define `targetPath` once and use it everywhere:

- `targetPath` = the verbatim `existingConfigPath` returned by `discover_existing_config.py` IF the user chose to overwrite in Phase 2; else `$HOME/.config/groundcrew/config.ts` (fresh-install default).
- `configDir` = `$(dirname "$targetPath")` — this is where `initial-prompt.md` lands (as a sibling of the config file).

## Procedure (full wizard)

### Phase 0 — Pre-flight discovery (silent; no prompts)

Run these six discovery scripts via Bash (in parallel where possible) and capture their output. Do not narrate this to the user — just gather the facts.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/discover_existing_config.py"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/discover_repos.py"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/detect_installed_skills.py"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/discover_clearance_setup.py"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/discover_safehouse_setup.py"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install_groundcrew.py" --check
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install_safehouse.py" --check
```

Capture: `existingConfigPath` (stdout of `discover_existing_config.py`, possibly empty), the repo JSON array, the installed-skills JSON, the clearance JSON, the safehouse JSON, the groundcrew install-check JSON, and the safehouse install-check JSON. Defer `load_existing.py` to Phase 2 — only run it if a config was found AND the user opts to overwrite. Defer re-running `discover_repos.py --workspace-dir` to Phase 3, once you know the project dir.

### Phase 1 — Install prerequisites (conditional)

For each of the two install-check JSON blobs from Phase 0, if `action: "missing"`, ask one **AskUserQuestion** and run the installer on Yes. If `action: "already-installed"` for both, skip this phase silently.

1. **groundcrew** — if missing, **AskUserQuestion** "Install `@clipboard-health/groundcrew` via `npm install -g`? It brings clearance along as a dependency." with **Yes** (Recommended) / **No**. On Yes:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install_groundcrew.py"
   ```

   Parse the JSON. If `action: "failed"`, surface `details` and stop the whole wizard — without groundcrew on the system, `crew doctor` at the end will fail and the rendered `config.ts` is unusable. On **No**, print one line — "Skipping groundcrew install. Install it later with `npm install -g @clipboard-health/groundcrew`, then re-run this wizard." — and stop.

2. **agent-safehouse** — if missing, **AskUserQuestion** "Install `eugene1g/agent-safehouse` via Homebrew? It's the macOS sandbox-exec wrapper groundcrew uses for unattended runs." with **Yes** (Recommended) / **No**. On Yes:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install_safehouse.py"
   ```

   Parse the JSON. If `action: "failed"`, surface `details` and continue — safehouse is optional for `--runner none`, but make sure the Phase 9 summary mentions that crew can only run sandboxed once safehouse is installed.

### Phase 2 — Existing-config confirm (conditional)

Only if `existingConfigPath` is non-empty. Use **AskUserQuestion**: "Found an existing config at `<path>`. Overwrite it?" with options **Yes** / **No**.

- **No** → tell the user where their existing config is and stop. This is a chat session; there's no exit code.
- **Yes** → run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/load_existing.py" "<existingConfigPath>"`. If it exits 0, parse the JSON into `existingConfigJson` and use its values as the defaults you offer in later phases. If it exits non-zero, print one line: "Couldn't load the existing config for seeding (this is expected if you only have a groundcrew source clone, not a global npm install) — falling back to static defaults." Then proceed with static defaults.

If `existingConfigPath` was empty, skip this phase entirely and use static defaults throughout.

### Phase 3 — Workspace

1. **Workspace kind** — **AskUserQuestion** "Workspace kind?" with three options: **auto** (Recommended) / **cmux** / **tmux**. Default = `existingConfigJson.workspace?.kind` if seeded, else `auto`. Record as `workspaceKind`. (`auto` will be omitted from the rendered config — that's intentional.)
2. **Project directory** — ask in chat (free-form, not AskUserQuestion): "Workspace project directory? [default: `~/work`]" (or the seeded value). Empty reply = keep the default. Record as `workspaceProjectDir`. This is **required and non-empty** for the renderer.
3. **Repo picker** — now that you know the project dir, re-run `discover_repos.py --workspace-dir "<workspaceProjectDir>"` to fold any clones under it into the list. Render the merged array as a **numbered chat list**, one repo per line, with source annotations. Pre-mark any already in `existingConfigJson.workspace?.knownRepositories`.

   ```text
   1. foo/bar              (gh + local)
   2. foo/baz              (gh)              [already configured]
   3. quux/zonk            (local)
   ...
   ```

   Ask: "Pick repos by number (comma-separated, e.g. `1, 3, 7`; blank to skip)." Then a free-form follow-up: "Any repos not in the list? (owner/repo, comma-separated; blank for none)." Combine into `knownRepositories` (may be an empty array — that's valid).

   Use a numbered chat list, **not** AskUserQuestion, here: the merged gh+local list routinely runs 10–100 entries, and AskUserQuestion caps at 4 options.

### Phase 4 — Claude permissions and model commands

1. **Bypass permissions** — **AskUserQuestion** "Skip Claude tool-permission prompts (`--permission-mode bypassPermissions`)?" with **Yes** (default; recommended for unattended runs) / **No**. Record as `claudeBypassPermissions`.
2. **Custom claude command** — **AskUserQuestion** "Customize the `claude` command?" **No** (default) / **Yes**. On Yes, ask in chat for the command string; seed the default from the bypass answer (`claude --permission-mode bypassPermissions`) or the existing value. Record under `modelClaude.cmd`.
3. **Custom codex command** — **AskUserQuestion** "Customize the `codex` command?" **No** (default) / **Yes**. On Yes, ask in chat for the command string. Record under `modelCodex.cmd`.

If the user keeps the bypass default and customizes nothing, leave `modelClaude`/`modelCodex` unset — the renderer derives the claude bypass command from `claudeBypassPermissions` on its own.

### Phase 5 — Initial-prompt features (additive)

The built-in default already ships an always-on baseline (placeholders + autonomy guidance + workflow). This phase only offers additive snippets on top. Render a **numbered chat list**, pre-selecting the rows that `detect_installed_skills.py` found:

```text
The built-in default ships placeholders + autonomy guidance + workflow (always-on; not editable here).

Additional features (pick by number, comma-separated; blank = omit prompts.initial, runtime falls back to built-in default):
  1. Invoke `superpowers` skills for non-trivial work   (detected ✓)
  2. Invoke `core:babysit-pr` after opening the PR      (detected ✓)
  3. Read `CLAUDE.md` / `AGENTS.md` before writing code
```

Annotate `(detected ✓)` on row 1 when `superpowers` is true and row 2 when `babysitPr` is true, and treat those as pre-selected (the default if the user hits enter). Map selections to feature keys: 1 → `superpowers`, 2 → `babysitPr`, 3 → `codeStylePointer`. Record the chosen keys as `promptFeatures`.

- **Empty selection** → `promptFeatures: []`. The renderer omits the `prompts.initial` block entirely and the runtime falls back to its default. Write no `initial-prompt.md`.
- **Non-empty** → defer the actual `compose_initial_prompt.py` call and the `initial-prompt.md` write to Phase 9 (so it happens alongside `config.ts`).

### Phase 6 — Clearance egress allowlist (recommended)

Skip this phase if the Phase 0 clearance JSON reported `personalFileHasClaudeHosts: true` AND `envExported: true` AND a sidecar already lives at `~/.config/clearance/env.sh`. In that case print one line — "Clearance is already set up." — and move on.

Otherwise, **AskUserQuestion** "Set up clearance? Without it, safehouse-wrapped Claude sessions log DENY entries for `downloads.claude.ai` (claude updater) and `mcp-proxy.anthropic.com` (MCP proxy) every session." with **Yes** (Recommended) / **No**.

On **Yes** — run two file-render steps, then conditionally offer a daemon restart:

1. **Allowlist file** — three sub-cases keyed off the Phase 0 probe:
   - `personalFileExists: false` → create:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_clearance_hosts.py"
     ```

   - `personalFileExists: true` AND `personalFileHasClaudeHosts: false` → append:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_clearance_hosts.py" --append
     ```

   - `personalFileExists: true` AND `personalFileHasClaudeHosts: true` → skip; nothing to do.

2. **Env sidecar** — always run, unconditionally; the renderer is idempotent and the smart-merge handles already-configured rc files:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_clearance_env.py"
   ```

   Parse the JSON. If `rcConflicts` is non-empty, remember the count for the Phase 9 summary (so the user knows which vars stayed under rc-file ownership rather than moving to the sidecar).

3. **Daemon-stale prompt** — only if `daemonPid` is set AND `daemonAgeSeconds > 3600`. **AskUserQuestion** "A clearance daemon has been running for `<formatted age>`; it won't pick up the new env until it restarts. Kill it now? (The next `crew` run respawns it.)" **Yes** / **No**. On Yes, run `kill <daemonPid>` and confirm. On No, print a short reminder. Skip this prompt entirely when the daemon is younger than an hour.

On **No** to the top-level question: skip all writes and print one line pointing at `~/.cache/clearance/clearance.log` so the user can find DENY entries later.

Track what happened (allowlist written/appended, sidecar written, conflicts count, daemon killed) for the Phase 9 summary. None of this goes into `config.ts`.

### Phase 7 — Safehouse env sidecar (recommended on macOS)

Skip this phase if the Phase 0 safehouse JSON reported `sidecarPresent: true` AND `sidecarHasFunctions: true`. In that case print one line — "Safehouse env sidecar already in place." — and move on.

Skip this phase entirely if the user said **No** to safehouse install in Phase 1 (the sidecar's `safe()` wrapper would just fail without `safehouse` on PATH).

Otherwise — no question needed — run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_safehouse_env.py"
```

Parse the JSON. If `rcConflicts` is non-empty, remember the count for the Phase 9 summary. If `overridesStub` is a path (not `null`), the wizard created a fresh `local-overrides.sb` stub at that path — mention it in the summary so the user knows where to add machine-local sandbox rules later (see `https://agent-safehouse.dev/docs`).

Track what happened (sidecar written, conflicts count, overrides stub created) for the Phase 9 summary.

### Phase 8 — Orchestrator and logging (both optional)

1. **Session cap** — **AskUserQuestion** "Set a session-usage cap?" **No** (default) / **Yes**. On Yes, ask in chat "Session limit percentage (1–100)?" and record an integer as `sessionLimitPercentage`.
2. **Log file** — **AskUserQuestion** "Set a custom log file location?" **No** (default) / **Yes**. On Yes, ask in chat "Log file path?" and record as `loggingFile`.

### Phase 9 — Render config, write sidecars, run crew doctor, exit

1. If Phase 5 picked features, compose the prompt and write it next to the config:

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

4. Run `crew doctor` and surface the result:

   ```bash
   crew doctor
   ```

   On exit 0, print "✓ `crew doctor` passed — groundcrew is ready." On non-zero, print "`crew doctor` reported issues:" followed by its stdout verbatim so the user can act on the specific check that failed. Do not abort the wizard on doctor failure — the user may want to inspect the rendered files even if doctor is unhappy.

5. Print the absolute path(s) written and a numbered Next-Steps block. Generate the Step 1 clone commands from `knownRepositories`, filtering out repos the Phase 3 picker reported with `"local"` in `sources` (those are already cloned). Emit `PROJECT_DIR=<workspaceProjectDir>` once, then one `git clone` line per remaining repo using groundcrew's own format (matches `crew init`'s clone guidance). If every selected repo was local, omit Step 1 entirely.

   Example with `workspaceProjectDir: "~/work"`, two not-yet-cloned repos, and clearance + safehouse sidecars both written:

   ```text
   Wizard complete. Files written:
     ~/.config/groundcrew/config.ts
     ~/work/initial-prompt.md
     ~/.config/clearance/personal-allow-hosts
     ~/.config/clearance/env.sh
     ~/.config/agent-safehouse/env.sh
     ~/.config/agent-safehouse/local-overrides.sb  (empty stub)

   Next steps:
     1. Clone your configured repositories into the workspace:
          PROJECT_DIR=~/work
          git clone git@github.com:foo/bar.git "$PROJECT_DIR/foo/bar"
          git clone git@github.com:baz/qux.git "$PROJECT_DIR/baz/qux"
     2. Add ONE line to your shell rc (~/.zshrc or ~/.bashrc) so future
        shells pick up the clearance + safehouse env:

          for f in ~/.config/clearance/env.sh ~/.config/agent-safehouse/env.sh; do
            [ -f "$f" ] && . "$f"
          done

        Then start a new shell or `source` it.
     3. In Linear: Settings → Integrations → GitHub, connect your workspace
        (required for crew to map Linear tickets to GitHub PRs).
     4. Run `crew run --watch` to start the orchestrator.
   ```

   If Phase 6 or 7 reported `rcConflicts > 0`, append one line: "(N of the sidecars' exports were left commented because they're already in your rc — see the sidecars' inline notes.)"

## Clearance-only scope

Reached when the argument is `clearance`. Only the clearance bits — probe, then the two atomic file renders and the daemon-stale check. Use this when groundcrew is already configured but clearance isn't.

1. **Probe (silent)** — run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/discover_clearance_setup.py"` and capture the JSON. Don't narrate it. If both `personalFileHasClaudeHosts: true` AND `envExported: true`, print one line — "Clearance is already set up." — and stop.
2. **Run Phase 6's mechanical steps 1–3** ([Allowlist file](#phase-6--clearance-egress-allowlist-recommended), Env sidecar, Daemon-stale prompt) exactly as written, keyed off this scope's probe JSON. **Skip Phase 6's top-level "Set up clearance?" Yes/No question** — invoking the `clearance` scope is the user's yes.
3. **Summary** — print the paths written and the one-line rc snippet:

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

   If the env-sidecar render reported conflicts, append: "(N of the sidecar's exports were left commented because they're already in your rc — see the sidecar's inline notes.)"

## Safehouse-only scope

Reached when the argument is `safehouse`. Only the safehouse bits — probe, install if missing, then write the env sidecar. macOS only. Use this when groundcrew + clearance are already configured but safehouse isn't.

1. **Probe (silent)** — run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/discover_safehouse_setup.py"` and capture the JSON. Don't narrate it.
2. **Install if missing** — only if `brewFormulaInstalled: false`, run Phase 1's safehouse install prompt: **AskUserQuestion** "Install `eugene1g/agent-safehouse` via Homebrew? It's the macOS sandbox-exec wrapper groundcrew uses for unattended runs." **Yes** (Recommended) / **No**. On Yes run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install_safehouse.py"`; if `action: "failed"`, surface `details` and stop (don't write the sidecar without a working binary). On No, print one line — "Skipping safehouse install. Run `brew install eugene1g/safehouse/agent-safehouse` later, then re-run `/groundcrew-setup:setup safehouse`." — and stop. If `brewFormulaInstalled: true`, skip this step.
3. **Run Phase 7's env-sidecar render** ([Phase 7](#phase-7--safehouse-env-sidecar-recommended-on-macos)) exactly as written. Parse the JSON; remember `rcConflicts` and any `overridesStub` for the summary.
4. **Summary** — print the paths written and the one-line rc snippet:

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

   If the sidecar render reported conflicts, append: "(N of the sidecar's items were left commented because they're already in your rc — see the sidecar's inline notes.)" If safehouse was actually installed (not already-installed), append "Installed agent-safehouse `<version>` via Homebrew."

## Common mistakes

- **Don't use AskUserQuestion for the repo picker or the Phase 5 feature picker.** Both are multi-select over lists that can exceed AskUserQuestion's 4-option cap. Always render them as numbered chat lists and parse the comma-separated reply.
- **Don't hardcode `~/.claude/plugins/cache/...` paths to the scripts.** Always invoke them through `${CLAUDE_PLUGIN_ROOT}/scripts/<name>` — that env var resolves to the installed plugin dir at runtime and survives version bumps.
- **Don't edit the user's shell rc files.** Always write the sidecars and print the one-line source snippet. Auto-appending to `~/.zshrc` / `~/.bash_profile` breaks dotfile managers (chezmoi, yadm, stow) that would clobber or duplicate the entry.
- **Don't offer to kill the clearance daemon unless `daemonAgeSeconds > 3600`.** A fresh daemon already has current config; killing it is pointless churn.
- **Don't ask Phase 6's "Set up clearance?" / offer the Phase 7 decline-skip when running the `clearance` / `safehouse` scope.** Invoking a scope is the user's yes; the scope flows run the mechanical steps directly.
- **Don't treat a non-zero `load_existing.py` exit as fatal.** It means "no seeding available" — print the one-line fallback note and continue with static defaults. The user can always re-enter values.
- **Don't run `render_clearance_hosts.py` without `--append` against a file that already exists.** Create mode exits 1 (refuses overwrite). When `personalFileExists: true`, only the `--append` path is safe.
- **Don't render the safehouse sidecar when the user declined the safehouse install in Phase 1 (full wizard) or the `safehouse` scope's install prompt.** The sidecar's `safe()` wrapper would just fail on every shell start without `safehouse` on PATH.
- **Don't proceed past a failed `install_groundcrew.py`.** Without groundcrew installed, the rendered `config.ts` is unusable and `crew doctor` at the end will fail.
- **Don't send `null` for unset optional keys in the `Answers` object.** Omit them entirely — the renderer treats absent keys as "use the shipped default," but a `null` value can trip type validation and exit 2.
- **Don't forget that `workspaceProjectDir` and `knownRepositories` are required.** The renderer exits 2 without them. `knownRepositories` may be an empty array, but it must be present.
- **Don't write `initial-prompt.md` when Phase 5 selected nothing.** An empty `promptFeatures` means the renderer omits the `prompts.initial` block; a stray `initial-prompt.md` with no reference to it is dead weight.
- **Don't pass the default path to `render_config.py --target` when overwriting a discovered config.** If Phase 2 found an existing config at a non-default location (e.g. `${PWD}/groundcrew.config.ts`), using `$HOME/.config/groundcrew/config.ts` as the target writes a stray file and leaves the real config untouched. Always use `$targetPath` (the verbatim discovered path on overwrite, else the fresh-install default).
- **Don't abort the wizard on a non-zero `crew doctor` exit.** The user may want to inspect the rendered files even if doctor flags an unrelated issue (e.g. `GROUNDCREW_LINEAR_API_KEY` not set yet). Surface the output verbatim and keep going.

## Notes

- **Seeding fallback is expected on source-only installs.** `load_existing.py` shells out to groundcrew's `loadConfig()`, which needs `@clipboard-health/groundcrew` installed via npm. Users who only have a source clone (no global install) hit the fallback — that's normal, not a bug. Say so in the warning text so they don't think the wizard is broken.
- **`workspaceKind: "auto"` is not emitted.** The renderer drops it (the runtime default is `auto`), so an `auto` answer produces a config with no `workspaceKind` key. Only `cmux` / `tmux` get written.
- **Phases 6 and 7 never touch `config.ts`.** Clearance and safehouse setup are separate runtime concerns. They only affect the Phase 9 summary text, not the rendered config.
- **The `clearanceSetup` / `safehouseSetup` keys in `Answers` are ignored by the renderer.** If you include them for your own bookkeeping, that's harmless — `render_config.py` ignores any key it doesn't recognize, so neither reaches `config.ts`.
- **One Bash call per render.** Pipe the `Answers` JSON via a quoted heredoc rather than writing a temp file and reading it back; it's one auto-approvable invocation instead of two.
- **The `clearance` and `safehouse` scopes are slices of the full wizard.** Users who already have a working `config.ts` but want to set up clearance or safehouse on its own can pass that scope to `/groundcrew-setup:setup` without re-running everything. The mechanical steps are single-sourced from Phases 6 and 7.
