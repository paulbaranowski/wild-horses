---
name: plan-queue
description: Use when the user asks to see or manage the groundcrew queue across repos, queue a plan for groundcrew, promote plans to todo (in bulk or cross-repo), dequeue plans, or move any plan in any repo into todo state to be picked up by groundcrew.
---

# plan-queue

Show the groundcrew dispatch queue across **all** repos under `~/plans/`, and manage it both
directions in bulk: promote `backlog → todo` (add to the queue) and dequeue `todo → backlog`
(pull out). Backed by two `plan_keeper_cli.py` subcommands: `queue list` (cross-repo read) and
`queue set` (bulk atomic `Status` write).

## Quick reference

- **Reads:** every `~/plans/<repo>/*.md` with frontmatter (one level deep; `done/`, `deferred/` excluded).
- **Writes:** frontmatter `Status` (and, on promote, `Agent` when missing) — atomic per file.
- **Promote default agent:** a `backlog` plan with no `Agent` gets `Agent: claude` on promote, so it
  dispatches as the `claude` agent explicitly (groundcrew would already default to claude, but this
  makes it visible in the frontmatter and the queue view).
- **Confirmation:** required before any mutation.
- **Sibling:** `plan-update` is the targeted single-plan / current-repo frontmatter editor; plan-queue
  is its cross-repo, multi-select counterpart. Both can promote a single plan — use plan-update when
  you already have one specific plan in hand, plan-queue when browsing the queue across repos.

## Procedure

Follow these steps in order. Do not skip the confirmation step.

### 1. Show the queue

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" queue list
```

Output is a JSON array of `{repo, file, status, agent}` objects (one per active plan). Group them for
the user by `status` and present each ACTIONABLE plan with a global number:

- **Queued (todo)** — the live dispatch queue. Show `repo · file · agent`. These are **dequeue** candidates.
- **In flight (in-progress)** — groundcrew is running these now. Show as read-only context; do NOT number them for action.
- **In review (in-review)** — read-only context; do NOT number for action.
- **Available** — `status` of `backlog` or empty string. Show `repo · file · agent`. These are **promote** candidates.

Number the **Available** and **Queued** rows in one continuous numbered list so the user can refer to
any actionable plan by a single number. Example:

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

If `queue list` returns `[]`, tell the user there are no plans under `~/plans/` yet and stop.

### 2. Parse the user's actions

The user replies with `promote <numbers>` and/or `dequeue <numbers>` (either or both, any order). Map
each number back to its `{repo, file}` from the `queue list` output. Build the absolute path for each
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

Run one `queue set` per direction selected. Each call takes the newline-delimited absolute paths on
stdin via a quoted heredoc (one auto-approved Bash call each).

Promote (writes `Agent: claude` where missing):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" queue set --status todo --default-agent claude <<'EOF'
/Users/<you>/plans/<repo>/<file>.md
/Users/<you>/plans/<repo2>/<file2>.md
EOF
```

Dequeue (never touches Agent):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" queue set --status backlog <<'EOF'
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
- **Building paths from the display string instead of the JSON.** Always map the chosen number back to the `{repo, file}` fields from `queue list`, then form `$HOME/plans/<repo>/<file>`.

## Edge cases

- **`queue list` returns `[]`** — no plans anywhere; tell the user and stop.
- **A chosen plan's frontmatter is malformed** — `queue set` exits non-zero with a message and writes nothing (all-or-nothing). Surface the error; the user can fix that plan via plan-save/plan-update and retry.
- **User selects a `todo` plan to promote (or a `backlog` plan to dequeue)** — it's already in/out of the queue; point it out and skip it rather than writing a no-op.

## Notes

- plan-queue only ever sets `Status` to `todo` or `backlog` (and fills a default `Agent` on promote). The system-managed states (`in-progress`, `in-review`, `done`) are written by groundcrew / plan-done, never here.
- The mutation is atomic (tmp file + fsync + os.replace), so an interrupted run can't corrupt a plan.
- This skill is cross-repo by design; there is no repo override — it always shows the whole `~/plans/` tree.
