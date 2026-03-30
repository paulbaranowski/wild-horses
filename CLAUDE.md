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

## Reference Marketplaces

- Official (canonical, 119 plugins): https://github.com/anthropics/claude-plugins-official
- Laravel (small branded marketplace): https://github.com/laravel/claude-code
- Shopware (good schema/metadata usage): https://github.com/shopwareLabs/ai-coding-tools
- Devflow (community single-purpose): https://github.com/kwiercioch-okicode/devflow
- Anthropic Skills: https://github.com/anthropics/skills

## Rules

### Validation
- Always run `claude plugin validate .` or `/plugin validate .` before pushing. A plugin that fails validation will silently not install.
- Validate both the marketplace root AND individual plugin dirs: `claude plugin validate ./plugins/harness-review`

### marketplace.json
- Lives in `.claude-plugin/` at the repo root.
- Do NOT add `"$schema"` — the validator rejects unrecognized keys, even though some official repos include it.
- The marketplace `name` is the brand (`wild-horses`). Each plugin entry `name` is the install identifier (`harness-review`).
- Install command: `/plugin install harness-review@wild-horses`
- Plugin `source` for local plugins must start with `./` and is relative to the repo root (NOT relative to `.claude-plugin/`).
- Set `version` in the marketplace entry OR plugin.json, not both. Plugin.json wins silently. For relative-path plugins, the docs recommend setting it in the marketplace entry.
- Optional useful fields on plugin entries: `category`, `homepage`, `license`, `keywords`, `author`.
- `metadata.description` describes the marketplace itself (not the plugin).

### plugin.json
- Lives in `plugins/<name>/.claude-plugin/plugin.json`.
- Keep it minimal: `name`, `description`, `version`, `author`. That's typically all you need.
- `repository` must be a **string** (URL like `"https://github.com/user/repo"`), NOT an object. An object like `{"type": "git", "url": "..."}` causes validation to fail with: `repository: Invalid input: expected string, received object`. This was the root cause of the plugin silently failing to install.
- `name` determines the skill namespace (e.g., plugin name `harness-review` + skill name `harness-review` = `/harness-review:harness-review`).
- The plugin directory name, the plugin.json `name`, and the marketplace entry `name` should all match.

### SKILL.md
- Frontmatter uses **hyphens** (e.g., `user-invocable`, `disable-model-invocation`), NOT underscores. `user_invocable` is wrong; `user-invocable` is correct.
- `user-invocable: true` (default) makes the skill appear in the `/` menu.
- `disable-model-invocation: true` prevents Claude from auto-triggering the skill.
- Other useful frontmatter: `name`, `description`, `argument-hint`, `allowed-tools`, `context`, `model`.

### Directory Structure
- Each plugin lives in its own directory under `plugins/`.
- Skills, agents, commands, and hooks go at the **plugin root** level, NOT inside `.claude-plugin/`. Only `plugin.json` goes in `.claude-plugin/`.
- Plugins are copied to `~/.claude/plugins/cache` on install — they cannot reference files outside their directory with `../`.

### Testing & Debugging
- Test locally without installing: `claude --plugin-dir ./plugins/harness-review`
- Use `claude --debug` to see plugin loading details and errors.
- After making changes during a session, run `/reload-plugins` to pick up updates.
- When updating an already-installed plugin, users need to run `/plugin marketplace update wild-horses` to get new code.
- Bump the `version` on every change — Claude Code uses version to detect updates. Same version = skip.

### Common Pitfalls (learned the hard way)
1. **Invalid `repository` field type** — using an npm-style object instead of a string causes silent install failure. The validator catches this: always validate before pushing.
2. **`$schema` in marketplace.json** — rejected as an unrecognized key despite being in the official docs and some official repos.
3. **`user_invocable` (underscore)** — wrong. Must be `user-invocable` (hyphen). All SKILL.md frontmatter keys use hyphens.
4. **Plugin name mismatch** — if the marketplace entry `name` doesn't match the plugin.json `name` and plugin directory name, the plugin may not resolve correctly.
5. **Version not bumped** — existing installs won't see changes if the version stays the same due to caching.
