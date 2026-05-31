# plan-keeper

Organize markdown plans on disk across repos. Six skills cover the lifecycle: capture from conversation (`plan-save`), pick up and route to the next step (`plan-do`), archive with a completion stamp (`plan-done`), edit frontmatter (`plan-update`), manage the cross-repo dispatch queue (`plan-queue`), and file plans as tickets (`plan-push`). All share a bundled CLI and a `~/plans/<repo>/` tree that's local to your machine — nothing is committed to any repo.

Install:

```text
/plugin install plan-keeper@wild-horses
```

## Skills

| Skill                                    | Role     | What it does                                                                                                                                                                             |
| ---------------------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **[`plan-save`](skills/plan-save/)**     | Captures | Writes the latest plan from the current conversation to `~/plans/<repo>/<YYYY-MM-DD>-<topic>.md`.                                                                                        |
| **[`plan-do`](skills/plan-do/)**         | Routes   | Lists active plans for the current repo, classifies the picked one (idea / spec / sequential impl / task-list-shaped), and invokes the matching next skill.                              |
| **[`plan-done`](skills/plan-done/)**     | Archives | Moves a completed plan to `~/plans/<repo>/done/` and appends a `*Completed: YYYY-MM-DD*` stamp.                                                                                          |
| **[`plan-update`](skills/plan-update/)** | Edits    | Mutates frontmatter fields (`Agent`, `Status`, `Ticket`) for a single plan in the current repo.                                                                                          |
| **[`plan-queue`](skills/plan-queue/)**   | Queues   | Shows the groundcrew dispatch queue across all repos and bulk-promotes/dequeues plans (`Status todo/backlog`). Cross-repo, multi-select. The bulk/cross-repo counterpart to plan-update. |
| **[`plan-push`](skills/plan-push/)**     | Files    | Files the plan as a Linear or Jira ticket and stamps `Ticket:` in frontmatter.                                                                                                           |

All skills are model-invoked by description — no slash command is required. Trigger phrases like "save this plan", "do a plan from `<name>`", or "I'm done with the plan" route Claude into the right skill.

## How the pieces fit

```text
conversation ──► plan-save ──► ~/plans/<repo>/*.md ──► plan-do ──► (next skill)
                                                                   ├─► superpowers:brainstorming      (idea)
                                                                   ├─► superpowers:writing-plans      (spec)
                                                                   ├─► superpowers:executing-plans    (sequential impl)
                                                                   └─► harness:task-list-builder      (task-list-shaped)

                                                       plan-done ──► ~/plans/<repo>/done/<file>.md
```

`plan-do` is the entry point that joins the [superpowers](https://github.com/obra/superpowers) brainstorming → writing-plans → executing-plans pipeline (or the [harness task-list-builder](../harness/skills/task-list-builder/)) at the right stage based on the plan's shape — independence of work units is the discriminator between sequential and task-list-shaped plans, not vocabulary.

## Repo derivation

`<repo>` auto-derives from `git remote get-url origin` (or `basename $PWD` as a fallback). Each skill also accepts an explicit override phrased naturally — "save the plan to `herds`", "do a plan from `general`", "archive the plan in `scratch`". Full algorithm: [`repo-derivation.md`](repo-derivation.md).

The override and auto-derive paths normalize differently: auto-derived names are kept verbatim (so `herds_mobile_app` stays `herds_mobile_app`), but user-typed overrides are lowercased with whitespace-to-hyphen ("General Folder" → `general-folder`). The asymmetry is deliberate — a git remote name is already canonical, but a user-typed phrase usually isn't.

## The bundled CLI

`scripts/plan_keeper_cli.py` is the canonical interface for all the skills — the skills never write to `~/plans/` directly. Subcommands: `repo`, `list`, `list-repos`, `save`, `archive`. Mutations are atomic (tmp file + `fsync` + `os.replace`), and collisions surface as a structured exit-2 signal that the skills present to the user rather than treating as a fatal error.

A PreToolUse hook (`hooks/hooks.json`) auto-approves `python3 .../plan_keeper_cli.py` Bash invocations so each skill's flow runs without per-call permission prompts. The allow script anchors on the plugin-specific path so a stray `plan_keeper_cli.py` elsewhere in the workspace won't be auto-approved.

## Guardrails

- **Local-only.** `~/plans/` lives on your machine. Nothing is staged, committed, or pushed to any repo.
- **No silent overwrites.** Collisions on save or archive surface as a structured exit-2 error; the skill asks whether to overwrite, suffix `-2`, or pick a new name.
- **`plan-do` is read-only.** Only `plan-save` (creates) and `plan-done` (moves) mutate the tree.
- **Confirmation before mutating.** `plan-done` always shows source/destination paths and asks before invoking the CLI.
- **Empty-repo isolation.** When the current repo has no active plans, `plan-do` and `plan-done` say so and stop — they do not silently fall back to a different repo's folder.

## Files in this plugin

| Path                               | Purpose                                                       |
| ---------------------------------- | ------------------------------------------------------------- |
| `skills/plan-save/SKILL.md`        | Instructions for the save flow                                |
| `skills/plan-do/SKILL.md`          | Instructions for the list-and-route flow                      |
| `skills/plan-done/SKILL.md`        | Instructions for the archive flow                             |
| `scripts/plan_keeper_cli.py`       | Bundled CLI — the only sanctioned mutator for `~/plans/`      |
| `scripts/test_plan_keeper_cli.py`  | Pytest suite for the CLI                                      |
| `scripts/plan-keeper-cli-allow.sh` | PreToolUse hook script — auto-approves CLI Bash invocations   |
| `hooks/hooks.json`                 | PreToolUse hook registration                                  |
| `repo-derivation.md`               | Shared algorithm — auto-derive + override normalization rules |

Run the CLI tests from the repo root:

```text
uv run pytest plugins/plan-keeper/scripts/test_plan_keeper_cli.py
```
