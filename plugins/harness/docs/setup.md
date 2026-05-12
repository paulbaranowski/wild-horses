# /harness:setup

**Core question:** _Does the repo have a map for the agent to read?_

AI agents start every task by reading `CLAUDE.md` to orient themselves. If there's no structured documentation — no architecture overview, no pointers to design decisions, no separation between entry-point context and deep reference material — the agent spends its first minutes (and context window) on exploratory reads just to figure out what the project does. A well-organized harness directory (`CLAUDE.md` as a ~100-line table of contents, `ARCHITECTURE.md` for the domain map, `docs/` for everything else) gives agents fast orientation so they can start making useful changes immediately.

## Usage

```text
/harness:setup
/harness:setup /path/to/project
```

Run once per project, before the analysis commands.

## How it works

Analyzes existing files, proposes moves and generations into the **harness engineering** directory structure (from OpenAI's "Harness Engineering" article), and executes after approval.

```text
project-root/
├── CLAUDE.md                      ← Entry point (~100 lines max)
│                                    Table of contents pointing to deeper docs.
│                                    Agents read this first.
├── ARCHITECTURE.md                ← Domain map: major modules, layers,
│                                    dependency flow, system boundaries
├── docs/
│   ├── design-docs/               ← Architecture Decision Records (ADRs)
│   │                                and design documents
│   ├── exec-plans/
│   │   ├── active/                ← Current execution plans
│   │   └── completed/             ← Finished plans (kept for reference)
```

**Cardinal rule:** never deletes files. Only moves or creates.

After running this, agents have somewhere to read first — and the analysis commands ([reasoning-gaps](reasoning-gaps.md), [feedback-blockers](feedback-blockers.md)) become more useful because they have a baseline to refer to.
