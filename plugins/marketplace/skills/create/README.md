# create

Scaffold a Claude Code plugin marketplace with proper structure, schema validation, and `CLAUDE.md` conventions.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

```text
/create
/create my-marketplace
```

Also model-invoked - trigger phrases include "starting a new marketplace repo" or "adding marketplace structure to an existing repo".

## What it does

1. **Gathers information interactively** (one question at a time): the marketplace name (normalized to lowercase-hyphenated), the owner name (from `git config user.name` if available), and whether to import an existing `SKILL.md` or plugin directory. Warns before overwriting an existing `marketplace.json`, and appends to an existing `CLAUDE.md` rather than replacing it.
2. **Confirms the plan** - name, owner, what's being imported, and the exact files about to be created - before writing anything.
3. **Creates the structure**: `.claude-plugin/marketplace.json`, a `CLAUDE.md` (new file, appended block, or skipped if already configured), and - if a skill/plugin was imported - the `plugins/<name>/` tree with its manifest.
4. **Validates** with `claude plugin validate .` and fixes any issue it finds, then presents a summary of what was created and the next steps (push to GitHub, install command, how to add more plugins/skills).

## Install

The skill ships with the `marketplace` plugin:

```text
/plugin install marketplace@wild-horses
```
