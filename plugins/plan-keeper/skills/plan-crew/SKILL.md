---
name: plan-crew
description: Use when the user asks to see or manage the groundcrew queue, queue a plan for groundcrew, promote plans to todo (in bulk), dequeue plans, or move a plan into todo state to be picked up by groundcrew. Shows the current repo's queue by default; show every repo with "all repos".
---

# plan-crew

Show the groundcrew dispatch queue — by default the **current repo's** plans, or **every** repo under
`~/plans/` on request — and manage it both directions in bulk: promote `backlog → todo` (add to the
queue) and dequeue `todo → backlog` (pull out). Backed by three `plan_keeper_cli.py` subcommands:
`crew queue list` (the scoped read), `crew queue add` (promote → todo), and `crew queue drop`
(dequeue → backlog). Both writers address plans by **bare filename** within a `--repo`, so you act on
each repo's selections with one call per direction.

## Quick reference

- **Scope:** the current repo by default (`crew queue list`); `--all` lists every repo, `--repo <name>` lists one named repo. See [Choosing the scope](#choosing-the-scope).
- **Reads:** `~/plans/<repo>/*.md` with frontmatter (one level deep; `done/`, `deferred/` excluded), filtered to the resolved scope.
- **Writes:** frontmatter `Status` (and, on promote, `Agent` when missing, plus a minted `Plan-keeper Ticket` when absent) — atomic per file.
- **Plan-keeper ticket mint:** on promote, the plan's `Plan-keeper Ticket` (`plan-<digits>`, minted once and frozen) is written if absent, so the dispatch id is visible the moment a plan is queued. Any `Linear Ticket` / `Jira Ticket` the plan carries is left untouched (the three ids coexist).
- **Dispatch has several gates; the `Agent` tag is the one this skill manages.** groundcrew dispatches
  a plan only when **all** of these hold at once:
  1. it lives in a `~/plans/<repo>/` bucket (active — not `done/`/`deferred/`) whose repo is registered
     in groundcrew's `workspace.knownRepositories`;
  2. its `Status` is `todo`;
  3. it carries an `Agent` tag;
  4. it isn't held by an unfinished `Blocked-by` prerequisite.
     The `Agent` gate (3) is the one plan-crew writes — and the one most often missing, because
     **groundcrew skips every agent-less plan in every status, including `todo`** (there is no implicit
     "default to claude"; an agent-less plan is never dispatched). So `crew queue add`, which stamps
     `Agent: claude` when missing, is what actually _makes_ a plan dispatchable, not merely what makes it
     visible. The repo-registration gate (1) lives in groundcrew's own config and is outside what
     plan-crew can set — see [../../groundcrew/README.md](../../groundcrew/README.md).
- **Promote is the only automatic `Agent` writer.** `crew queue add` writes `Agent: claude` on a plan
  that has none (a plan that already names an Agent keeps it). Nothing else stamps it: `plan-save` no
  longer adds it at birth, `plan-do` strips it when you start a plan locally, and `plan-split` leaves
  its slices agent-less. So the tag's presence means "queued for groundcrew," and nothing adds it
  behind your back. The flip side: a `todo` plan can reach this queue **agent-less** (e.g. a
  plan-split slice promoted to `todo` but never queued through here), and such a plan is **not**
  dispatchable until you queue it through plan-crew to stamp the `Agent`.
- **Confirmation:** required before any mutation.
- **Sibling:** `plan-update` is the targeted single-plan frontmatter editor; plan-crew is its
  multi-select counterpart and the only one that spans repos (with `--all`). Both can promote a single
  plan — use plan-update when you already have one specific plan in hand, plan-crew when browsing the
  queue.

## Choosing the scope

`crew queue list` resolves which repos to show from its flags. Pick the scope from the user's invocation:

- **Default — current repo.** Run the bare `crew queue list`. The CLI derives `<repo>` from the cwd's git remote (or `basename $PWD`) exactly like every other skill — see [../../repo-derivation.md](../../repo-derivation.md). This is the right scope whenever the user just says "show the queue" / "manage the crew queue" without naming a repo or asking for everything.
- **All repos — `--all`.** When the user asks for the whole tree — "all repos", "every repo", "across repos", "everything", "the full queue" — run `crew queue list --all`.
- **A specific other repo — `--repo <name>`.** When the user names a repo — "the queue for `herds`", "show `wild-horses`'s plans" — pass `--repo <name>` (the CLI normalizes it like any override).

`--all` and `--repo` are mutually exclusive; the CLI rejects passing both. Whichever scope you read in step 1 is the scope you mutate in step 4 — promote/dequeue only ever act on plans the user numbered from that listing.

## Procedure

Follow these steps in order. Do not skip the confirmation step.

### 1. Show the queue

Pick the scope first (see [Choosing the scope](#choosing-the-scope)), then run the matching command. The default — the current repo — is the bare command:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" crew queue list
```

For every repo, add `--all`; for a specific other repo, add `--repo <name>`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" crew queue list --all
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" crew queue list --repo <name>
```

**Run `crew queue list` fresh every time you reach this step — including on a re-invocation later in the same conversation, and again whenever step 5 sends you back here.** Never reprint an earlier queue from memory: plans get promoted, dequeued, or dispatched between turns, so a cached queue can be stale — and the user picks actions by the row numbers, so stale numbers target the wrong plan. The numbered queue you show must come from the output you just ran.

Output is a JSON array of `{repo, file, status, agent, blocked, blockedBy}` objects (one per active plan).
Repos are grouped in alphabetical order, and the plans within each repo arrive newest-first (by each
plan's `Created:` stamp, falling back to its filename date). Preserve that order — don't re-sort. Group
them for the user by `status` and present each ACTIONABLE plan with a global number:

Classify each plan by **both** its `status` and its `agent`, because `todo` alone does not mean
dispatchable (see the dispatch-gates note in [Quick reference](#quick-reference)):

- **Queued (todo, has Agent)** — the live dispatch queue. Show `repo · file · agent`. These are
  **dequeue** candidates.
- **Needs an Agent (todo, no Agent)** — already `todo` but groundcrew skips it because it has no
  `Agent` (typically a `plan-split` slice promoted to `todo` but never queued here). It is **not**
  dispatchable as-is. Show `repo · file · (no agent)`. Treat these as **promote** candidates: running
  `crew queue add` on them stamps `Agent: claude` and makes them dispatchable. (They can be dequeued
  too, but the usual intent is to give them an Agent.)
- **In flight (in-progress)** — groundcrew is running these now. Show as read-only context; do NOT number them for action.
- **In review (in-review)** — read-only context; do NOT number for action.
- **Available (backlog)** — `status` of `backlog` or empty string. Show `repo · file · agent`. These are **promote** candidates.

`blocked` (bool) and `blockedBy` (the unsatisfied prerequisite ticket IDs) report dependency state: a
plan with `Blocked-by:` prerequisites that aren't yet `done`. When `blocked` is true, append a held
marker and its blockers to the row, e.g. `⏸ blocked by plan-849321`, and do **not** frame the plan as
ready-to-dispatch — even a `todo` plan that has an Agent will be held back by groundcrew while it is
`blocked`. You may still let the user dequeue it; just don't present it as "will be dispatched."

Number the **Available**, **Needs an Agent**, and **Queued** rows in one continuous numbered list so
the user can refer to any actionable plan by a single number. Example:

Label the heading with the scope you ran (e.g. `Groundcrew queue (herds)` for the current repo, or `(all repos)` for `--all`):

```text
Groundcrew queue (all repos):

Queued (todo) — will be dispatched:
  1. herds      · 2026-05-20-fix-auth.md        · claude
  2. wild-horses · 2026-05-19-plan-do-crew.md   · codex

Needs an Agent (todo, no Agent — not dispatchable until queued):
  3. groundcrew-config · 2026-06-11-repo-templates-01--exec-plan.md · (no agent)

Available (backlog) — promote to queue:
  4. herds      · 2026-05-22-refactor-db.md     · (no agent)
  5. wild-horses · 2026-05-21-readme-pass.md    · claude

In flight (in-progress): herds/2026-05-18-billing.md (read-only)

Reply with what to change, e.g. "promote 3, 4" or "dequeue 1".
```

If `crew queue list` returns `[]`, tell the user the current scope has no plans (for a current-repo run, name the repo and suggest `--all` / "show all repos" if they expected to see others) and stop.

### 2. Parse the user's actions

The user replies with `promote <numbers>` and/or `dequeue <numbers>` (either or both, any order). Map
each number back to its `{repo, file}` from the `crew queue list` output, then **group the selections by
`repo` and direction** — `add` and `drop` each take bare filenames scoped to one `--repo`, so each repo
gets its own call per direction. (For the default current-repo scope there is only one repo, so it's a
single call each way.)

- `promote` targets must currently be a promote candidate — **Available** (backlog/empty) **or**
  **Needs an Agent** (`todo` with no Agent). Promoting an Available plan sets `todo` and stamps
  `Agent: claude`; promoting a Needs-an-Agent plan just stamps the missing `Agent` (it is already
  `todo`). If the user numbers a `todo` row that **already has an Agent** for promote, it is already
  queued — point it out and ask.
- `dequeue` targets must currently be **Queued** (todo, with or without an Agent). If the user numbers a backlog row for dequeue, point it out and ask.

### 3. Confirm

Show exactly what will change before writing:

<!-- markdownlint-disable MD032 -->
<!-- prettier-ignore -->
> About to update the queue:
>
> Promote → todo:
> - `<repo>/<file>` (Agent: `<agent>` | will set Agent: claude)
> Dequeue → backlog:
> - `<repo>/<file>`
>
> Proceed?

<!-- markdownlint-enable MD032 -->

For promote targets with no Agent, state explicitly that `Agent: claude` will be written. Wait for
confirmation. Do not write anything until the user agrees.

### 4. Apply

Run one call per `repo` per direction selected, passing that repo's chosen plans as **bare filenames**
(the `file` field from the JSON — no path, no `$HOME`) after `--repo <repo>`.

Promote (writes `Agent: claude` where missing — that's the `add` default; a plan that already names an
Agent keeps it):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" crew queue add --repo <repo> <file>.md <file2>.md
```

Dequeue (never touches Agent):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" crew queue drop --repo <repo> <file>.md
```

If the user selected plans across several repos (an `--all` listing), issue one `add`/`drop` per repo —
each call is atomic over its own repo's batch. Always pass `--repo <repo>` from the JSON row rather than
relying on the cwd, so the call targets the listed repo regardless of where you're running from.

### 5. Re-show the queue

Re-run step 1 and show the updated queue so the user sees the result.

## Common mistakes

- **Numbering in-progress or in-review plans for action.** They are read-only context. Action numbers go to promote candidates (`backlog`/empty, and `todo` with no Agent) and dequeue candidates (`todo`).
- **Treating a `todo` row as dispatchable without checking its Agent.** A `todo` plan with no `Agent` is **not** dispatchable — groundcrew skips it. Don't list it under "will be dispatched"; surface it as Needs-an-Agent and offer to promote it (which stamps the Agent).
- **Writing before confirming.** Step 3 is mandatory even for a single obvious promote.
- **Mixing repos in one `add`/`drop` call.** Each call is scoped to a single `--repo`. When an `--all` selection spans repos, group by repo and issue one call per repo per direction — don't pass another repo's filenames under the wrong `--repo`.
- **Passing absolute paths or `$HOME/plans/...`.** `add`/`drop` take **bare filenames** (the `file` field) plus `--repo <repo>`. A path with a slash is rejected (it must resolve directly inside the repo dir). Map the chosen number back to the `{repo, file}` fields from `crew queue list` and pass `file` verbatim.

## Edge cases

- **`crew queue list` returns `[]`** — the current scope has no plans. For a current-repo run, name the repo and offer `--all` ("show all repos") in case they expected another repo's plans; then stop.
- **A chosen plan's frontmatter is malformed** — `crew queue add`/`drop` exit non-zero with a message and write nothing for that repo's batch (all-or-nothing). Surface the error; the user can fix that plan via plan-save/plan-update and retry.
- **User selects a `todo` plan that already has an Agent to promote (or a `backlog` plan to dequeue)** — it's already in/out of the queue; point it out and skip it rather than writing a no-op. (A `todo` plan with **no** Agent is a valid promote — it stamps the missing Agent and makes the plan dispatchable — so don't skip that one.)

## Notes

- plan-crew only ever sets `Status` to `todo` or `backlog` (and, on promote, fills a default `Agent` when missing and mints the `Plan-keeper Ticket` when absent). The system-managed states (`in-progress`, `in-review`, `done`) are written by groundcrew / plan-done, never here.
- The mutation is atomic (tmp file + fsync + os.replace), so an interrupted run can't corrupt a plan.
- This skill scopes to the current repo by default. `--all` widens it to the whole `~/plans/` tree (its original bulk cross-repo mode); `--repo <name>` points it at one other repo. See [Choosing the scope](#choosing-the-scope).
