---
description: Diagnose why a PR keeps going through review rounds without converging, then recommend one way forward - keep patching, refactor in this PR, split the PR, restart with new requirements, or move the change to the backend, frontend, external API, or library that should own it. Use when a PR has had many review rounds, fixes keep producing new findings, or the user asks why a PR won't settle.
argument-hint: "[pr-number-or-url]"
---

# /wild-pr:churn - diagnose a PR that won't converge

Read and execute `${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT}}/skills/churn/SKILL.md` in full, following its reference file `skills/churn/references/lenses.md`. Treat `$ARGUMENTS` as the optional `[pr-number-or-url]` for that skill's Invocation parsing.
