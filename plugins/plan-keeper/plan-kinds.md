# Plan `Kind` — document-type taxonomy

The `Kind:` frontmatter field classifies **what type of document a plan file is** — where it sits on the path from raw idea to ready-to-build work. `plan-save` infers and writes it; `plan-do` reads it as its primary routing signal. This file is the single source of truth for the value set and its meaning — skills link here instead of re-stating it.

`Kind` is **orthogonal to `Status`**. `Status` (`backlog → todo → in-progress → in-review → done`) is the _lifecycle_ — how far along the work is. `Kind` is the _document type_ — what the file is. A file can be `Kind: prd, Status: in-progress`. They never overlap.

## Values

The values are a closed enum, ordered by pipeline position (earliest first):

| `Kind`      | What it is                                                                                                                                                               | Answers            |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------ |
| `idea`      | Exploratory thought. No committed requirements or design — a "what if", a sketch, a note to self.                                                                        | —                  |
| `prd`       | Product requirements: the problem, the why, user-facing requirements, success criteria, scope / non-goals.                                                               | WHAT & WHY         |
| `reqs`      | Engineering acceptance-contract: functional requirements, constraints, invariants to preserve, non-goals. What a design is verified against, with no structural HOW yet. | WHAT (constraints) |
| `design`    | Architecture / technical design: components, data model, interfaces, trade-offs. The structural HOW.                                                                     | HOW (structural)   |
| `spec`      | Implementation spec: the concrete, detailed HOW — close enough to start building, not yet decomposed to tasks.                                                           | HOW (detailed)     |
| `exec-plan` | Executable plan: phased steps or independent tasks with acceptance criteria, ready to dispatch / execute.                                                                | —                  |

A missing/blank `Kind` is a valid state — it means "not classified", and `plan-do` falls back to inferring the type from the file's content.

## Choosing the right Kind

Classify by **how far the work has actually progressed**, not by how the document reads. A doc's framing (its title, section structure, the presence of trade-offs or a rejected-alternatives section) is its _genre_; its readiness to build is its _stage_. They are independent: a doc written in the "design genre" can sit at the "exec-plan stage". Three rules resolve the common cases:

1. **Most-advanced-stage-present wins.** Pick the furthest pipeline stage the _content actually supports_. A doc that contains markers of several kinds at once is classified by the latest one, not the dominant tone.
2. **Rationale never demotes.** Design rationale, trade-offs, invariants, and a rejected-alternative section are provenance. They explain _why_; they do not pull a doc back to `design` when its body also carries everything needed to build.
3. **The `exec-plan` test is executability, not shape.** If an agent could implement it end-to-end **with no further design decisions** (concretely: named touch points (files/functions), the actual logic or pseudocode, an acceptance/test list, and a verification step), it is `exec-plan`, even when it is written as prose under a `# Design:` heading rather than decomposed into numbered tasks.

The `spec` / `exec-plan` boundary follows from rule 3: a `spec` is the detailed HOW that still has open design decisions or has not been pinned to concrete touch points; an `exec-plan` is buildable as-is. (`design` and `spec` route the same way in `plan-do`, so a design-vs-spec slip is cheap; a design-vs-exec-plan slip is the costly one, because only `exec-plan` routes straight to execution.)

The `prd` / `reqs` / `design` boundary sits earlier on the same axis. All three can mention "requirements"; the discriminator is what the content commits to:

- `prd` is product-level — the problem, the why, user value, success criteria. Requirements framed as outcomes.
- `reqs` is the engineering acceptance-contract a design gets verified against — functional requirements, constraints, invariants to preserve, non-goals — with **no structural HOW** (no components, data model, or interfaces). It is `reqs` only when that contract is the doc's furthest content.
- `design` is where structural HOW appears. By rule 1 (most-advanced-stage-present wins) and rule 2 (rationale never demotes), a doc that carries a requirements section _alongside_ a component/interface design stays `design` — the embedded requirements do not pull it back to `reqs`.

## plan-do routing

`plan-do` maps `Kind` onto its tier-1 readiness routes:

| `Kind`                             | Readiness       | plan-do route                                                               |
| ---------------------------------- | --------------- | --------------------------------------------------------------------------- |
| `idea`                             | idea            | `superpowers:brainstorming` (turn it into a reviewed spec)                  |
| `prd` / `reqs` / `design` / `spec` | spec            | `superpowers:writing-plans` (turn it into a phased implementation plan)     |
| `exec-plan`                        | execution-ready | the execution menu (`autonomous` / `task-list-builder` / `executing-plans`) |

When `Kind` is present it is the authoritative signal (still confirmable by the user). When it is absent, `plan-do` infers readiness from the content as before.

## Where it is enforced

- **Value set:** `VALID_KINDS` in `scripts/plan_keeper_cli.py` (and `validate_kind`).
- **Written by:** `plan_keeper_cli.py save --kind <value>` (fill-if-absent on `.md` saves only) and `file-meta set --kind <value>` (validated).
- **Set / inferred by:** the `plan-save` skill (infer-and-confirm at save time).
- **Read by:** the `plan-do` skill (routing) and `plan-update` (editing).
- **Surfaced in the filename:** a classified `.md` save is named `<date>-<slug>--<kind>.md` (`plan_filename` in `scripts/plan_keeper/naming.py`); the grouped listing (`list --group`) recovers the project slug via `plan_group_key`. Frontmatter `Kind` is the source of truth (`_kind_of` reads it, not the filename); the `--<kind>` segment is a display/sort convenience. A later Kind change via `file-meta set --kind` (or `plan-update`) **re-stamps that segment** (`rename_for_kind`) so the name and frontmatter stay in sync, printing the new path. Only dated plan names carry the segment; a hand-named no-date `.md` keeps its name and only its frontmatter updates.
