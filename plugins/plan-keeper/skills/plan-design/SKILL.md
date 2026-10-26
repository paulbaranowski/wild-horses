---
name: plan-design
description: >-
  Write a structured, code-grounded design document — TLDR, goals, non-goals, requirements, paired before/after architecture and data-flow diagrams, DB and RPC/API deltas, touch points, trade-offs, risks, rollout, and verification — and save it as a `Kind: design` plan file in `~/plans/<repo>/`. Use after `grill-me` has settled the decisions, or when the user asks to turn a PRD or requirements into a design.
---

# plan-design

Author a design document for a change to this codebase, then save it as a `Kind: design` plan file in `~/plans/<repo>/`.

The document is grounded in real code: the "before" half of each diagram (when the change modifies something that already exists), the current schema, and the current interface surface come from reading the repository, not from the brief. That is what makes the before/after pairs real rather than decorative.

**This skill documents; it does not decide.** It records decisions that are already settled. It never runs a design interview, never proposes approaches for the user to choose between, and never argues for a design. Decisions are made with `grill-me` (the `grilling` skill). Anything still open when this skill runs goes into the document's **Decisions to make** section, as an option set with a recommendation.

## Quick reference

- **Output:** `<root>/<repo>/<YYYY-MM-DD>-<slug>--design.md`, `Kind: design`, `Status: backlog`.
- **Input:** a saved plan routed in by `plan-do` (`prd` / `reqs` / `idea`), or the current conversation — typically right after a `grill-me` session.
- **`<repo>`:** auto-derived or override — see [../../repo-derivation.md](../../repo-derivation.md).
- **Section contract:** the sections in [Section contract](#section-contract), in order. The TLDR leads, and **Decisions to make** follows it whenever anything is open.
- **Conditional sections:** DB changes and RPC/API changes appear only when the change touches a schema or an interface; **Decisions to make** appears only when something is open.
- **Diagrams:** mermaid. Paired before/after when the change modifies something that already exists; the after alone when greenfield.
- **Writes:** exactly one file, through the bundled CLI. Nothing is committed to any repo.

## Procedure

Follow these steps in order. Do not skip steps.

### 1. Resolve the brief

Two entry shapes:

- **Routed from `plan-do`.** The `prd` / `reqs` / `idea` plan was read into context before this skill was invoked. Its content is the brief.
- **From the conversation.** Typically the user has just finished a `grill-me` session. The decisions reached there are the brief.

If neither is present — no plan in context and no prior design discussion — stop and ask the user what is being designed. Do not invent a brief.

The brief is the only default source. Read ADRs into it only when the user asks you to; do not go looking for `docs/adr/` on your own.

### 2. Investigate the current state (dispatched)

Dispatch an `Explore` agent to map the area the design changes. Do not read those files into this context yourself; the agent returns a digest, and the digest is all this skill needs.

Brief the agent to report, for the area in question:

- the components involved and how they connect — for the before architecture diagram;
- the path data actually takes through them — for the before data-flow diagram;
- the current schema: table and column definitions, with types and nullability, for any table the change touches;
- the current RPC / API surface: method names and request/response shapes, for any interface the change touches;
- the concrete files and functions a change here would land in — candidate touch points.

Ask it to cite `path/file:line` for every finding, so the draft can be checked.

### 3. Draft the document

Write the sections. The rules that carry weight:

- **TLDR is section 1** and runs 3–5 sentences. It states the proposal, not the problem.
- **Requirements are grouped past roughly seven items.** A flat numbered list is correct at or below that; above it, group under labelled sub-headings — by area, or by functional versus constraint. Do not split three requirements into three groups of one.
- **Before/after pairs.** Sections 6 and 7 carry both states when the change modifies something that already exists. A greenfield change shows the after alone, with no note about the absent before.
- **Sections 8 and 9 are conditional.** Emit DB changes only if the change touches a schema, and RPC/API changes only if it touches an interface. When either does not apply, leave no heading behind.
- **Decisions to make is where undecided things go.** One entry per open fork: the question in product terms, the options with what a reader would see under each, a recommendation, what follows from it, and a **Mechanics** block for whoever builds it. It sits immediately after the TLDR, unnumbered, and is omitted entirely when nothing is open. Name `grill-me` as the tool that settles each entry.

### 4. Self-review

Read the draft once with fresh eyes and check:

- **Placeholders.** No "TBD", no "fill in later", no section that describes what it should contain instead of containing it.
- **Before/after symmetry.** Every "after" diagram answers the same question as its "before" — same components, same flow, same schema — or the difference is explained.
- **Touch points exist.** Every path section 10 names as modified is a real file in the repository. Paths the change creates are labelled new, and their parent directory exists. This is what catches a stale or renamed path from the `Explore` digest.
- **Deltas are complete.** Every column added, dropped, or changed appears in section 8; every method added, dropped, or changed appears in section 9.
- **Omitted sections.** Sections 8 and 9 are absent rather than empty when they do not apply.

Fix what you find inline. Do not re-run the review.

### 5. Save

Write the document through the bundled CLI, the same way `plan-save` writes a `.md` heredoc save:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" save \
  --topic "<the document's H1>" \
  --kind design \
  <<'EOF'
<the document, verbatim — no frontmatter, no preamble, no footer; the CLI writes the frontmatter>
EOF
```

The CLI prints the written absolute path on stdout. On exit 2 the target already exists — handle the collision exactly as [plan-save's step 4](../plan-save/SKILL.md) describes: ask the user, then re-invoke with `--on-collision`. Do not restate that procedure here.

Report the path and the `Kind` you assigned, so the user can correct the Kind in one reply.

## Section contract

**Decisions to make** sits between the TLDR and section 2 — unnumbered, because it is conditional and a number would shift every other section's number depending on whether it is present. One entry per open fork: the question in product terms, the options with what a reader would see under each, a recommendation, what follows from it, and a **Mechanics** block. Omitted entirely when nothing is open.

| #  | Section                     | Rule                                                          |
| -- | --------------------------- | ------------------------------------------------------------- |
| 1  | TLDR                        | 3–5 sentences stating the proposal                             |
| 2  | Problem / context           | What is wrong today, and why now                               |
| 3  | Goals                       | What success looks like                                        |
| 4  | Non-goals                   | Explicit scope fence                                           |
| 5  | Requirements                | Functional + constraints; grouped above ~7 items               |
| 6  | Before / after architecture | Paired mermaid; the after alone when greenfield                |
| 7  | Data flow                   | Paired mermaid; the after alone when greenfield                |
| 8  | DB changes                  | **Conditional** — DDL delta, current → proposed                |
| 9  | RPC / API changes           | **Conditional** — the interface delta                          |
| 10 | Touch points                | The files and functions the change lands in                    |
| 11 | Trade-offs / alternatives   | Options rejected and why                                       |
| 12 | Risks                       | What could go wrong                                            |
| 13 | Rollout / migration         | Ordering, backfill, reversibility                              |
| 14 | Verification                | How you will know it works                                     |

## Common mistakes

- **Don't run a design interview.** Deciding is `grill-me`'s job. This skill structures decisions; it does not extract them.
- **Don't propose approaches for the user to pick between.** That is the designer's move, and this skill is a documenter.
- **Don't invent a decision to fill a gap.** An unanswered fork belongs in **Decisions to make**, with its options and a recommendation.
- **Don't emit an empty conditional section.** If the change touches no schema, section 8 does not exist — no heading, no "N/A".
- **Don't read the codebase into this context.** Dispatch the `Explore` agent and work from its digest.
- **Don't go looking for ADRs.** The brief is the default source; read ADRs only when the user asks.
- **Don't group a short requirements list.** Grouping three requirements into three groups of one is worse than not grouping.
- **Don't draw only the after state when there is a before.** Sections 6 and 7 are pairs whenever the change modifies something that already exists.
- **Don't restate plan-save's collision procedure.** Link to it. Two copies of one procedure drift.
- **Don't add a preamble or footer to the saved body.** The CLI writes stdin verbatim; the document is the document.
