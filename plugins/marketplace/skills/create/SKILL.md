---
name: create
description: Scaffold a Claude Code plugin marketplace with proper structure, schema validation, and CLAUDE.md conventions. Asks for the marketplace name and whether to import an existing skill. Use when starting a new marketplace repo or adding marketplace structure to an existing repo.
user-invocable: true
disable-model-invocation: true
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

```text
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
  "author": {
    "name": "<owner-name>"
  },
  "category": "development",
  "license": "MIT"
}
```

### Step 2: Create or update CLAUDE.md

Read CLAUDE.md if it exists. Then choose one of three paths:

**Path A — No CLAUDE.md exists:** Create it with the full template below.

**Path B — CLAUDE.md exists but has no `<!-- marketplace: <marketplace-name> -->` delimiter:** Append only the marketplace section block (the delimited block from the append template below) to the end of the existing file.

**Path C — CLAUDE.md already contains `<!-- marketplace: <marketplace-name> -->` :** The marketplace block was already added (previous run). Do nothing — skip this step and tell the user CLAUDE.md is already configured.

#### Full template (Path A — new file)

```markdown
# <Marketplace Display Name> - Claude Code Plugin Marketplace

## Project Structure

This is a Claude Code **plugin marketplace**.

\```
.claude-plugin/marketplace.json    -- marketplace catalog (points to plugins)
plugins/<plugin-name>/             -- plugin root (one per plugin)
  .claude-plugin/plugin.json       -- plugin manifest
  skills/<skill-a>/SKILL.md        -- a plugin can contain multiple skills
  skills/<skill-b>/SKILL.md        -- each skill gets its own directory
\```

## Key References

- Plugin Marketplaces: https://code.claude.com/docs/en/plugin-marketplaces
- Creating Plugins: https://code.claude.com/docs/en/plugins
- Plugins Reference (schemas, validation, caching): https://code.claude.com/docs/en/plugins-reference
- Skills Reference (frontmatter fields): https://code.claude.com/docs/en/skills

<!-- marketplace: <marketplace-name> -->

## Marketplace Rules

### marketplace.json
- marketplace.json goes in `.claude-plugin/` at the repo root. It may only contain `name`, `owner`, `plugins`, and optional `metadata` fields. Do NOT add `$schema` — the validator rejects unrecognized keys.
- The marketplace `name` is the brand (`<marketplace-name>`). The plugin entry `name` is the install identifier.
- Install command: `/plugin install <plugin-name>@<marketplace-name>`
- Plugin `source` for local plugins must start with `./` and is relative to the repo root.
- Optional useful fields on plugin entries: `category`, `homepage`, `license`, `keywords`.

### plugin.json
- Keep it minimal: `name`, `description`, `version`, `author`. That's it for most plugins.
- Set `version` in plugin.json only — do not duplicate it in the marketplace entry.
- `repository` must be a **string** (URL), not an object.
- `name` determines the skill namespace. A plugin can contain **multiple skills** — each becomes `/plugin-name:skill-name` (e.g., plugin `harness` with skills `audit`, `setup`, `reasoning-gaps` → `/harness:audit`, `/harness:setup`, `/harness:reasoning-gaps`).
- Group related skills under one plugin rather than creating one plugin per skill. This is the established convention (see Anthropic's `plugin-dev`, `feature-dev`).

### SKILL.md
- Frontmatter uses **hyphens** (e.g., `user-invocable`, `disable-model-invocation`), not underscores.
- `user-invocable: true` (default) makes the skill appear in the `/` menu.
- `disable-model-invocation: true` prevents Claude from auto-triggering the skill.
- Each plugin lives in its own directory under `plugins/`. The plugin's `.claude-plugin/plugin.json` goes inside that directory, NOT at the repo root.
- Skills, agents, commands, and hooks go at the **plugin root** level, NOT inside `.claude-plugin/`.
- A plugin can have multiple skills: `plugins/<plugin>/skills/<skill-a>/SKILL.md`, `plugins/<plugin>/skills/<skill-b>/SKILL.md`, etc.
- Validate with: `claude plugin validate .` or `/plugin validate .`

<!-- /marketplace: <marketplace-name> -->
```

#### Append template (Path B — existing file)

Append this block to the end of the existing CLAUDE.md:

```markdown

<!-- marketplace: <marketplace-name> -->

## Marketplace: <Marketplace Display Name>

This repo is a Claude Code **plugin marketplace** (`<marketplace-name>`).

### marketplace.json
- marketplace.json goes in `.claude-plugin/` at the repo root. It may only contain `name`, `owner`, `plugins`, and optional `metadata` fields. Do NOT add `$schema` — the validator rejects unrecognized keys.
- The marketplace `name` is the brand (`<marketplace-name>`). The plugin entry `name` is the install identifier.
- Install command: `/plugin install <plugin-name>@<marketplace-name>`
- Plugin `source` for local plugins must start with `./` and is relative to the repo root.
- Optional useful fields on plugin entries: `category`, `homepage`, `license`, `keywords`.

### plugin.json
- Keep it minimal: `name`, `description`, `version`, `author`.
- Set `version` in plugin.json only — do not duplicate it in the marketplace entry.
- `repository` must be a **string** (URL), not an object.
- `name` determines the skill namespace. A plugin can contain **multiple skills** — each becomes `/plugin-name:skill-name` (e.g., plugin `harness` with skills `audit`, `setup` → `/harness:audit`, `/harness:setup`).
- Group related skills under one plugin rather than creating one plugin per skill.

### SKILL.md
- Frontmatter uses **hyphens** (e.g., `user-invocable`, `disable-model-invocation`), not underscores.
- Each plugin lives in its own directory under `plugins/`, NOT at the repo root.
- Skills go at the **plugin root** level, NOT inside `.claude-plugin/`.
- A plugin can have multiple skills under `skills/`.
- Validate with: `claude plugin validate .` or `/plugin validate .`

<!-- /marketplace: <marketplace-name> -->
```

### Step 3: Import existing skill (if provided)

If the user provided a skill to import:

1. **Determine the plugin name.** Ask the user what plugin this skill belongs to. Related skills should be grouped under one plugin (e.g., `audit`, `setup`, and `reasoning-gaps` all under a `harness` plugin → `/harness:audit`, `/harness:setup`, `/harness:reasoning-gaps`).

2. **Create the plugin** (if it doesn't exist yet). Create `plugins/<plugin-name>/.claude-plugin/plugin.json`:

```json
{
  "name": "<plugin-name>",
  "description": "<from imported skill — or broader description if multiple skills planned>",
  "version": "0.1.0",
  "author": {
    "name": "<owner-name>"
  }
}
```

3. **Add the skill** to `plugins/<plugin-name>/skills/<skill-name>/SKILL.md`
   - The skill name becomes the second part of the slash command: `/plugin-name:skill-name`
   - If the source was already a full plugin directory, copy its entire structure into `plugins/<plugin-name>/`
   - Preserve the original SKILL.md content exactly — do not modify it
   - If adding to an existing plugin, just create the new `skills/<skill-name>/` directory

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
4. Add more skills to an existing plugin by creating new directories under `plugins/<plugin-name>/skills/`
5. Validate anytime with: `claude plugin validate .` or `/plugin validate .`
```

3. **Offer to continue:**

> Would you like to add a plugin or a skill to this marketplace?

---

## Guidelines

- **Be conversational.** This is an interactive scaffolding tool. Ask questions one at a time, don't dump a wall of prompts.
- **Respect existing files.** Never overwrite without asking. Append to CLAUDE.md rather than replacing it.
- **No `$schema` in marketplace.json.** The validator rejects unrecognized keys. Only use `name`, `owner`, `plugins`, and optional `metadata`.
- **Source paths must start with `./`** — relative to repo root.
- **Names are lowercase, hyphen-separated.** Normalize silently but tell the user what you changed.
- **Version starts at 0.1.0** for new plugins. Set version in plugin.json only.
- **Keep plugin.json minimal.** Don't add fields that aren't needed yet.
- **SKILL.md frontmatter uses hyphens**, not underscores. This is a common mistake — always double-check.
- **Group related skills under one plugin.** Don't create one plugin per skill. Related skills belong in the same plugin (e.g., `harness` with `audit`, `setup`, `reasoning-gaps`). This matches the convention in Anthropic's official marketplace.
- **Default to empty marketplace.** Don't pressure the user to import a skill. An empty marketplace is a perfectly valid starting point.
