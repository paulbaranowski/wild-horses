---
name: plan-crew
description: Use when the user asks to see or manage the groundcrew queue, queue a plan for groundcrew, promote plans to todo (in bulk), dequeue plans, or move a plan into todo state to be picked up by groundcrew. Shows the current repo's queue by default; show every repo with "all repos".
---

# plan-crew

Show the groundcrew dispatch queue — by default the **current repo's** plans, or **every** repo under
`~/plans/` on request — and manage it both directions in bulk: promote `backlog → todo` (add to the
queue) and dequeue `todo → backlog` (pull out). Backed by two `plan_keeper_cli.py` subcommands:
`crew queue list` (the scoped read) and `crew queue set` (bulk atomic `Status` write).

## Quick reference

- **Scope:** the current repo by default (`crew queue list`); `--all` lists every repo, `--repo <name>` lists one named repo. See [Choosing the scope](#choosing-the-scope).
- **Reads:** `~/plans/<repo>/*.md` with frontmatter (one level deep; `done/`, `deferred/` excluded), filtered to the resolved scope.
- **Writes:** frontmatter `Status` (and, on promote, `Agent` when missing, plus the groundcrew `Ticket` / `Ticket System` stamp) — atomic per file.
- **Groundcrew ticket stamp:** on promote, the plan's synthesized groundcrew id (`plan-<digits>`) is written to `Ticket` with `Ticket System: groundcrew`, so the dispatch id is visible the moment a plan is queued. A plan already tracked in `linear`/`jira` keeps that reference untouched.
- **Promote default agent:** a `backlog` plan with no `Agent` gets `Agent: claude` on promote, so it
  dispatches as the `claude` agent explicitly (groundcrew would already default to claude, but this
  makes it visible in the frontmatter and the queue view). Promote here is the **only** path that
  writes the `Agent` tag automatically — `plan-save` no longer stamps it at birth, and `plan-do`
  strips it when you start a plan locally. So the tag's presence means "queued for groundcrew," and
  nothing else adds it behind your back.
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

Output is a JSON array of `{repo, file, status, agent}` objects (one per active plan). Repos are grouped
in alphabetical order, and the plans within each repo arrive newest-first (by each plan's `Created:`
stamp, falling back to its filename date). Preserve that order — don't re-sort. Group them for the user
by `status` and present each ACTIONABLE plan with a global number:

- **Queued (todo)** — the live dispatch queue. Show `repo · file · agent`. These are **dequeue** candidates.
- **In flight (in-progress)** — groundcrew is running these now. Show as read-only context; do NOT number them for action.
- **In review (in-review)** — read-only context; do NOT number for action.
- **Available** — `status` of `backlog` or empty string. Show `repo · file · agent`. These are **promote** candidates.

Number the **Available** and **Queued** rows in one continuous numbered list so the user can refer to
any actionable plan by a single number. Example:

Label the heading with the scope you ran (e.g. `Groundcrew queue (herds)` for the current repo, or `(all repos)` for `--all`):

```text
Groundcrew queue (all repos):

Queued (todo) — will be dispatched:
  1. herds      · 2026-05-20-fix-auth.md        · claude
  2. wild-horses · 2026-05-19-plan-do-crew.md   · codex

Available (backlog) — promote to queue:
  3. herds      · 2026-05-22-refactor-db.md     · (no agent)
  4. wild-horses · 2026-05-21-readme-pass.md    · claude

In flight (in-progress): herds/2026-05-18-billing.md (read-only)

Reply with what to change, e.g. "promote 3, 4" or "dequeue 1".
```

If `crew queue list` returns `[]`, tell the user the current scope has no plans (for a current-repo run, name the repo and suggest `--all` / "show all repos" if they expected to see others) and stop.

### 2. Parse the user's actions

The user replies with `promote <numbers>` and/or `dequeue <numbers>` (either or both, any order). Map
each number back to its `{repo, file}` from the `crew queue list` output. Build the absolute path for each
selected plan as `$HOME/plans/<repo>/<file>`.

- `promote` targets must currently be **Available** (backlog/empty). If the user numbers a `todo` row for promote, point it out and ask.
- `dequeue` targets must currently be **Queued** (todo). If the user numbers a backlog row for dequeue, point it out and ask.

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

Run one `crew queue set` per direction selected. Each call takes the newline-delimited absolute paths on
stdin via a quoted heredoc (one auto-approved Bash call each).

Promote (writes `Agent: claude` where missing):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" crew queue set --status todo --default-agent claude <<'EOF'
/Users/<you>/plans/<repo>/<file>.md
/Users/<you>/plans/<repo2>/<file2>.md
EOF
```

Dequeue (never touches Agent):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" crew queue set --status backlog <<'EOF'
/Users/<you>/plans/<repo>/<file>.md
EOF
```

Use the real absolute paths (expand `$HOME`). Use a quoted heredoc (`<<'EOF'`) so paths pass byte-verbatim.

### 5. Re-show the queue

Re-run step 1 and show the updated queue so the user sees the result.

## Common mistakes

- **Numbering in-progress or in-review plans for action.** They are read-only context. Only `backlog`/empty (promote) and `todo` (dequeue) rows get action numbers.
- **Writing before confirming.** Step 3 is mandatory even for a single obvious promote.
- **Passing `--default-agent` on a dequeue.** Dequeue is `--status backlog` with no `--default-agent`; the CLI ignores a default agent on backlog, but omit it to keep intent clear.
- **Building paths from the display string instead of the JSON.** Always map the chosen number back to the `{repo, file}` fields from `crew queue list`, then form `$HOME/plans/<repo>/<file>`.

## Edge cases

- **`crew queue list` returns `[]`** — the current scope has no plans. For a current-repo run, name the repo and offer `--all` ("show all repos") in case they expected another repo's plans; then stop.
- **A chosen plan's frontmatter is malformed** — `crew queue set` exits non-zero with a message and writes nothing (all-or-nothing). Surface the error; the user can fix that plan via plan-save/plan-update and retry.
- **User selects a `todo` plan to promote (or a `backlog` plan to dequeue)** — it's already in/out of the queue; point it out and skip it rather than writing a no-op.

## Notes

- plan-crew only ever sets `Status` to `todo` or `backlog` (and, on promote, fills a default `Agent` when missing and stamps the groundcrew `Ticket` / `Ticket System`). The system-managed states (`in-progress`, `in-review`, `done`) are written by groundcrew / plan-done, never here.
- The mutation is atomic (tmp file + fsync + os.replace), so an interrupted run can't corrupt a plan.
- This skill scopes to the current repo by default. `--all` widens it to the whole `~/plans/` tree (its original bulk cross-repo mode); `--repo <name>` points it at one other repo. See [Choosing the scope](#choosing-the-scope).
