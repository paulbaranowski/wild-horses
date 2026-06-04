# wild-horses

A [Claude Code](https://claude.ai/code) plugin marketplace for making code AI-readable and agent-friendly.

## Overview

Three questions an AI agent has to be able to answer before it can edit your code reliably:

1. _Does the type layer tell the truth?_ — answered by `/pyright:run-and-fix` (Python only).
2. _If an AI agent read this code, what would it get wrong?_ — answered by `/harness:reasoning-gaps`.
3. _Can an AI edit this code and know whether it got it right?_ — answered by `/harness:feedback-blockers`.

Run them in that order on a PR or feature branch. Each step asks a harder question than the last: types, then comprehension, then verification.

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
