---
description: Set up the harness engineering directory structure in any repo. Analyzes existing files, proposes moves and generations, executes after approval. Never deletes files.
argument-hint: "[project root path]"
---

# Harness Setup

Set up the **harness engineering** directory structure (from OpenAI's "Harness Engineering" article) in the target repository. This skill analyzes existing files, proposes how to reorganize them into the harness structure, identifies which new files to generate, and executes the plan after user approval.

**Target:** "$ARGUMENTS" (defaults to current working directory)

**Cardinal rule:** Never delete files. Only move or create.

---

## Target Directory Structure

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
│   ├── generated/                 ← Auto-generated docs (DB schemas,
│   │                                API specs, dependency graphs)
│   ├── product-specs/             ← Product requirements & specifications
│   └── references/                ← Reformatted external library/API docs
│                                    for agent consumption
```

### Why This Structure

| Component | What goes here | Why agents need it |
|-----------|---------------|-------------------|
| **CLAUDE.md** | ~100-line table of contents with pointers to docs/ | Agents read this first; small context = fast orientation |
| **ARCHITECTURE.md** | Module descriptions, layer diagram, dependency flow | Agents understand boundaries before making changes |
| **docs/design-docs/** | ADRs, design rationale, interface contracts | Agents understand WHY decisions were made |
| **docs/exec-plans/active/** | Step-by-step plans for current work | Agents can pick up and continue work |
| **docs/exec-plans/completed/** | Finished plans kept for reference | Historical context for future decisions |
| **docs/generated/** | Auto-generated docs (schemas, specs) | Machine-readable, always fresh |
| **docs/product-specs/** | Product requirements, user stories | Agents understand WHAT to build |
| **docs/references/** | External library docs, reformatted | Agents can reference without web access |

---

## Phase 1: Analyze Existing Repository

Scan the target repository and build an inventory of what already exists.

### Step 1: Discover existing files

Run these searches to understand the repo:

1. List all root-level markdown files: `find <root> -maxdepth 1 -name "*.md" -type f`
2. List all markdown files repo-wide: `find <root> -name "*.md" -type f -not -path "*/.git/*" -not -path "*/node_modules/*" -not -path "*/.venv/*"`
3. Check for existing docs directory: `ls -la <root>/docs/ 2>/dev/null`
4. Find agent instruction files: search for CLAUDE.md, AGENTS.md, .cursorrules, COPILOT.md, .github/copilot-instructions.md
5. Find architecture/design docs: search for `ARCHITECTURE*`, `DESIGN*`, `ADR-*`, `adr-*`, `*-design.md`, `*-architecture.md`
6. Find execution plans: search in paths containing plans/, exec-plans/, roadmap/, todo/
7. Find product specs: search in paths containing specs/, product-specs/, requirements/, features/
8. Find generated docs: search in paths containing `generated/`, and for `schema.md`, `db-schema*`, `api-spec*`, `openapi*`
9. Find reference docs: search in paths containing references/, vendor-docs/, external-docs/
10. Read CLAUDE.md (or AGENTS.md) if it exists — assess whether it's table-of-contents style (~100 lines, mostly pointers) or contains inline content

### Step 2: Classify each discovered file

For each file found, classify it as one of:

- **KEEP** — Already in the correct location per the target structure. No action needed.
- **MOVE** — Exists but should be relocated. Record: source path → destination path, with reason.
- **ENHANCE** — Exists but needs restructuring (e.g., a monolithic CLAUDE.md that should become a table of contents with detail pushed to docs/).

### Step 3: Identify gaps

For each component in the target structure that has no matching file, classify as:

- **GENERATE** — Should be created. Describe what content will be generated.
- **SKIP** — Not relevant for this project. Explain why (e.g., "no database detected" for db-schema).

---

## Phase 2: Present the Proposal

Present a clear, actionable proposal. Use these exact sections:

### 1. Directories to Create

List each directory that doesn't already exist:
```bash
mkdir -p docs/design-docs
mkdir -p docs/exec-plans/active
mkdir -p docs/exec-plans/completed
mkdir -p docs/generated
mkdir -p docs/product-specs
mkdir -p docs/references
```

### 2. Files to Move

For each file relocation:
```text
MOVE: current/path.md → docs/design-docs/path.md
      Reason: This is an architecture decision record
```

If no files need moving, say so.

### 3. Files to Generate

For each file to create:
```text
GENERATE: ARCHITECTURE.md
          How: Analyze codebase to produce a domain map
          Sections: modules, layers, dependency flow, key interfaces

GENERATE: docs/exec-plans/active/TEMPLATE.md
          How: Create a reusable execution plan template
```

For files that aren't relevant:
```text
SKIP: docs/generated/db-schema.md
      Reason: No database detected in this project
```

**CLAUDE.md handling:**
- If it doesn't exist → propose GENERATE as a table of contents
- If it exists and is >100 lines → propose ENHANCE: restructure to a ToC, move detail into docs/
- If it exists and is <=100 lines → propose ENHANCE: add pointers to new docs/ structure
- If it already points to docs/ → KEEP

### 4. Summary Table

```markdown
| Action   | Count | Details                     |
|----------|-------|-----------------------------|
| Create   | N     | directories                 |
| Move     | N     | files relocated             |
| Generate | N     | new files                   |
| Enhance  | N     | files restructured          |
| Skip     | N     | not relevant for project    |
| Keep     | N     | already correct             |
```

### 5. Ask for Approval

> **Review the proposal above. What would you like to do?**
> 1. **Execute all** — Create directories, move files, generate content
> 2. **Execute selectively** — Tell me which items to include/exclude
> 3. **Revise** — Provide feedback to adjust the proposal

**STOP HERE. Do not proceed until the user responds.**

---

## Phase 3: Execute the Plan

After the user approves (option 1 or 2):

### Step 1: Create directories

Create all approved directories using `mkdir -p`. Add a `.gitkeep` to directories that will otherwise be empty (no files being moved or generated into them).

### Step 2: Move files

For each approved move:
1. Check if the repo uses git. If yes, use `git mv`. Otherwise, `mv`.
2. Verify the file exists at the new location.
3. Report: `Moved: old/path → new/path`

### Step 3: Generate files

For each approved file, generate meaningful content — not boilerplate. Adapt to what you learned about the project in Phase 1.

**ARCHITECTURE.md** — Analyze the project and generate:
- One-paragraph project overview
- List of major modules/packages with one-line descriptions
- Dependency flow between modules (which module depends on which)
- Layer diagram if applicable (e.g., UI → Service → Repository → Database)
- Key interfaces and boundaries
- Keep it concise: this is a map, not a manual

**CLAUDE.md (new or restructured)** —
- If creating new: write a ~100-line table of contents that:
  - Opens with a 2-3 line project description
  - Lists build/test/lint commands
  - Lists key conventions (coding style, naming, patterns)
  - Points to ARCHITECTURE.md for system overview
  - Points to each docs/ subdirectory with a one-line description
  - Includes any existing conventions discovered in Phase 1
- If restructuring existing: **preserve every existing rule and instruction**. Reorganize:
  - Keep the most critical, always-needed rules in CLAUDE.md (target ~100 lines)
  - Move detailed content into appropriate docs/ files
  - Replace moved content with pointers: `See [topic](docs/design-docs/topic.md)`
  - Present a diff-style before/after so the user can verify nothing was lost

**Execution plan template** (`docs/exec-plans/active/TEMPLATE.md`):
```markdown
# [Plan Title]

## Goal
[What this plan achieves]

## Status
- [ ] Step 1: ...
- [ ] Step 2: ...

## Context
[Why this work is needed]

## Approach
[How to implement, key decisions]

## Verification
[How to confirm the plan succeeded]
```

**Design doc template** (`docs/design-docs/TEMPLATE.md`):
```markdown
# [Title]

## Status
Draft | Approved | Implemented | Deprecated

## Context
[Problem being solved]

## Decision
[What was decided and why]

## Consequences
[Trade-offs, what changes]
```

### Step 4: Final Report

Present a completion summary following this structure:

```markdown
# Harness Setup Complete

## Created
- [list directories created]

## Moved
- [list file moves with old → new paths]

## Generated
- [list generated files with line counts]

## Enhanced
- [list restructured files with summary of changes]

## Next Steps
- Review ARCHITECTURE.md and refine — it was generated from static analysis
- Add design docs as you make architectural decisions
- Create execution plans in docs/exec-plans/active/ for current work
- Add product specs for upcoming features
- Drop external library docs into docs/references/ as needed
- Run `/harness:audit` to analyze code quality against this structure
```

---

## Rules

1. **NEVER delete files.** Move or create only. If a file doesn't fit the structure, leave it where it is.
2. **Preserve git history.** Always use `git mv` for moves when in a git repo.
3. **Don't over-generate.** Only create files that add value for THIS project. Empty directories with `.gitkeep` are fine — the structure exists for when it's needed.
4. **CLAUDE.md is sacred.** When restructuring an existing CLAUDE.md, preserve every rule and instruction. Reorganize and add pointers — never remove content.
5. **Ask before acting.** Always present the full proposal and get explicit user approval before making any changes.
6. **Adapt to the project.** Not every project needs every component. A CLI tool probably doesn't need product-specs/. A library might not need exec-plans/. Use judgment and mark irrelevant components as SKIP with a reason.
7. **Generate from reality.** ARCHITECTURE.md should reflect what the codebase actually is, not what it aspires to be. Read the code before writing about it.
