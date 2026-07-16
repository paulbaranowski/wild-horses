---
name: babysit
description: Watch a PR through CI and review feedback. Auto-fix high-confidence failures and address review comments. Use when the user says /wild-pr:babysit, babysit a PR, or respond to PR comments.
user-invocable: true
disable-model-invocation: true
argument-hint: "[pr-number-or-url]"
---

# babysit — watch CI and review feedback

Read and execute `${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT}}/commands/babysit.md` in full. Treat `$ARGUMENTS` as the optional PR number or URL for the Setup parser.
