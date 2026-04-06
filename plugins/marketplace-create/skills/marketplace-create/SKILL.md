---
name: marketplace-create
description: Scaffold a Claude Code plugin marketplace with proper structure, schema validation, and CLAUDE.md conventions. Asks for the marketplace name and whether to import an existing skill. Use when starting a new marketplace repo or adding marketplace structure to an existing repo.
user-invocable: true
argument-hint: "[marketplace-name]"
---

# Create a Claude Code Plugin Marketplace

Scaffold a complete Claude Code plugin marketplace in the current repository.

**Arguments:** "$ARGUMENTS"

---

## Phase 1: Gather Information (you do this — interactive)

### Step 1: Marketplace name

If a marketplace name was provided in `$ARGUMENTS`, use it. Otherwise, **ask the user**:

> What would you like to name your marketplace?
> (This becomes the brand in install commands, e.g. `/plugin install my-plugin@<marketplace-name>`)

Normalize the name: lowercase, hyphen-separated, no spaces. If you had to normalize it, tell the user what you changed.

### Step 2: Owner name

Check git config for `user.name`. If not available, ask the user.

### Step 3: Existing skill to import?

Ask the user:

> Do you have an existing SKILL.md or plugin you'd like to import into this marketplace?
> - If yes, provide the path to the skill or plugin directory
> - If no, I'll create an empty marketplace you can add plugins to later

**If the user provides a path:**
- Read the file/directory at that path
- If it's a SKILL.md, extract the skill name and description from frontmatter
- If it's a plugin directory (has `.claude-plugin/plugin.json`), read the plugin manifest
- Use this information to create the first plugin entry in the marketplace
- Copy or reference the skill into the proper `plugins/<name>/skills/<name>/` structure

**If the user says no (or skips):**
- Create an empty marketplace with no plugins — the `plugins` array in marketplace.json will be empty

### Step 4: Check for conflicts

Before creating anything:
- If `.claude-plugin/marketplace.json` already exists, warn the user and ask if they want to overwrite or abort
- If `CLAUDE.md` already exists, tell the user you'll append marketplace conventions rather than overwrite

### Step 5: Confirm the plan

Present a summary and ask for confirmation:

```
Marketplace: <name>
Owner: <owner>
Import: <skill/plugin name or "none — empty marketplace">

Files to create:
  .claude-plugin/marketplace.json
  CLAUDE.md (create or append)
  plugins/<plugin-name>/...  (only if importing a skill)
```

---

## Phase 2: Create Marketplace Structure (you do this, after confirmation)

### Step 1: Create marketplace.json

Create `.claude-plugin/marketplace.json`:

```json
{
  "name": "<marketplace-name>",
  "owner": {
    "name": "<owner-name>"
  },
  "metadata": {
    "description": "<ask user for a one-line description of their marketplace>"
  },
  "plugins": []
}
```

If importing a skill/plugin, add it to the `plugins` array:

```json
{
  "name": "<plugin-name>",
  "source": "./plugins/<plugin-name>",
  "description": "<from imported skill/plugin>",
  "version": "0.1.0",
  "author": {
    "name": "<owner-name>"
  },
  "category": "development",
  "license": "MIT"
}
```

### Step 2: Create or update CLAUDE.md

If CLAUDE.md does not exist, create it. If it does exist, append marketplace conventions to it.

Use this template, filling in the marketplace name:

```markdown
# <Marketplace Display Name> - Claude Code Plugin Marketplace

## Project Structure

This is a Claude Code **plugin marketplace**.

\```
.claude-plugin/marketplace.json    -- marketplace catalog (points to plugins)
plugins/<name>/                    -- plugin root (one per plugin)
  .claude-plugin/plugin.json       -- plugin manifest
  skills/<skill-name>/SKILL.md     -- skill definitions
\```

## Key References

- Plugin Marketplaces: https://code.claude.com/docs/en/plugin-marketplaces
- Creating Plugins: https://code.claude.com/docs/en/plugins
- Plugins Reference (schemas, validation, caching): https://code.claude.com/docs/en/plugins-reference
- Skills Reference (frontmatter fields): https://code.claude.com/docs/en/skills

## Rules

### Marketplace Structure
- marketplace.json goes in `.claude-plugin/` at the repo root. It may only contain `name`, `owner`, `plugins`, and optional `metadata` fields. Do NOT add `$schema` — the validator rejects unrecognized keys.
- Each plugin lives in its own directory under `plugins/`. The plugin's `.claude-plugin/plugin.json` goes inside that directory, NOT at the repo root.
- Skills, agents, commands, and hooks go at the **plugin root** level, NOT inside `.claude-plugin/`.
- Validate with: `claude plugin validate .` or `/plugin validate .`

### plugin.json
- Keep it minimal: `name`, `description`, `version`, `author`. That's it for most plugins.
- `repository` must be a **string** (URL), not an object.
- `name` determines the skill namespace (e.g., plugin name `my-plugin` + skill name `my-skill` = `/my-plugin:my-skill`).

### marketplace.json
- The marketplace `name` is the brand (`<marketplace-name>`). The plugin entry `name` is the install identifier.
- Install command: `/plugin install <plugin-name>@<marketplace-name>`
- Plugin `source` for local plugins must start with `./` and is relative to the repo root.
- Set `version` in the marketplace entry OR plugin.json, not both. Plugin.json wins silently.
- Optional useful fields on plugin entries: `category`, `homepage`, `license`, `keywords`.

### SKILL.md
- Frontmatter uses **hyphens** (e.g., `user-invocable`, `disable-model-invocation`), not underscores.
- `user-invocable: true` (default) makes the skill appear in the `/` menu.
- `disable-model-invocation: true` prevents Claude from auto-triggering the skill.
```

### Step 3: Import existing skill (if provided)

If the user provided a skill to import:

1. Create `plugins/<plugin-name>/.claude-plugin/plugin.json`:

```json
{
  "name": "<plugin-name>",
  "description": "<from imported skill>",
  "version": "0.1.0",
  "author": {
    "name": "<owner-name>"
  }
}
```

2. Copy the SKILL.md into `plugins/<plugin-name>/skills/<skill-name>/SKILL.md`
   - If the source was already a full plugin directory, copy its entire structure into `plugins/<plugin-name>/`
   - Preserve the original SKILL.md content exactly — do not modify it

---

## Phase 3: Validate and Summarize (you do this)

After creating all files:

1. **Run validation**: `claude plugin validate .`
   - If validation fails, fix the issues and re-validate
   - If `claude` CLI is not available, skip and tell the user to validate manually

2. **Present a summary**:

```markdown
## Marketplace Created

**<marketplace-name>** is ready.

### Files created:
- `.claude-plugin/marketplace.json` — marketplace catalog
- `CLAUDE.md` — project conventions (created/updated)
- `plugins/<plugin-name>/...` — imported plugin (if applicable)

### Next steps:
1. Push this repo to GitHub
2. Others install plugins with: `/plugin install <plugin-name>@<marketplace-name>`
3. Add more plugins by creating new directories under `plugins/`
4. Validate anytime with: `claude plugin validate .` or `/plugin validate .`
```

3. **Offer to continue:**

> Would you like to add a plugin to this marketplace?

---

## Guidelines

- **Be conversational.** This is an interactive scaffolding tool. Ask questions one at a time, don't dump a wall of prompts.
- **Respect existing files.** Never overwrite without asking. Append to CLAUDE.md rather than replacing it.
- **No `$schema` in marketplace.json.** The validator rejects unrecognized keys. Only use `name`, `owner`, `plugins`, and optional `metadata`.
- **Source paths must start with `./`** — relative to repo root.
- **Names are lowercase, hyphen-separated.** Normalize silently but tell the user what you changed.
- **Version starts at 0.1.0** for new plugins. Only the marketplace entry OR plugin.json should set the version, not both.
- **Keep plugin.json minimal.** Don't add fields that aren't needed yet.
- **SKILL.md frontmatter uses hyphens**, not underscores. This is a common mistake — always double-check.
- **Default to empty marketplace.** Don't pressure the user to import a skill. An empty marketplace is a perfectly valid starting point.
