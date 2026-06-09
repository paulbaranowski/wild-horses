# Loop Protocol — shared by `/refactor:feedback-blockers` and `/refactor:reasoning-gaps`

This file documents the post-analysis flow both commands share: the Phase 4 options menu. **The JSON task file is built by the `task-list-builder` skill** (`${CLAUDE_PLUGIN_ROOT}/skills/task-list-builder/SKILL.md`) — re-read its SKILL.md before invoking it; do not duplicate or re-state its phases here. **Execution** (resuming an existing task file, the iterative Agent loop, and the Task Implementation Prompt) lives in the `task-list-runner` skill at `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/SKILL.md`. The JSON file shape is defined in `${CLAUDE_PLUGIN_ROOT}/task-list-schema.md` — both skills link to it.

**Slug substitution.** The orchestrating command substitutes `<slug>` with its own slug — `feedback-blockers` or `reasoning-gaps` — wherever it appears below. The slug is passed to `task-list-builder` via `--slug <slug>` so the resulting filenames preserve provenance.

---

## Phase 4 — Propose

After presenting the merged report from the orchestrator's Phase 3, briefly explain the interventions with trade-offs for each. Then prompt the user:

> **What would you like to do?**
>
> 1. **Save plan and implement all** — Write plan and implement ALL interventions iteratively via automated loop
> 2. **Save plan and fix top intervention** — Write the full remediation plan and implement intervention #1
> 3. **Save full remediation plan** — Write the plan for incremental work
> 4. **Revise** — Provide feedback to refine the analysis or change focus

### Option 1: Save plan and implement all

Save the analysis and implement all interventions iteratively. Each intervention is implemented by a foreground Agent tool call within this conversation — the user sees every file read, edit, and test run as it happens. The JSON task file tracks progress between iterations.

**Step 1 — Build the task list via `task-list-builder`.** Invoke the `task-list-builder` skill with arguments `--slug <slug> --md-body-from-context`. The skill owns: verifySteps discovery, run-ID and path generation, the JSON shape, the preview confirmation, and writing both files. The merged Phase 3 analysis report is already rendered in conversation — `--md-body-from-context` directs the builder to use it verbatim as the MD body so the deliverable retains the Ratings Summary, Cross-Pillar/Cross-Dimension Findings, Findings by Severity, Interventions, and Coverage Check sections.

The user will see the task-list preview from `task-list-builder` Phase 5 before any files are written. If they cancel at the preview, halt — do not proceed to Step 2.

When the builder reports back, note the absolute path to the JSON file it wrote.

**Step 2 — Hand off to `task-list-runner`.** Invoke the `task-list-runner` skill with the absolute JSON path from Step 1 and the `--all` flag. The skill owns the Agent loop, `MAX_ITER` math, the Task Implementation Prompt, and the final summary — re-read its `SKILL.md` for the up-to-date procedure.

### Option 2: Save plan and fix top intervention

- **Step 1** — same as Option 1 Step 1 (invoke `task-list-builder` with `--slug <slug> --md-body-from-context`).
- **Step 2** — hand off to `task-list-runner` with the JSON path and the `--next` flag — it will run intervention #1 and stop.

### Option 3: Save full remediation plan

- Invoke `task-list-builder` with `--slug <slug> --md-body-from-context`. The builder writes both files; do NOT hand off to the runner.

### Option 4: Revise

- Ask the user for feedback (different focus area, scope change, alternative priorities, additional context)
- Revise the analysis based on their input
- Present the updated report and prompt with the same four options

---

## Resuming an existing task list

To resume an in-progress task list (instead of running a fresh analysis), invoke the `task-list-runner` skill directly. It auto-locates an in-progress JSON in `docs/exec-plans/active/` when no path is given, and accepts `--all` or `--next` to skip the interactive menu. See `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/SKILL.md`.
