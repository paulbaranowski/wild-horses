# marketplace

Scaffold a Claude Code plugin marketplace with proper structure, schema validation, and `CLAUDE.md` conventions. Use this when you're starting a new marketplace repo, or want to add marketplace structure to an existing one.

Install:

```text
/plugin install marketplace@wild-horses
```

## Skill

### `/create`

Walks you through creating a marketplace repo: asks for a name, checks for an existing skill or plugin to import, and generates `marketplace.json`, `plugin.json`, and `CLAUDE.md` with the right conventions.

```text
/create
/create my-marketplace
```

The skill will:

1. Ask for a marketplace name (normalized to lowercase, hyphen-separated).
2. Pull the owner from `git config user.name`, or ask if not set.
3. Ask whether you have an existing `SKILL.md` or plugin directory to import. If yes, it copies it into the proper `plugins/<name>/` structure; if no, it creates an empty marketplace you can add plugins to later.
4. Generate `.claude-plugin/marketplace.json`, the first plugin's `.claude-plugin/plugin.json`, and a `CLAUDE.md` that captures the conventions you'll need to remember.
5. Validate the result with `claude plugin validate .` so you find schema mistakes before commit, not after.

## Why this matters

Claude Code plugins are how you package reusable AI workflows — analysis tools, scaffolding commands, automated loops — and share them across projects and teams. A marketplace is a collection of plugins others can install with a single command. Getting the directory structure, manifest fields, and conventions right is fiddly; this skill handles it interactively so you can focus on the plugin content.
