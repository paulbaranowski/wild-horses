# wild-horses

A [Claude Code](https://claude.ai/code) plugin marketplace of tools for working on code with AI agents — making code agent-readable, planning and shipping work, and smoothing the day-to-day agent workflow.

## Overview

The plugins group into four themes, following the lifecycle of working on code with an agent. Each one-liner below links to the full plugin entry further down.

**Make code agent-readable** — can an agent understand your code and safely edit it?

- **[pyright](#pyright)** — run pyright on a Python codebase and fix what it finds, using a documented playbook of fix patterns.
- **[harness](#harness)** — diagnose where an agent would misread your code (`/harness:reasoning-gaps`) or couldn't tell whether it succeeded (`/harness:feedback-blockers`), then build, run, and inspect a remediation task list.
- **[linting-hooks](#linting-hooks)** — auto-lint Markdown and Python the moment Claude edits them.

Run the first three in order on a PR or feature branch — types (`/pyright:run-and-fix`), then comprehension (`/harness:reasoning-gaps`), then verification (`/harness:feedback-blockers`). Each asks a harder question than the last.

**Plan and ship work** — turn an idea into a merged PR.

- **[plan-keeper](#plan-keeper)** — save, route, and archive markdown plans in `~/plans/<repo>/` (also a standalone Homebrew CLI).
- **[autonomous](#autonomous)** — drive a single issue or plan file all the way to an opened PR, with no human in the loop.
- **[steelman](#steelman)** — argue the strongest good-faith case _against_ a plan before you commit to it.

**Understand and scaffold** — see what's there, or stand up something new.

- **[codepath-visualizer](#codepath-visualizer)** — map a codebase's call chains into an interactive architecture diagram.
- **[marketplace](#marketplace)** — scaffold a new Claude Code plugin marketplace with proper structure and schema.

**Smooth the agent workflow** — quality-of-life hooks and utilities.

- **[update-git-repos](#update-git-repos)** — pull every configured git repo from `origin` in one shot.
- **[yes-no-questions-hook](#yes-no-questions-hook)** — nudge the agent to pose decisions as numbered yes/no questions.
- **[pr-status-hook](#pr-status-hook)** — report PR / push / dirty-tree state at the end of every turn.

## Plugins

### [pyright](plugins/pyright/README.md)

Run pyright on a Python codebase and fix what it finds, using a documented playbook of fix patterns instead of ad-hoc guesses. Three fix intents (`silence`, `improve`, `bugs-only`) shape how aggressively to refactor. Parallelizes across agents for codebases with ≥20 errors. Hands off cleanly to `/harness:reasoning-gaps`.

```text
/plugin install pyright@wild-horses

/pyright:run-and-fix
/pyright:run-and-fix strict --persist
/pyright:run-and-fix --scope src/workers/ --intent improve
```

See **[plugins/pyright/README.md](plugins/pyright/README.md)** for fix-intent semantics, the rule/library/bug pattern files, and ratchet/persist flags.

### [harness](plugins/harness/README.md)

Two commands plus a task-list pipeline for making code agent-friendly. The commands diagnose reasoning gaps and feedback-loop blockers; the task-list skills (`task-list-builder`, `task-list-runner`, `task-list-viewer`) produce, execute, and inspect the resulting remediation plans.

```text
/plugin install harness@wild-horses

/harness:reasoning-gaps              # comprehension review
/harness:feedback-blockers           # observability review
/task-list-builder                   # build an implementation plan
/task-list-runner --all              # drive an implementation plan to completion
/task-list-viewer                    # inspect what's left
```

See **[plugins/harness/README.md](plugins/harness/README.md)** for what each command analyzes, the task-list pipeline, and a comparison with the [superpowers](https://github.com/obra/superpowers) plan skills.

### [linting-hooks](plugins/linting-hooks/README.md)

PostToolUse hooks that lint Markdown and Python files immediately after Claude edits them — `prettier` + `markdownlint-cli2` on `.md`, `pyright` on `.py`. Both are non-blocking. Hook registration is automatic; `/linting-hooks:install` handles the per-machine software.

```text
/plugin install linting-hooks@wild-horses
/linting-hooks:install
```

See **[plugins/linting-hooks/README.md](plugins/linting-hooks/README.md)** for the bundled hooks and install behavior.

### [marketplace](plugins/marketplace/README.md)

Scaffold a new Claude Code plugin marketplace with proper structure, schema validation, and `CLAUDE.md` conventions. Generates `marketplace.json`, `plugin.json`, and a starter `CLAUDE.md` interactively.

```text
/plugin install marketplace@wild-horses
/create
/create my-marketplace
```

See **[plugins/marketplace/README.md](plugins/marketplace/README.md)** for the scaffolding flow.

### [codepath-visualizer](plugins/codepath-visualizer/README.md)

Map and visualize codepaths in any codebase as an interactive architecture diagram. `/codepath-mapper` walks entry points and extracts call chains into a structured JSON file; `/codepath-visualizer` renders the resulting graph as an interactive HTML diagram you can explore in the browser. Scope the mapper to a user-facing flow (e.g. "invite new user") to produce a focused diagram of just that path.

```text
/plugin install codepath-visualizer@wild-horses

/codepath-mapper
/codepath-mapper "invite new user"
/codepath-visualizer
/codepath-visualizer --select
```

See **[plugins/codepath-visualizer/README.md](plugins/codepath-visualizer/README.md)** for the JSON schema, scoping behavior, and rendering options.

### [plan-keeper](plugins/plan-keeper/README.md)

Three skills for organizing markdown plans in `~/plans/<repo>/`. `plan-save` captures the latest plan from the current conversation into a dated file; `plan-do` lists saved plans and routes the picked one to the right next skill (brainstorming / writing-plans / executing-plans / task-list-builder) based on whether it reads as an idea, spec, sequential impl plan, or task-list-shaped plan; `plan-done` archives a completed plan into `~/plans/<repo>/done/` with a completion stamp. All three are model-invoked by description — no slash command required.

```text
/plugin install plan-keeper@wild-horses

"save this plan"
"do a plan from herds"
"I'm done with the plan"
```

See **[plugins/plan-keeper/README.md](plugins/plan-keeper/README.md)** for the three-skill pipeline, the shared `~/plans/<repo>/` tree, and the bundled CLI.

### [autonomous](plugins/autonomous/README.md)

Drive a single task — an issue/ticket link or a plan/spec file — all the way to an opened pull request, with no human in the loop. Hand it a Linear/GitHub issue URL, a path to a plan file, or a plan already in the conversation, and it implements, tests, runs an independent sub-agent review to convergence, opens a PR following the target repo's own conventions, and tends it through CI. Ships an autonomy contract (never stop to ask) plus a 10-rule code-style bar.

```text
/plugin install autonomous@wild-horses

/autonomous https://linear.app/.../ISSUE-123
/autonomous ~/plans/myrepo/feature.md
"work this issue autonomously"          # model-invoked
```

See **[plugins/autonomous/README.md](plugins/autonomous/README.md)** for the autonomy contract, the code-style bar, and the review-to-convergence loop.

### [steelman](plugins/steelman)

Argue the strongest good-faith case _against_ the proposed changes in the current conversation or a named design/plan file — hidden costs, wrong assumptions, simpler alternatives, second-order effects, and the do-nothing option. A built-in red-team voice that stress-tests a plan before it ships.

```text
/plugin install steelman@wild-horses

/steelman                               # red-team the proposal in the conversation
/steelman path/to/design.md             # red-team a specific file
```

### [update-git-repos](plugins/update-git-repos/README.md)

Pull every configured git repo from `origin/<branch>` in one shot. Maintains a repo list at `~/.config/wild-horses/update-git-repos/repos.json`, supports bootstrap auto-discovery under a root directory and manual add/remove, and applies a configurable dirty-tree action (`ask` / `skip` / `stash`) per repo. Uses `git merge --ff-only` so diverged histories never auto-merge silently. Backed by a bundled CLI with a PreToolUse hook that auto-approves its invocations.

```text
/plugin install update-git-repos@wild-horses

/update-git-repos                       # pull every configured repo
"update all my git repos"               # model-invoked
```

See **[plugins/update-git-repos/README.md](plugins/update-git-repos/README.md)** for the config schema, bootstrap discovery, and dirty-tree action resolution.

### [yes-no-questions-hook](plugins/yes-no-questions-hook)

A `UserPromptSubmit` hook that injects a per-turn reminder to pose decision questions as numbered yes/no questions — collapsing every either/or into a single yes/no rather than an inline "X, or Y?" or a pick-one menu. A portable, shareable restatement of a personal `CLAUDE.md` rule. No command — it fires automatically once installed.

```text
/plugin install yes-no-questions-hook@wild-horses
```

### [pr-status-hook](plugins/pr-status-hook)

A `Stop` hook that reports, at every turn-end, whether an open PR exists for the current branch (with its link), whether the last commits were actually pushed, and whether the working tree is dirty — all computed from real `git`/`gh` state, never from memory. Stays silent unless there is something worth reporting, and exits early on non-repos, detached HEAD, and default branches. No command — it fires automatically once installed.

```text
/plugin install pr-status-hook@wild-horses
```

## Standalone CLI: `plan-keeper`

The I/O backend behind the plan-keeper skills — `plan_keeper_cli.py`, a zero-dependency stdlib tool that manages the `~/plans/<repo>/` tree (save, list, archive, frontmatter, and Linear/Jira push) — is also distributed as a standalone command-line tool via Homebrew, for working with your plans outside an agent session:

```text
brew install paulbaranowski/tap/plan-keeper

plan-keeper list                     # active plans for the current repo
plan-keeper repo list                # every repo under ~/plans/ with counts
plan-keeper save --topic "spike notes" <<'EOF'
...plan body...
EOF
plan-keeper --help                   # all subcommands
```

It is the same source file the plan-keeper plugin invokes in-place — packaged from `plugins/plan-keeper/scripts/` with no second copy to drift. Both the plugin's skills and this CLI read and write the same `~/plans/<repo>/` tree, so they interoperate directly.

## Install

1. Run `/plugin` in Claude Code
2. Select **Marketplaces**
3. Select **Add marketplace**
4. Enter `paulbaranowski/wild-horses`

## License

MIT
