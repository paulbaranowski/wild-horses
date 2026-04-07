# Consolidate all three plugins into single `harness` plugin

## Context

Three separate plugins (`harness-review`, `harness-setup`, `reasoning-gaps`) produce verbose, redundant slash commands (`/harness-review:harness-review`, `/harness-setup:harness-setup`, `/reasoning-gaps:reasoning-gaps`). All three serve the same domain: making code AI-ready.

Merging them into a single `harness` plugin with renamed skills gives:
- `/harness:audit` (was harness-review) — check if AI can validate its own work against your code
- `/harness:setup` (was harness-setup) — scaffold the harness directory structure
- `/harness:reasoning-gaps` (was reasoning-gaps) — find where AI struggles to reason about the code

"review" → "audit" because the skill's purpose is allowing AI to validate what it does (feedback loops, test seams, error locality), not a generic code review.

This is a structural rename — no skill logic changes.

## Plan

### 1. Create new plugin directory and plugin.json

Create `plugins/harness/.claude-plugin/plugin.json`:
```json
{
  "name": "harness",
  "description": "Harness engineering tools: audit code for AI-validatability, find AI reasoning gaps, and set up the harness directory structure.",
  "version": "2.0.0",
  "author": {
    "name": "Paul Baranowski"
  }
}
```

Version 2.0.0 because the slash commands are a breaking change.

### 2. Move SKILL.md files to new locations

- `plugins/harness-review/skills/harness-review/SKILL.md` → `plugins/harness/skills/audit/SKILL.md`
- `plugins/harness-setup/skills/harness-setup/SKILL.md` → `plugins/harness/skills/setup/SKILL.md`
- `plugins/reasoning-gaps/skills/reasoning-gaps/SKILL.md` → `plugins/harness/skills/reasoning-gaps/SKILL.md`

### 3. Update SKILL.md frontmatter (`name` field only)

- **audit/SKILL.md** line 2: `name: harness-review` → `name: audit`
- **setup/SKILL.md** line 2: `name: harness-setup` → `name: setup`
- **reasoning-gaps/SKILL.md** line 2: `name: reasoning-gaps` → no change needed (already correct)

### 4. Fix cross-reference inside setup SKILL.md

- Line 270: `Run '/harness-review'` → `Run '/harness:audit'`

### 5. Delete old plugin directories

Remove all three:
- `plugins/harness-review/`
- `plugins/harness-setup/`
- `plugins/reasoning-gaps/`

### 6. Update `marketplace.json`

Replace the three separate plugin entries with one `harness` entry:
```json
{
  "name": "harness",
  "source": "./plugins/harness",
  "description": "Harness engineering tools: audit code for AI-validatability, find AI reasoning gaps, and set up the harness directory structure.",
  "version": "2.0.0",
  "author": { "name": "Paul Baranowski" },
  "category": "development",
  "license": "MIT"
}
```

Keep `marketplace-create` as a separate plugin (different domain).

### 7. Update `CLAUDE.md`

| Location | Current | New |
|----------|---------|-----|
| Line 5 | `containing the 'harness-review' plugin` | `containing harness engineering plugins` |
| Lines 7-12 | Structure block shows only harness-review | Show `plugins/harness/` with `skills/audit/`, `skills/setup/`, and `skills/reasoning-gaps/` |
| Line 32 | `plugin name 'harness-review' + skill name 'harness-review' = '/harness-review:harness-review'` | `plugin name 'harness' + skill name 'audit' = '/harness:audit'` |
| Line 35 | `install identifier ('harness-review')` | `install identifier ('harness')` |
| Line 36 | `/plugin install harness-review@wild-horses` | `/plugin install harness@wild-horses` |

### 8. Update `README.md`

- `### harness-review` → `### harness:audit`
- Usage examples: `/wild-horses:harness-review` → `/harness:audit`
- `### reasoning-gaps` section: update usage examples from `/reasoning-gaps:reasoning-gaps` → `/harness:reasoning-gaps`
- Add a `### harness:setup` section (currently undocumented)

## Files modified

- `plugins/harness/.claude-plugin/plugin.json` (new)
- `plugins/harness/skills/audit/SKILL.md` (moved from harness-review, frontmatter name changed)
- `plugins/harness/skills/setup/SKILL.md` (moved from harness-setup, frontmatter name changed, line 270 cross-ref fixed)
- `plugins/harness/skills/reasoning-gaps/SKILL.md` (moved from reasoning-gaps plugin, name unchanged)
- `plugins/harness-review/` (deleted)
- `plugins/harness-setup/` (deleted)
- `plugins/reasoning-gaps/` (deleted)
- `.claude-plugin/marketplace.json`
- `CLAUDE.md`
- `README.md`

## What stays unchanged

- All skill logic/content in all three SKILL.md files (only `name` frontmatter and one cross-reference change)
- Plan filename patterns (`YYYY-MM-DD-harness-review-...`, `YYYY-MM-DD-reasoning-gaps-...`) — these are output filenames in the user's repo, keeping the prefix descriptive is fine
- `marketplace-create` plugin — untouched (different domain)

## Verification

1. Run `claude plugin validate .` to confirm the merged plugin validates
2. Check `/harness:audit`, `/harness:setup`, and `/harness:reasoning-gaps` appear in the `/` menu
3. Confirm old commands no longer appear
