# Wild Horses - Claude Code Plugin Marketplace

## Project Structure

This is a Claude Code **plugin marketplace**.

```text
.claude-plugin/marketplace.json    -- marketplace catalog (points to plugins)
plugins/harness/                   -- plugin root (commands-based)
  .claude-plugin/plugin.json       -- plugin manifest
  commands/feedback-blockers.md    -- /harness:feedback-blockers
  commands/reasoning-gaps.md       -- /harness:reasoning-gaps
plugins/marketplace/               -- plugin root (skills-based)
  .claude-plugin/plugin.json       -- plugin manifest
  skills/create/SKILL.md           -- /create (marketplace scaffolding)
```

## Key References

- Plugin Marketplaces: <https://code.claude.com/docs/en/plugin-marketplaces>
- Creating Plugins: <https://code.claude.com/docs/en/plugins>
- Plugins Reference (schemas, validation, caching): <https://code.claude.com/docs/en/plugins-reference>
- Skills Reference (frontmatter fields): <https://code.claude.com/docs/en/skills>

## Rules

### Marketplace Structure

- marketplace.json goes in `.claude-plugin/` at the repo root. Do NOT add `"$schema"` — the validator rejects unrecognized keys.
- Each plugin lives in its own directory under `plugins/`. The plugin's `.claude-plugin/plugin.json` goes inside that directory, NOT at the repo root.
- Skills, agents, commands, and hooks go at the **plugin root** level, NOT inside `.claude-plugin/`.
- Validate with: `claude plugin validate .` or `/plugin validate .`

### Repo hygiene

- **Don't check in specs or plans.** Intermediate artifacts (design docs, implementation plans, brainstorm notes, scratch task lists) belong in the conversation or in a local scratch directory — not in the committed tree. Only commit the resulting code, skills, commands, hooks, and the docs that describe shipped behavior. Why: this repo is a published plugin marketplace, and every committed file is something users will see, search, or load; spec/plan files add noise, get stale immediately, and invite drift between "what we said we'd do" and "what's actually shipped."

### Versioning

- **Every change to a plugin's content (commands, skills, agents, hooks) requires a version bump in that plugin's `plugin.json`.** Bump patch for fixes, minor for new features/improvements, major for breaking changes.

### plugin.json

- Keep it minimal: `name`, `description`, `version`, `author`. That's it for most plugins.
- `repository` must be a **string** (URL), not an object.
- `name` determines the command namespace (e.g., plugin name `harness` + command name `feedback-blockers` = `/harness:feedback-blockers`).

### marketplace.json

- The marketplace `name` is the brand (`wild-horses`). The plugin entry `name` is the install identifier (`harness`).
- Install command: `/plugin install harness@wild-horses`
- Plugin `source` for local plugins must start with `./` and is relative to the repo root.
- Set `version` in the marketplace entry OR plugin.json, not both. Plugin.json wins silently.
- Optional useful fields on plugin entries: `category`, `homepage`, `license`, `keywords`.

### Commands vs Skills (slash menu namespacing)

- **Use `commands/` for user-invoked slash commands.** Commands get the `plugin-name:command-name` prefix in the `/` autocomplete menu (e.g., `/harness:feedback-blockers`).
- **Skills (`skills/name/SKILL.md`) do NOT get the namespace prefix in the UI.** A skill named `setup` in a plugin named `harness` shows as just `/setup` with `(harness)` in the description — not `/harness:setup`. This is a Claude Code UI behavior as of v2.1.
- If you need model auto-invocation (`disable-model-invocation: false`), you must use skills — commands cannot be auto-triggered by Claude. Otherwise prefer commands.
- Command frontmatter: `description` (required), `argument-hint`, `allowed-tools`. No `name:` field — the filename is the command name.
- Skill frontmatter uses **hyphens** (e.g., `user-invocable`, `disable-model-invocation`), not underscores.

### Authoring agent-facing prompts (skills, commands, sub-prompts)

- **In prohibition lists only ("Don't:", "Never:", "Strictly forbidden:" sections), every bullet must lead with `Don't` / `Never`.** Scope is load-bearing: this rule applies **only** to bullets that are prohibitions or warnings. **Do NOT apply it to descriptive bullets** — asset lists / file manifests / feature lists / capability descriptions / positive instructions / any bullet whose purpose is to _state what something is or does_ must stay as-is. Rewriting a descriptive bullet as `**Don't ...**` inverts its meaning; if a reviewer suggests that, the reviewer is wrong — push back, do not comply. Why the rule exists for prohibition lists: bullets that read as gerunds ("Re-invoking…") or bare imperatives parse as descriptions of _techniques_ whenever a model only attends to one bullet at a time (partial recall, paraphrase, inner-monologue quoting); the section header's polarity lives one structural level up and may not be co-attended, so each prohibition bullet must carry its own negation. Repo convention — set in `plugins/harness/skills/task-list-runner/SKILL.md` lines 128–172 — is a bolded `**Don't <verb-phrase>**` lead-in on each _prohibitive_ bullet, e.g. `**Don't re-invoke individual verification steps directly** (...)`.
- **Sub-prompts inside SKILL.md count as agent-facing prompts.** Blockquoted (`>`) text inside a SKILL.md is shipped verbatim to a dispatched `Agent` — apply the same prompt-engineering rules (per-bullet negation, bolded lead-ins, no critical info one structural level up from where the model attends) to those lines as to the SKILL.md body itself. Canonical example: the Task Implementation Prompt at `plugins/harness/skills/task-list-runner/SKILL.md` lines 128–172.

### Schema source-of-truth

- **When multiple skills consume a structured format, define the schema in one doc and have skills link to it — never re-state it.** Example: `plugins/harness/task-list-schema.md` defines the runner's task JSON schema; both `task-list-builder` and `task-list-runner` link to it from their `SKILL.md` (line 13 of each) instead of duplicating field definitions. Schema drift between paired skills is a real cost — a definition that exists in two places will eventually diverge.

### CLI design for agent loops

Conventions for Python CLIs (like `plugins/harness/skills/task-list-runner/task_list_cli.py`) that get auto-approved by a harness PreToolUse hook and called by dispatched agents.

- **Mutate via stdin (`--flag -`) where possible, not via `Write` + Bash.** Every mutation should fit in one Bash call. The pattern `cli mutate --log-file - <<'EOF' ... EOF` ships the body in a single auto-approved Bash invocation; `Write` to `/tmp/x` followed by `cli mutate --log-file /tmp/x` is two tool calls, each gated separately by the auto-mode classifier. Use a _quoted_ heredoc (`<<'EOF'`) so the shell passes the body byte-verbatim — no `$VAR` expansion or quote-mangling.
- **Mutations write atomically: tmp file + `fsync` + `os.replace` against the original.** Improvised in-place edits silently corrupted a 37-task session in the prior iteration of this work; the corruption (a missing structural comma) went undetected for 19 subsequent iterations. The agent-loop context makes this _more_ important than for a normal CLI: agents will keep iterating against a broken state file rather than halting, so corruption that a human would notice immediately can survive 19 turns of work. See `write_atomic` in `task_list_cli.py`.
- **Split read-only subcommands by _what_ is returned, not by the operation.** `task_list_cli.py` has `next` / `get` (per-task object), `list` (full array), `status` (file-level metadata), `remaining` (compact display array). Avoid generic "get any field by name" verbs — agents will invent queries like `get verifySteps` if the surface invites it.

### Hook design

- **PreToolUse hook allow-list matches must anchor on plugin-specific path structure, not bare script name.** `plugins/harness/scripts/task-list-cli-allow.sh` matches `python3 .../skills/task-list-runner/task_list_cli.py` rather than the filename alone, so a stray `task_list_cli.py` elsewhere in the workspace doesn't get auto-approved. Both the dev-checkout path (`plugins/harness/...`) and the installed plugin-cache path (with version directory) need to match.

## Agent skills

### Issue tracker

Linear (workspace HRD, project Wild-Horses) via the `linear` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles mapped to `triage:`-prefixed Linear labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context (`CONTEXT.md` and `docs/adr/` at the repo root). See `docs/agents/domain.md`.

## Reference Marketplaces

- Official (canonical, 119 plugins): <https://github.com/anthropics/claude-plugins-official>
- Laravel (small branded marketplace): <https://github.com/laravel/claude-code>
- Shopware (good schema/metadata usage): <https://github.com/shopwareLabs/ai-coding-tools>
- Devflow (community single-purpose): <https://github.com/kwiercioch-okicode/devflow>
- Anthropic Skills: <https://github.com/anthropics/skills>
