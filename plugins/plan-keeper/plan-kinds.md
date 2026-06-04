# Plan `Kind` — document-type taxonomy

The `Kind:` frontmatter field classifies **what type of document a plan file is** — where it sits on the path from raw idea to ready-to-build work. `plan-save` infers and writes it; `plan-do` reads it as its primary routing signal. This file is the single source of truth for the value set and its meaning — skills link here instead of re-stating it.

`Kind` is **orthogonal to `Status`**. `Status` (`backlog → todo → in-progress → in-review → done`) is the _lifecycle_ — how far along the work is. `Kind` is the _document type_ — what the file is. A file can be `Kind: prd, Status: in-progress`. They never overlap.

## Values

The values are a closed enum, ordered by pipeline position (earliest first):

| `Kind`      | What it is                                                                                                     | Answers          |
| ----------- | -------------------------------------------------------------------------------------------------------------- | ---------------- |
| `idea`      | Exploratory thought. No committed requirements or design — a "what if", a sketch, a note to self.              | —                |
| `prd`       | Product requirements: the problem, the why, user-facing requirements, success criteria, scope / non-goals.     | WHAT & WHY       |
| `design`    | Architecture / technical design: components, data model, interfaces, trade-offs. The structural HOW.           | HOW (structural) |
| `spec`      | Implementation spec: the concrete, detailed HOW — close enough to start building, not yet decomposed to tasks. | HOW (detailed)   |
| `exec-plan` | Executable plan: phased steps or independent tasks with acceptance criteria, ready to dispatch / execute.      | —                |

A missing/blank `Kind` is a valid state — it means "not classified", and `plan-do` falls back to inferring the type from the file's content.

## plan-do routing

`plan-do` maps `Kind` onto its tier-1 readiness routes:

| `Kind`                    | Readiness       | plan-do route                                                               |
| ------------------------- | --------------- | --------------------------------------------------------------------------- |
| `idea`                    | idea            | `superpowers:brainstorming` (turn it into a reviewed spec)                  |
| `prd` / `design` / `spec` | spec            | `superpowers:writing-plans` (turn it into a phased implementation plan)     |
| `exec-plan`               | execution-ready | the execution menu (`autonomous` / `task-list-builder` / `executing-plans`) |

When `Kind` is present it is the authoritative signal (still confirmable by the user). When it is absent, `plan-do` infers readiness from the content as before.

## Where it is enforced

- **Value set:** `VALID_KINDS` in `scripts/plan_keeper_cli.py` (and `validate_kind`).
- **Written by:** `plan_keeper_cli.py save --kind <value>` (fill-if-absent on `.md` saves only) and `file-meta set --kind <value>` (validated).
- **Set / inferred by:** the `plan-save` skill (infer-and-confirm at save time).
- **Read by:** the `plan-do` skill (routing) and `plan-update` (editing).
- **Surfaced in the filename:** a classified `.md` save is named `<date>-<slug>--<kind>.md` (`plan_filename` in `scripts/plan_keeper/naming.py`); the grouped listing (`list --group`) recovers the project slug via `plan_group_key`.
