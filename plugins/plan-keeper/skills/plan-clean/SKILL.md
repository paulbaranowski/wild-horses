---
name: plan-clean
description: >
  Find this repo's complete, partial, irrelevant, and open plans, and archive the complete ones.
  The other plans are listed and left in place.
  Use when the user asks to clean plans, archive complete plans, or find plans that no longer apply.
---

# plan-clean

This skill checks every `backlog`, `todo`, and `in-progress` plan for one repo against the default branch.
It gives each plan one verdict: complete, partial, irrelevant, or open.
It archives the complete plans to `done/` and prints the four lists.
Partial, irrelevant, and open plans stay where they are.

The bundled `plan_keeper_cli.py` does every write. This skill does the judging.

## Calling this skill

- **Input.** The current repo, or one repo the user names. An optional dry-run phrase.
- **Return.** The four lists, with one evidence line per plan. The archived path of each complete plan.
- **Does alone.** Reads every `backlog`, `todo`, and `in-progress` plan. Assigns one verdict each. Archives the complete plans with no second confirm.
- **Never.** Never scans a second repo. Never edits a plan body. Never moves a partial, irrelevant, or open plan. Never archives on a guess.

## Quick reference

- **Scan set:** active plans whose Status is `backlog`, `todo`, or `in-progress`. A missing Status counts as `backlog`.
- **Out of scope:** `in-review` plans, and everything in `done/` and `deferred/`.
- **`<repo>`:** resolved once in step 1 and passed as `--override` to every `list` call. See [../../repo-derivation.md](../../repo-derivation.md).
- **Writes:** `file-meta set --status done` on each complete plan. Nothing else.
- **Pairs:** a `.md` plan and its same-base-name siblings (usually a task-list `.json`) are one plan. The CLI moves the siblings with the plan, byte for byte.
- **Dry run:** "dry run", "just show", or "don't archive yet". The report appears and nothing is written.
- **Kind order:** see [../../plan-kinds.md](../../plan-kinds.md). The order is idea, prd, reqs, design, spec, exec-plan.

## Procedure

Write every CLI call with the literal `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py"` prefix.
The plugin's approval hook matches only that prefix.
A shell variable does not survive to the next Bash call.

### 1. Determine the repo

Look for an explicit repo in the request.
Examples: "plan-clean herds", "clean plans in herds", "clean up the herds plans".
If there is one, normalize it per [../../repo-derivation.md](../../repo-derivation.md). That is `<name>`.

Otherwise run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" repo name
```

Its stdout is `<name>`. If stdout is empty, say so and stop.

Add `--override <name>` to every `list` call below.
Without it, `list` in a directory with no git `origin` lists every repo's plans.

The **checkout** is the git working tree that the verdicts read.
When the user named no repo, the checkout is the current directory.
When the user named a repo, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" repo name` in the current directory.
If it prints `<name>`, the checkout is the current directory.
Otherwise ask the user for the path of a local checkout of `<name>`.
Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" repo name` inside that path.
If it does not print `<name>`, say so and stop.
Never judge one repo's plans against another repo's history.

Run every `git` command in steps 3 and 4 as `git -C <checkout> ...`.
Run every `gh` command with `--repo <owner>/<repo>`, taken from `git -C <checkout> remote get-url origin`.

### 2. List the plans

Run all four listings on every invocation. Plans change between turns.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list --override <name> --status backlog,todo,in-progress
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list --override <name>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list --override <name> --group
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list --override <name> --state done --group
```

- The `--status` call gives the scan set, as `status<TAB>filename` rows.
- The bare call gives every active file, including `.json` siblings and `in-review` plans.
- The two `--group` calls give the active and archived project slugs for the successor check.

`--status` and `--group` cannot combine, so these are separate calls.

If the scan set is empty, say so and stop.
Do not switch to another repo.

Resolve each filename to an absolute path the way `plan-do` step 3 does.
A `root/` prefix names the plan's root; map it with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" root list`.
No prefix means the root with `"default": true`.
Never rebuild the path as `~/plans/<repo>/<filename>`.

Group the bare listing's files by base name (the filename without its last extension).
A group with a `.md` file is one plan.
Keep a group only when its `.md` is in the scan set.
This drops an `in-review` plan together with its `.json`.
A `.json` has no Status, so the `--status` call lists it as `backlog`.
Use the grouping, not that row, to decide its scope.
A group with no `.md` file is open, with the reason "no markdown sibling".

### 3. Resolve the default branch

Resolve the base branch once, in this order:

1. `git symbolic-ref --quiet --short refs/remotes/origin/HEAD`, with the `origin/` prefix stripped.
2. `main`, when `git rev-parse --verify --quiet refs/remotes/origin/main` succeeds.
3. `main`, when `git rev-parse --verify --quiet refs/heads/main` succeeds.
4. `master`.

Use `origin/<base>` when `git rev-parse --verify --quiet origin/<base>` succeeds.
Otherwise use the local `<base>` when `git rev-parse --verify --quiet <base>` succeeds.
This is the **base ref**.
If neither ref exists, say that no default branch was found and stop.
Record its commit date with `git log -1 --format=%cs <base ref>`.

### 4. Judge each plan

Read each plan's requirements, acceptance list, or task list.
Skip the rest of a long plan.

Search the base ref, not the worktree:

- `git grep -n <pattern> <base ref> -- <path>` finds text.
- `git show <base ref>:<path>` reads a file.
- `git log --oneline <base ref> -- <path>` shows history.
- `gh pr view <number> --json state` checks a pull request the plan names.

A worktree hit counts only when the same path is on the base ref.

Apply these checks in order. The first match wins.

1. **Successor.** Only for Kind idea, prd, reqs, design, or spec.
   Look in both `--group` outputs for a later plan with the same slug and a later Kind.
   That later plan can be active, in review, or in `done/`.
   Read its opening.
   If it rejects or replaces this plan's approach, this plan is **irrelevant**.
   Otherwise this plan is **complete**.
   An exec-plan has no later Kind, so skip this check for it.
2. **Complete.** Every outcome the plan asks for is on the base ref.
3. **Partial.** At least one outcome is on the base ref, and at least one is absent.
4. **Irrelevant.** The plan no longer applies. Any one of these is enough:
   - Its paths are gone from the base ref, and no successor continues the work.
   - A later plan or a repo doc drops the goal.
   - It targets a plugin or layout this repo no longer has.
5. **Open.** Everything else. This includes these cases:
   - Nothing it asks for is on the base ref.
   - A `Blocked-by` plan holds it.
   - The work exists only on a side branch. Name that branch in the evidence line.
   - It has no checkable outcome.
   - The evidence is weak.

On the skill's own judgment, only check 1 or check 2 can archive a plan.
The one exception is an explicit user override in step 7.

Take the Kind from frontmatter.
When frontmatter has none, use the filename's `--<kind>` segment.

Every verdict gets one evidence line.
A complete line names something concrete: a file, a symbol, a test, a merged pull request, or a successor plan.

For more than 30 plans, judge in batches of 10.
Append each batch's verdict lines to a scratch file from `mktemp`, so no verdict is lost.

A plan with malformed frontmatter stays active.
List it under **Errors**.
CLI exit 5 means malformed frontmatter.

### 5. Archive the complete plans

Skip this step on a dry run.

For each complete plan, run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta set --file <absolute .md path> --status done
```

The CLI sets `Status: done`, stamps `Completed on`, and moves the plan and its siblings to `done/`.
Stdout is the archived path. Show that path, not one you built.

Exit 2 with `ERROR: collision` on stderr means a file of the same name is already in `done/`.
Nothing moved.
Continue with the other complete plans.
At the end, ask the user to pick for each collision: overwrite, suffix, or skip.
Re-run with `--on-collision overwrite` or `--on-collision suffix` for that plan only.

### 6. Print the report

Keep the headings in this order.
An empty group says `None`.
Numbers restart at 1 in each group.

```text
Checked 22 plans (backlog, todo, in-progress) for wild-horses against origin/main at 2026-09-20.

## Complete

1. 2026-05-01-foo--exec-plan.md
   Evidence: plugins/foo/skills/foo/SKILL.md on origin/main
   Archived: /Users/paul/plans/wild-horses/done/2026-05-01-foo--exec-plan.md

## Partial

1. 2026-05-02-bar--exec-plan.md
   On origin/main: the command file
   Missing: the hook

## Irrelevant

1. 2026-04-01-old--design.md
   Reason: plugins/old is absent on origin/main, and no later plan continues it

## Open

1. 2026-06-01-baz--exec-plan.md
   Reason: no outcome on origin/main; the plugin path still exists
```

On a dry run, a complete row says `Would archive` in place of `Archived:` and has no path.
Add an `## Errors` group only when a plan was malformed or a collision is waiting.

### 7. Stop

Leave partial, irrelevant, and open plans in place.
The user can override one verdict: they name a listed plan and say it is done.
That is the user's decision, not a verdict, so say so in the reply.
Then run step 5 for that plan only.
To shelve one, point the user to `plan-done` or `plan-update`.

## Common mistakes

- **Don't reprint a plan list from memory.** Run step 2 on every invocation.
- **Don't judge a named repo's plans against another repo's history.** Use that repo's checkout, or stop.
- **Don't switch repos when this repo has no plans to scan.** Report the empty scan and stop.
- **Don't scan an in-review plan.** Its pull request is still under review.
- **Don't treat CLI exit 2 as a crash.** A collision is exit 2, and nothing moved.
- **Don't run file-meta set on a non-markdown file.** The CLI moves siblings when you pass the `.md` plan.
- **Don't archive a plan whose evidence line names nothing concrete.** Mark it open.
- **Don't move a partial or irrelevant plan.** This skill only lists them.
- **Don't edit a plan body.** Only the CLI writes plan files, and only frontmatter.
- **Don't fetch from origin.** Judge the base ref as it is locally.
- **Don't run refresh_worktree_cli.py.** Fast-forwarding the worktree is out of scope.

## Notes

- An archive is hard to undo. The CLI refuses an active status on a plan in `done/`. Undo is a hand move back to the active directory, plus a frontmatter edit.
- A dependent whose `Blocked-by` names an archived plan can proceed after the archive.
- Siblings: `plan-list` shows plans, `plan-done` archives one plan, `plan-update` edits frontmatter.
