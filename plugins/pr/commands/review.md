---
description: Code review of a diff, branch, or PR, with findings posted as anchored PR comments. Use when the user asks to review a diff, branch, or PR, asks to check a change against its ticket/spec/PRD, or runs /pr:review [pr-number-or-url] [--effort low|high] [--report].
argument-hint: "[pr-number-or-url] [--effort low|high] [--report]"
---

# /pr:review - code review a diff, branch, or PR

Read and execute `${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT}}/skills/review/SKILL.md` in full, following its reference files under `skills/review/references/`. Treat `$ARGUMENTS` as the optional `[pr-number-or-url] [--effort low|high] [--report]` for that skill's Invocation parsing.
