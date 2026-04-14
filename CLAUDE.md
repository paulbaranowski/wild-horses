# Wild Horses - Claude Code Plugin Marketplace

## Project Structure

This is a Claude Code **plugin marketplace** containing harness engineering plugins.

```text
.claude-plugin/marketplace.json    -- marketplace catalog (points to plugins)
plugins/harness/                   -- plugin root
  .claude-plugin/plugin.json       -- plugin manifest
  commands/audit.md                -- /harness:audit
  commands/setup.md                -- /harness:setup
  commands/reasoning-gaps.md       -- /harness:reasoning-gaps
```

## Key References

- Plugin Marketplaces: https://code.claude.com/docs/en/plugin-marketplaces
- Creating Plugins: https://code.claude.com/docs/en/plugins
- Plugins Reference (schemas, validation, caching): https://code.claude.com/docs/en/plugins-reference
- Skills Reference (frontmatter fields): https://code.claude.com/docs/en/skills

## Rules

### Marketplace Structure
- marketplace.json goes in `.claude-plugin/` at the repo root. Do NOT add `"$schema"` — the validator rejects unrecognized keys.
- Each plugin lives in its own directory under `plugins/`. The plugin's `.claude-plugin/plugin.json` goes inside that directory, NOT at the repo root.
- Skills, agents, commands, and hooks go at the **plugin root** level, NOT inside `.claude-plugin/`.
- Validate with: `claude plugin validate .` or `/plugin validate .`

### Versioning
- **Every change to a plugin's content (commands, skills, agents, hooks) requires a version bump in that plugin's `plugin.json`.** Bump patch for fixes, minor for new features/improvements, major for breaking changes.

### plugin.json
- Keep it minimal: `name`, `description`, `version`, `author`. That's it for most plugins.
- `repository` must be a **string** (URL), not an object.
- `name` determines the command namespace (e.g., plugin name `harness` + command name `audit` = `/harness:audit`).

### marketplace.json
- The marketplace `name` is the brand (`wild-horses`). The plugin entry `name` is the install identifier (`harness`).
- Install command: `/plugin install harness@wild-horses`
- Plugin `source` for local plugins must start with `./` and is relative to the repo root.
- Set `version` in the marketplace entry OR plugin.json, not both. Plugin.json wins silently.
- Optional useful fields on plugin entries: `category`, `homepage`, `license`, `keywords`.

### Commands vs Skills (slash menu namespacing)
- **Use `commands/` for user-invoked slash commands.** Commands get the `plugin-name:command-name` prefix in the `/` autocomplete menu (e.g., `/harness:audit`).
- **Skills (`skills/name/SKILL.md`) do NOT get the namespace prefix in the UI.** A skill named `setup` in a plugin named `harness` shows as just `/setup` with `(harness)` in the description — not `/harness:setup`. This is a Claude Code UI behavior as of v2.1.
- If you need model auto-invocation (`disable-model-invocation: false`), you must use skills — commands cannot be auto-triggered by Claude. Otherwise prefer commands.
- Command frontmatter: `description` (required), `argument-hint`, `allowed-tools`. No `name:` field — the filename is the command name.
- Skill frontmatter uses **hyphens** (e.g., `user-invocable`, `disable-model-invocation`), not underscores.

## Reference Marketplaces

- Official (canonical, 119 plugins): https://github.com/anthropics/claude-plugins-official
- Laravel (small branded marketplace): https://github.com/laravel/claude-code
- Shopware (good schema/metadata usage): https://github.com/shopwareLabs/ai-coding-tools
- Devflow (community single-purpose): https://github.com/kwiercioch-okicode/devflow
- Anthropic Skills: https://github.com/anthropics/skills
