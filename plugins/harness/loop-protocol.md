# Loop Protocol — shared by `/harness:feedback-blockers` and `/harness:reasoning-gaps`

This file documents the post-analysis flow both commands share: the Phase 4 options menu. **The JSON task file is built by the `task-list-builder` skill** (`${CLAUDE_PLUGIN_ROOT}/skills/task-list-builder/SKILL.md`) — re-read its SKILL.md before invoking it; do not duplicate or re-state its phases here. **Execution** (resuming an existing task file, the iterative Agent loop, and the Task Implementation Prompt) lives in the `task-list-runner` skill at `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/SKILL.md`. The JSON file shape is defined in `${CLAUDE_PLUGIN_ROOT}/task-list-schema.md` — both skills link to it.

**Slug substitution.** The orchestrating command substitutes `<slug>` with its own slug — `feedback-blockers` or `reasoning-gaps` — wherever it appears below. The slug is passed to `task-list-builder` via `--slug <slug>` so the resulting filenames preserve provenance.

---

## Autofix mode (skip the menu)

When the orchestrating command has set autofix mode (its `Autofix Check` matched `--autofix [N]`), do **not** present the four-option menu below. Instead, execute the autofix path here. Slug substitution still applies: `<slug>` is `feedback-blockers` or `reasoning-gaps`.

1. **Build the task list via `task-list-builder`** — invoke the skill with arguments `--slug <slug> --md-body-from-context --autofix [N]`. The `--autofix` flag is passed through verbatim from the orchestrator: if the user invoked `--autofix N`, pass `--autofix N`; if they invoked plain `--autofix`, pass plain `--autofix` (no integer). On the builder side, `--autofix` always suppresses its Phase 5 confirmation prompt, and the optional integer N additionally truncates the JSON's `tasks` array to the first N intervention tasks plus their paired test tasks (see the builder's Phase 0 and Phase 4.5 docs). The markdown report retains the full analysis regardless of N — every finding, every intervention, the full coverage check. To work on more interventions later, the user re-invokes `task-list-builder` in rewrite mode against the existing markdown.

   The builder's Phase 5 preview still renders to the conversation so the user has a visible audit trail of what's being written; only the interactive prompt is suppressed. The merged Phase 3 analysis report is already in conversation; `--md-body-from-context` directs the builder to use it verbatim as the MD body.

   When the builder reports back, note the absolute path to the JSON file it wrote (parsed from the `JSON: <path>` line in the builder's Phase 7 summary).

2. **Hand off to `task-list-runner`** — invoke with the absolute JSON path from step 1 and the `--all` flag. The runner consumes whatever is in the JSON; truncation is invisible from its perspective.

After the runner completes, fall through to its Phase 5 final summary. The user sees the same final summary they would have seen via Option 1 from the menu.

If the orchestrator captured `--autofix 0` or a negative integer, it should have rejected the input before reaching this section. If it didn't, refuse here with the same `"--autofix N requires a positive integer"` message — do not silently coerce to 1.

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
