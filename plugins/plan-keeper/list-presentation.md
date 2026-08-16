# Numbered list presentation

How the `plan-*` skills print a numbered inventory to the user. Each listing skill's present step links here instead of restating the rule.

This file is the single source of truth, like [repo-derivation.md](repo-derivation.md) and [plan-kinds.md](plan-kinds.md).

## The rule

- Put a `##` heading before each group of numbered rows.
- Number the rows in one continuous count across groups.
- After `##`, CommonMark allows any start number.

Status-grouped pickers (`plan-do`, `plan-done`, `plan-crew`, `plan-list`) use one `##` heading per group.

Flat pickers (`plan-update`, `plan-linear`, `plan-jira`) use one `## Plans` heading, then `1.` `2.` `3.`

This rule applies to chat output. Instructional numbered steps inside a SKILL.md body stay as they are.

## Why

CommonMark lets an ordered list interrupt a paragraph only when it starts at `1`. A later group under a `Todo:` or `Queued:` label stays in that paragraph. The renderer joins the rows into one blob.

## Grouped example

This is the shape `plan-crew` and `plan-list` emit.

```markdown
## Queued

1. herds · 2026-05-20-fix-auth.md · claude

## Available

3. herds · 2026-05-22-refactor-db.md · (no agent)
```

## Flat example

This is the shape a file picker emits.

```markdown
## Plans

1. 2026-05-19-plan-do-design.md
2. 2026-05-17-task-list-runner-refactor.md
```

## Don't

- **Don't put `N.` with `N` not equal to `1` under a paragraph label.** Use a `##` heading first.
