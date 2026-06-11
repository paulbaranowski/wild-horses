---
name: plan-split
description: Use when the user asks to split a plan into independently-grabbable pieces, decompose a plan/spec/design into child implementation tasks, break a plan into vertical slices or tracer bullets, or turn one big plan into several smaller groundcrew-dispatchable plans. Saves the slices into the per-repo plans tree wired with native Blocked-by dependencies.
---

# plan-split

Decompose **one plan** into **N child implementation-task plans** under `~/plans/<repo>/`, each a thin vertical slice (tracer bullet), wired together with plan-keeper's native `Blocked-by:` dependencies and staged at `todo`. Queue the staged slices through `plan-crew` (which stamps each one's `Agent:` tag — the dispatch gate `file-meta set` doesn't write) and groundcrew runs the wave in dependency order — the ready ones immediately, the blocked ones the moment their prerequisites finish.

This is the `to-issues` decomposition pattern retargeted from an external issue tracker to the plans tree. It composes the existing CLI (`save`, `file-meta get`, `file-meta set`) — there is no new subcommand.

## Where this sits

plan-keeper's unit of work is a **task**, and every task is a plan file. A planning task spawns more plans; an implementation task produces code (see [../../README.md](../../README.md) "The model"). `plan-split` is the bridge: it takes one **source plan** (a `spec` / `design` / `exec-plan`) and spawns its child **implementation tasks**, each ready for groundcrew. It does not file anything to Linear/Jira — that stays the occasional `plan-linear` / `plan-jira` export.

## Quick reference

- **Source plan:** the single plan being decomposed. A saved file in `~/plans/<repo>/` (named by path, topic, or "the plan I just saved") **or** the current conversation's plan. See [Choosing the source](#1-resolve-the-source-plan).
- **Slice (child plan):** one tracer-bullet plan written as `~/plans/<repo>/<date>-<project>-NN-<slice>--exec-plan.md`, `Kind: exec-plan`, `Status: todo`.
- **Ordinal:** each slice topic carries a zero-padded ordinal (`01`, `02`, …) so the wave sorts in order in `plan-list`.
- **Dependencies:** the native `Blocked-by:` frontmatter field (comma-separated prerequisite ticket IDs). groundcrew holds a `todo` slice until **every** prerequisite is `done`, then auto-dispatches. See [../../groundcrew/README.md](../../groundcrew/README.md) "Dependencies between plans".
- **Provenance:** each slice records a `Source:` frontmatter key pointing at the source plan — provenance, **not** a dependency (keep it distinct from `Blocked-by:`).
- **Source plan disposition:** when it was a saved file, it is marked `done` after slicing (its deliverable — the child plans — has been produced).
- **`<repo>`:** auto-derived or override — see [../../repo-derivation.md](../../repo-derivation.md).
- **Approval gate:** the breakdown is presented and iterated with the user; nothing is written until the user approves.

## Procedure

Follow these steps in order. Do not skip the approval gate (step 4).

### 1. Resolve the source plan

**First, check the user's invocation for a repo override** ("split the `<name>` plan", "in the `<name>` bucket"). If present, extract `<name>` and pass `--override <name>` to every CLI call below.

Then resolve what to decompose, preferring in order:

- **A saved plan the user named** — a filename, a topic, a ticket id, or "the plan I just saved / opened". Read that file from `~/plans/<repo>/`. This is the **source plan**, and it exists on disk.
- **The most recent plan opened by `plan-do`** earlier in the conversation.
- **The current conversation's plan** — a plan just produced, pasted, or approved in this session with no saved file. There is **no source plan on disk** in this case.

If you cannot confidently identify a single source, stop and ask the user which plan to split. Do not guess between candidates.

**If the source plan is a saved file, capture its ticket now** (used for the `Source:` back-reference on every slice):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta get --file ~/plans/<repo>/<source>.md
```

Record the `Plan-keeper Ticket` value and the source filename.

### 2. Explore the codebase (optional)

If you have not already explored the code, do so to ground the slices in reality. Slice titles and acceptance criteria should use the project's domain glossary vocabulary and respect ADRs in the area you're touching.

### 3. Draft vertical slices

Break the source plan into **tracer bullet** slices. Each slice is a thin vertical cut through ALL integration layers end-to-end, NOT a horizontal slice of one layer.

Slices may be **HITL** or **AFK**. HITL slices require human interaction (an architectural decision, a design review). AFK slices can be implemented and merged without human interaction. Prefer AFK over HITL where possible.

<vertical-slice-rules>
- Each slice delivers a narrow but COMPLETE path through every layer (schema, API, UI, tests)
- A completed slice is demoable or verifiable on its own
- Prefer many thin slices over few thick ones
</vertical-slice-rules>

### 4. Quiz the user (approval gate)

Present the proposed breakdown as a numbered list. For each slice, show:

- **Title** — short descriptive name (the published slice gets a zero-padded ordinal prefix).
- **Type** — HITL / AFK.
- **Blocked by** — which other slices (if any) must complete first.

Ask the user:

- Does the granularity feel right? (too coarse / too fine)
- Are the dependency relationships correct?
- Should any slices be merged or split further?
- Are the correct slices marked HITL vs AFK?

Iterate until the user approves the breakdown. **Don't publish any slice before the user approves** — this gate is mandatory.

### 5. Publish the slices

Publish in **dependency order** (prerequisites first) so a prerequisite's minted ticket exists before a dependent references it. Pick one shared `--date` (today, unless the user said otherwise) and one project slug so the wave groups and sorts together.

For each slice, in order:

**5a. Save the slice.** Stream the body on a quoted heredoc. Bake the `Source:` back-reference into the body's frontmatter (there is no `--source` flag; `save` round-trips foreign frontmatter keys). Omit the `Source:` line entirely when the source was conversation-only.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" save \
  --topic "<project> — NN <slice title>" --kind exec-plan --date <date> <<'EOF'
---
Source: <source-ticket> (<source-filename>)
---

# <project> — NN <slice title>

## What to build

<end-to-end behavior of this slice — describe the demoable path, not layer-by-layer implementation>

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2
EOF
```

`save` mints a frozen `Plan-keeper Ticket: plan-<digits>`, injects `Kind`/`Created`/`Status: backlog`, and prints the written absolute path on stdout. On exit 2 (collision), resolve it per [Collisions](#collisions) before continuing.

**5b. Capture the slice's ticket.** Read back the minted id so later slices can cite it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta get --file <path-from-5a>
```

Record this slice's `Plan-keeper Ticket` and filename, keyed by its ordinal.

**5c. Wire dependencies.** For a slice that has prerequisites, set `Blocked-by:` to the prerequisite slices' tickets (the `(filename)` hint is for humans and is ignored by the resolver):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta set --file <path> \
  --blocked-by "<prereq-ticket> (<prereq-filename>), <prereq-ticket-2> (<prereq-filename-2>)"
```

**5d. Stage every slice at `todo`.** Set each slice's status to `todo`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta set --file <path> --status todo
```

This stages the slices but does **not** make them dispatchable: `file-meta set` writes no `Agent:` tag, and groundcrew skips every agent-less plan in every status — the `Agent` tag is a separate, required dispatch gate. To actually dispatch the slices, queue them through the `plan-crew` skill (`crew queue add`), which stamps `Agent: claude` on each. groundcrew's eligibility check then holds the blocked ones until their prerequisites reach `done`.

### 6. Mark the source plan done

**Only when the source plan was a saved file** (step 1), archive it — its deliverable (the slices) has been produced:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta set --file ~/plans/<repo>/<source>.md --status done
```

This relocates the source plan to `~/plans/<repo>/done/` and stamps `Completed on`. A conversation-only source has no file on disk — skip this step.

### 7. Confirm

Report, on consecutive lines: each published slice's absolute path with its ordinal, its `Status` (`todo`), and its blockers (or "ready", meaning no unfinished prerequisite — not "dispatchable"); then the archived source-plan path (if step 6 ran). Close with a one-line reminder that the slices still need to be queued via `plan-crew` to receive an `Agent` before groundcrew will dispatch them. For example:

```text
Split into 3 slices in ~/plans/<repo>/:
  01 …-01-schema--exec-plan.md   todo   ready
  02 …-02-api--exec-plan.md      todo   blocked by 01
  03 …-03-ui--exec-plan.md       todo   blocked by 02
Source plan archived to ~/plans/<repo>/done/<source>.md
Next: queue these via plan-crew (stamps Agent: claude) to make them dispatchable.
```

## Collisions

`save` (step 5a) and the `--status done` move (step 6) exit 2 when a same-name file already exists, printing `existing:` and `suggestion:` lines on stderr. Ask the user — overwrite, suffix `-N`, or a new topic — then re-run with `--on-collision suffix` or `--on-collision overwrite`. Exit 2 is a structured signal, not a failure to abort on.

## Common mistakes

- **Don't publish before the user approves the breakdown.** Step 4 is a hard gate — the user shapes granularity and dependencies before anything is written, exactly as `to-issues` quizzes before filing.
- **Don't put the `Source:` reference in `Blocked-by:`.** The source plan is provenance, not a prerequisite — a slice is not blocked on the plan it was carved from. `Source:` and `Blocked-by:` are orthogonal keys.
- **Don't reference a prerequisite before it is saved.** Publish in dependency order so each `Blocked-by:` cites a ticket that already exists; a reference matching no plan holds the dependent and prints a `note:` on stderr.
- **Don't claim a freshly-split slice is dispatchable.** Step 5d stages slices at `todo`, but groundcrew also requires an `Agent:` tag (which `file-meta set` does not write) and a registered repo before it will dispatch. A split slice is `todo` + agent-less = held until queued via `plan-crew`. State that, not "dispatchable."
- **Don't read the CLI's exit 2 as fatal.** It is the collision signal — resolve it per [Collisions](#collisions) and re-run.
- **Don't auto-mark the source done when it was conversation-only.** Step 6 runs only for a saved-file source; there is no file to move otherwise.

## Notes

- Each slice is a full task in its own right (`Kind: exec-plan`), so `plan-do`, `plan-crew`, `plan-update`, and `plan-done` all operate on it like any other plan.
- The `Blocked-by:` machinery, ticket resolution, and the full set of dispatch gates (active-in-a-registered-repo + `todo` + `Agent` tag + unblocked) live in groundcrew — see [../../groundcrew/README.md](../../groundcrew/README.md). plan-keeper reports each slice's real `Status`; it never masquerades a held plan.
- Sibling skills in the `plan-` family (`plan-save`, `plan-do`, `plan-done`, `plan-crew`, `plan-update`) share the same CLI and the same `~/plans/<repo>/` tree.
