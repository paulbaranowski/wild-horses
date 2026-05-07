# Loop Protocol — shared by `/harness:feedback-blockers` and `/harness:reasoning-gaps`

This file documents the post-analysis flow both commands share: the Phase 4 options menu. **The JSON task file's shape** lives in `${CLAUDE_PLUGIN_ROOT}/task-list-schema.md` — read that for field definitions and the paired-test-task rule. **Execution** (resuming an existing task file, the iterative Agent loop, and the Task Implementation Prompt) lives in the `task-list-runner` skill at `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/SKILL.md`.

**Slug substitution.** The orchestrating command substitutes `<slug>` with its own slug — `feedback-blockers` or `reasoning-gaps` — wherever it appears below.

---

## Phase 4 — Propose

After presenting the merged report from the orchestrator's Phase 3, briefly explain the interventions with trade-offs for each. Then prompt the user:

> **What would you like to do?**
>
> 1. **Save plan and implement all** — Write plan and implement ALL interventions iteratively via automated loop
> 2. **Save plan and fix top intervention** — Write the full remediation plan and implement intervention #1
> 3. **Save full remediation plan** — Write the plan for incremental work
> 4. **Revise** — Provide feedback to refine the analysis or change focus

Before executing any option below, generate a **run ID** by running `openssl rand -hex 2` to produce a 4-character hex string (e.g., `a3f2`). Use this same run ID in all file names produced by this run — this prevents collisions when the command is run multiple times on the same day.

### Option 1: Save plan and implement all

Save the analysis and implement all interventions iteratively. Each intervention is implemented by a foreground Agent tool call within this conversation — the user sees every file read, edit, and test run as it happens. The JSON task file tracks progress between iterations.

**Step 1 — Discover the verify steps.** Build the `verifySteps` array (each entry is `{name, command}`). Always include a `tests` step (check CLAUDE.md, fall back to `uv run pytest` or `npm test`). Add a `typecheck` step if the project has a static type-checker configured (`tsconfig.json` → `npx tsc --noEmit`; `pyrightconfig.json` → `uv run pyright`; `mypy.ini` → `uv run mypy .`). Order steps fastest-first so cheap checks fail fast. See the `task-list-builder` skill's Phase 2 for the full discovery procedure.

**Step 2 — Write the markdown report** to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.md` (where YYYY-MM-DD is today's date and `<run-id>` is the 4-character hex run ID).

Use YAML frontmatter for metadata:

```yaml
---
status: in-progress
task_file: "docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.json"
generated: "YYYY-MM-DDTHH:MM:SSZ"
---
```

The body contains the full report from Phase 3: Scope (with repo-relative file paths), Ratings Summary, Cross-Pillar/Cross-Dimension Findings, Findings by Severity, Interventions (with full details), and Coverage Check. This is the human-readable artifact — the loop does NOT modify this file.

**Step 3 — Write the JSON task file** to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.json`.

This is the machine-readable task list that the loop reads and writes for state tracking. **The file shape — top-level fields, per-task fields, the paired-test-task rule — is defined in `${CLAUDE_PLUGIN_ROOT}/task-list-schema.md`. Read that file before writing the JSON; do not rely on memory.**

Specifics for this command's output:

- Convert the absolute file paths from Phase 1 to repo-relative paths for the `scope` array (strip the repository root prefix — e.g., `/Users/name/project/src/pipeline.py` becomes `src/pipeline.py`).
- Extract each intervention from Phase 3 into a task. For interventions tagged `createsNewCode: true`, the schema requires a paired test task immediately after — generate it.
- Set `status: "pending"` and `log: null` on every new task.

**Step 4 — Implement tasks via the `task-list-runner` skill.**

Hand off execution to the `task-list-runner` skill, passing the absolute path to the JSON task file from Step 3 with the `--all` flag. The skill owns the Agent loop, `MAX_ITER` math, the Task Implementation Prompt, and the final summary — re-read its `SKILL.md` for the up-to-date procedure.

### Option 2: Save plan and fix top intervention

- Write the full remediation plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.md` including scope, all findings, all interventions with details.
- Write the JSON task file (Step 3 above) so the runner has something to consume.
- Hand off to the `task-list-runner` skill with the JSON path and the `--next` flag — it will run intervention #1 and stop.

### Option 3: Save full remediation plan

- Write the full remediation plan to `docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.md` including scope, all findings, all interventions with details and effort estimates
- Do NOT implement anything

### Option 4: Revise

- Ask the user for feedback (different focus area, scope change, alternative priorities, additional context)
- Revise the analysis based on their input
- Present the updated report and prompt with the same four options

---

## Resuming an existing task list

To resume an in-progress task list (instead of running a fresh analysis), invoke the `task-list-runner` skill directly. It auto-locates an in-progress JSON in `docs/exec-plans/active/` when no path is given, and accepts `--all` or `--next` to skip the interactive menu. See `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/SKILL.md`.
