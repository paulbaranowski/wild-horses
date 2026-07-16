---
description: Use when about to write or revise a pull-request description or title - before running `gh pr create` or `gh pr edit`, when asked to write or update a PR body, or when a repo squashes with the PR body as the commit message. Also use when an existing description reads like a changelog (file-by-file bullets, acceptance-criteria checkboxes, review-round logs) and needs rewriting.
argument-hint: "[PR number or branch, optional]"
---

# /wild-pr:summary-writer - architecture-first PR description and title

Read and execute `${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT}}/skills/summary-writer/SKILL.md` in full. Treat `$ARGUMENTS` as the optional PR number or branch for that skill's Delivery step 1.
