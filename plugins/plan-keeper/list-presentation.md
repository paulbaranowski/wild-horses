# Numbered list presentation

How the `plan-*` skills print a numbered inventory to the user. The CLI emits the list. Each skill pastes that stdout.

This file is the single source of truth, like [repo-derivation.md](repo-derivation.md) and [plan-kinds.md](plan-kinds.md).

## The rule

- Run `list --sections <groups>` for a grouped inventory, or `list --present` for a flat one.
- Paste stdout as-is. Do not rebuild headings or rows.
- You may add one context line above the block and a pick prompt below it.

`--sections` takes comma-separated Status values in display order. Empty groups are omitted. Row numbers stay one continuous count. `in-progress` prints as `## In progress`.

`--status` still prints `status<TAB>filename`. Do not use it for chat output.

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
