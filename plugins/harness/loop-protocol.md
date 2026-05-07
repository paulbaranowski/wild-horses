# Loop Protocol — shared by `/harness:feedback-blockers` and `/harness:reasoning-gaps`

This file documents the post-analysis flow both commands share: the Phase 4 options menu and the JSON task schema. **Execution** (resuming an existing task file, the iterative Agent loop, and the Task Implementation Prompt) lives in the `task-list-runner` skill at `${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/SKILL.md`.

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

This is the machine-readable task list that the loop reads and writes for state tracking. Convert the absolute file paths from Phase 1 to repo-relative paths for the `scope` array (strip the repository root prefix — e.g., `/Users/name/project/src/pipeline.py` becomes `src/pipeline.py`). Extract each intervention into a task. For interventions tagged `createsNewCode: true`, place a paired test task immediately after.

Schema (illustrative example — actual tasks come from Phase 3 interventions):

```json
{
  "plan": "docs/exec-plans/active/YYYY-MM-DD-<run-id>-<short-description>.<slug>.md",
  "verifySteps": [
    { "name": "typecheck", "command": "<typecheck command, if applicable>" },
    { "name": "tests", "command": "<test command>" }
  ],
  "scope": ["<repo-relative file paths from Phase 1>"],
  "tasks": [
    {
      "id": 1,
      "title": "<intervention title>",
      "what": "<specific change — files to modify, structures to create, patterns to fix>",
      "resolves": ["<file:line>", "<file:line>"],
      "effort": "low | medium | high",
      "createsNewCode": true,
      "status": "pending",
      "acceptanceCriteria": [
        "<concrete, verifiable criterion>",
        "<another criterion>",
        "Tests pass"
      ],
      "log": null
    },
    {
      "id": 2,
      "title": "Write tests for <thing created in task 1>",
      "what": "<what to test, where to put tests>",
      "resolves": [],
      "effort": "low",
      "createsNewCode": false,
      "status": "pending",
      "acceptanceCriteria": [
        "Test file follows project test conventions",
        "At least N test cases covering happy path, errors, and edge cases",
        "Tests pass"
      ],
      "log": null
    }
  ]
}
```

Note: tasks with `createsNewCode: true` get a paired test task immediately after. Tasks with `createsNewCode: false` (annotation-only or restructuring-only) do not.

Field definitions:

- `verifySteps` — array of `{name, command}` objects, each describing one verification step the per-task Agent runs after implementing a task. Steps run in order; on first failure the Agent stops and reports which step (`name`) failed. At least one step is required. Conventional names: `typecheck`, `tests`, `lint` — but any non-empty string is valid. Discovered once during plan creation and reused every iteration.
- `scope` — repo-relative file paths from Phase 1, preserved for potential re-analysis. Use paths relative to the repository root to avoid leaking local machine structure if the file is committed
- `createsNewCode` — `true` if the intervention creates new callable code (functions, classes, methods, services, models, protocols), `false` if it only restructures, annotates, or documents existing code. Determines whether a paired test task is generated
- `acceptanceCriteria` — derived from the intervention's What and Resolves fields. Each criterion should be concrete and verifiable
- `status` — `"pending"` | `"in-progress"` | `"complete"` | `"failed"`
- `log` — `null` when pending; a string describing what was done (or what went wrong) when in-progress/complete/failed

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
