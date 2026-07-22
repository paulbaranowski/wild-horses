---
description: Write pull-request descriptions and titles that lead with the one structural idea, not a file-by-file changelog. Use when about to write or revise a PR description or title (before `gh pr create`/`gh pr edit`, or when a repo squashes with the PR body as the commit message), or when an existing description reads like a changelog and needs rewriting.
argument-hint: "[PR number or branch, optional]"
---

# /wild-pr:summary-writer - architecture-first PR description and title

Read and execute `${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT}}/skills/summary-writer/SKILL.md` in full. Treat `$ARGUMENTS` as the optional PR number or branch for that skill's Delivery step 1.
