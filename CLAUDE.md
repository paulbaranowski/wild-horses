# Wild Horses - Claude Code Plugin Marketplace

## Project Structure

This is a Claude Code **plugin marketplace** containing the `harness-review` plugin.

```
.claude-plugin/marketplace.json    -- marketplace catalog (points to plugins)
plugins/wild-horses/               -- plugin root
  .claude-plugin/plugin.json       -- plugin manifest
  skills/harness-review/SKILL.md   -- the harness-review skill
```

## Key References

- Plugin Marketplaces: https://code.claude.com/docs/en/plugin-marketplaces
- Creating Plugins: https://code.claude.com/docs/en/plugins
- Plugins Reference (schemas, validation, caching): https://code.claude.com/docs/en/plugins-reference
- Skills Reference (frontmatter fields): https://code.claude.com/docs/en/skills

## Important Notes

- `plugin.json` field `repository` must be a **string** (URL), not an object.
- SKILL.md frontmatter uses **hyphens** (e.g., `user-invocable`, `disable-model-invocation`), not underscores.
- The marketplace `name` is the install identifier: `/plugin install harness-review@wild-horses`.
- The `plugin.json` `name` determines the skill namespace: `/wild-horses:harness-review`.
- For relative-path plugins, set `version` in the marketplace entry OR plugin.json, not both. Plugin.json wins silently.
- Validate with: `claude plugin validate .` or `/plugin validate .`
