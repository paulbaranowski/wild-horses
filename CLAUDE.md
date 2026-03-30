# Wild Horses - Claude Code Plugin Marketplace

## Project Structure

This is a Claude Code **plugin marketplace** containing the `harness-review` plugin.

```
.claude-plugin/marketplace.json    -- marketplace catalog (points to plugins)
plugins/harness-review/            -- plugin root
  .claude-plugin/plugin.json       -- plugin manifest
  skills/harness-review/SKILL.md   -- the harness-review skill
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

### plugin.json
- Keep it minimal: `name`, `description`, `version`, `author`. That's it for most plugins.
- `repository` must be a **string** (URL), not an object.
- `name` determines the skill namespace (e.g., plugin name `harness-review` + skill name `harness-review` = `/harness-review:harness-review`).

### marketplace.json
- The marketplace `name` is the brand (`wild-horses`). The plugin entry `name` is the install identifier (`harness-review`).
- Install command: `/plugin install harness-review@wild-horses`
- Plugin `source` for local plugins must start with `./` and is relative to the repo root.
- Set `version` in the marketplace entry OR plugin.json, not both. Plugin.json wins silently.
- Optional useful fields on plugin entries: `category`, `homepage`, `license`, `keywords`.

### SKILL.md
- Frontmatter uses **hyphens** (e.g., `user-invocable`, `disable-model-invocation`), not underscores.
- `user-invocable: true` (default) makes the skill appear in the `/` menu.
- `disable-model-invocation: true` prevents Claude from auto-triggering the skill.

## Reference Marketplaces

- Official (canonical, 119 plugins): https://github.com/anthropics/claude-plugins-official
- Laravel (small branded marketplace): https://github.com/laravel/claude-code
- Shopware (good schema/metadata usage): https://github.com/shopwareLabs/ai-coding-tools
- Devflow (community single-purpose): https://github.com/kwiercioch-okicode/devflow
- Anthropic Skills: https://github.com/anthropics/skills
