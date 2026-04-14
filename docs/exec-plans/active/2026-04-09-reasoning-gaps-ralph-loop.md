# Add Ralph Loop Option to Reasoning-Gaps Command

## Context

The reasoning-gaps command (`/harness:reasoning-gaps`) currently offers 3 options after analysis: fix the top intervention, save the plan, or revise. Adding a new **option 1** that saves the plan and uses a Ralph Wiggum loop to implement ALL interventions iteratively — one per iteration — using a JSON task file for reliable state tracking.

## File to modify

`plugins/harness/commands/reasoning-gaps.md` — this is the only file that needs changes. The ralph-wiggum plugin (installed separately) provides the loop mechanics via its stop hook.

## Changes

### 1. Reorder options in Phase 4 menu (line ~296-299)

Ralph loop becomes option 1. Current options shift down:

```text
> 1. **Save plan and implement with Ralph loop** — Write plan and implement ALL interventions iteratively via Ralph loop (requires ralph-wiggum plugin)
> 2. **Save plan and fix top intervention** — Write the full remediation plan and implement intervention #1
> 3. **Save full remediation plan** — Write the plan for incremental work
> 4. **Revise** — Provide feedback to refine the analysis or change focus
```

Renumber existing options: 1→2, 2→3, 3→4.

### 2. Add `### Option 1: Save plan and implement with Ralph loop` section

Insert before current Option 1 (which becomes Option 2). This section instructs Claude to do the following when the user picks option 1:

#### A. Save two files

**Markdown report** — `docs/exec-plans/active/YYYY-MM-DD-<short-description>.md`

Same format as Option 3 (full remediation plan) with YAML frontmatter:

```yaml
---
status: in-progress
ralph_loop: true
task_file: "docs/exec-plans/active/YYYY-MM-DD-<short-description>.reasoning-gaps.json"
generated: "YYYY-MM-DDTHH:MM:SSZ"
---
```

Body contains the full report: Scope, Ratings Summary, Cross-Dimension Findings, Findings by Severity, Interventions, Coverage Check. This is the human-readable artifact — the ralph loop does NOT modify this file.

**JSON task file** — `docs/exec-plans/active/YYYY-MM-DD-<short-description>.reasoning-gaps.json`

Machine-readable task list extracted from the interventions. The ralph loop reads and writes this file:

```json
{
  "plan": "docs/exec-plans/active/YYYY-MM-DD-<short-description>.md",
  "completionPromise": "ALL REASONING GAP INTERVENTIONS COMPLETE",
  "testCommand": "uv run pytest",
  "scope": ["src/file1.py", "src/file2.py"],
  "tasks": [
    {
      "id": 1,
      "title": "Create PipelineConfig Pydantic model",
      "what": "specific change description from the intervention's What field",
      "resolves": ["pipeline.py:45", "executor.py:23"],
      "effort": "medium",
      "status": "pending",
      "acceptanceCriteria": [
        "PipelineConfig model exists with typed fields",
        "All callers in executor.py use the model",
        "Tests pass"
      ],
      "log": null
    }
  ]
}
```

- `testCommand`: discovered once during plan creation (check CLAUDE.md, fall back to `uv run pytest` or `npm test`)
- `scope`: repo-relative file paths from Phase 1 (avoids leaking local machine paths if committed)
- Each task's `acceptanceCriteria` are derived from the intervention's What and Resolves fields
- `status`: `"pending"` | `"in-progress"` | `"complete"` | `"failed"`
- `log`: null when pending, string with details when in-progress/complete/failed

#### B. Write the loop instructions file

Write the full iteration instructions to `.claude/reasoning-gaps-loop.md`. This file is read each iteration by the ralph loop prompt. Contents should include the task file path and all the iteration steps (find next pending task, implement, test, update status, commit, exit). See the reasoning-gaps command's Option 1 Step 4 for the full template.

#### C. Start the Ralph loop

Use `/ralph-wiggum:ralph-loop` to activate the loop (do NOT write the state file directly — the setup script handles hook registration correctly):

```text
/ralph-wiggum:ralph-loop Read and follow the instructions in .claude/reasoning-gaps-loop.md --max-iterations <ceil(tasks * 1.5) + 1> --completion-promise 'ALL REASONING GAP INTERVENTIONS COMPLETE'
```

The short prompt is repeated each iteration. Claude reads the full instructions from the file.

#### D. Inform the user

Tell the user:
- Plan saved to `docs/exec-plans/active/...`
- Task file saved to `docs/exec-plans/active/....reasoning-gaps.json`
- Ralph loop activated with N max iterations
- Each iteration implements one intervention and updates the task file
- Monitor: `cat <task-file> | jq '.tasks[] | {id, title, status}'`
- Cancel with `/ralph-wiggum:cancel-ralph`

Then exit — the stop hook intercepts and starts the loop.

### 3. Key design decisions

- **JSON task file as source of truth**: Machine-parseable, no regex/checkbox fragility. Status is a field flip, not a text edit. Follows the PRD.json pattern used in proven Ralph implementations.
- **Markdown report is read-only**: The ralph loop never modifies the markdown. It only reads/writes the JSON. Keeps the human report clean.
- **Direct state file write**: Writing `.claude/ralph-loop.local.md` directly is valid — the stop hook only checks for the file's existence.
- **One task per iteration**: Prevents compounding errors, each change is tested and committed independently. Git commits are the real checkpoints.
- **Acceptance criteria in JSON**: Each task has explicit criteria, not just "implement the What field."
- **`max_iterations` = ceil(task count × 1.5) + 1**: 50% retry budget per task plus 1 for the final completion check. Example: 10 tasks → 16 iterations.

## Verification

1. `claude plugin validate .` — command still validates
2. Invoke `/harness:reasoning-gaps` on a small scope, verify option 1 is the Ralph loop
3. Pick option 1, verify:
   - Markdown report created with YAML frontmatter pointing to task file
   - JSON task file created with all interventions as pending tasks
   - `.claude/ralph-loop.local.md` state file created with correct frontmatter
   - Ralph loop activates on exit, implements tasks one at a time
   - Task file JSON updated after each iteration (status flipped, log populated)
   - Loop exits after all tasks complete
