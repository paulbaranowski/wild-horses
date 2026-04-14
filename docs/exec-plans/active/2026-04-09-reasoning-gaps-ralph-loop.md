# Add Iterative Implementation Option to Reasoning-Gaps Command

## Context

The reasoning-gaps command (`/harness:reasoning-gaps`) currently offers 3 options after analysis: fix the top intervention, save the plan, or revise. Adding a new **option 1** that saves the plan and implements ALL interventions iteratively — one per `claude -p` call — using a JSON task file for state tracking and a self-contained bash loop for iteration.

## File to modify

`plugins/harness/commands/reasoning-gaps.md` — this is the only file that needs changes. No external plugin dependencies.

## Changes

### 1. Reorder options in Phase 4 menu

Option 1 becomes "Save plan and implement all." Current options shift down to 2-4.

### 2. Add `### Option 1: Save plan and implement all` section

Steps:
1. Discover the test command (from CLAUDE.md)
2. Write the markdown report (`docs/exec-plans/active/YYYY-MM-DD-<desc>.md`)
3. Write the JSON task file (`docs/exec-plans/active/YYYY-MM-DD-<desc>.reasoning-gaps.json`)
4. Write and execute a bash loop script (`.claude/reasoning-gaps-loop.sh`)

#### Task file format

```json
{
  "plan": "docs/exec-plans/active/YYYY-MM-DD-<desc>.md",
  "testCommand": "uv run pytest",
  "scope": ["src/file1.py", "src/file2.py"],
  "tasks": [
    {
      "id": 1,
      "title": "...",
      "what": "...",
      "resolves": ["file.py:45"],
      "effort": "medium",
      "status": "pending",
      "acceptanceCriteria": ["...", "Tests pass"],
      "log": null
    }
  ]
}
```

#### Bash loop script

Self-contained loop that calls `claude -p` per task. No stop hooks, no plugin dependencies:

```bash
#!/bin/bash
set -euo pipefail
TASK_FILE="<path>.reasoning-gaps.json"
MAX_ITER=<ceil(tasks * 1.5) + 1>
PROMPT="You are implementing reasoning-gap interventions. Read the task file at $TASK_FILE..."

i=0
while [ $i -lt $MAX_ITER ]; do
  i=$((i + 1))
  PENDING=$(jq '[.tasks[] | select(.status == "pending" or .status == "in-progress")] | length' "$TASK_FILE")
  [ "$PENDING" -eq 0 ] && break
  echo "🔄 Iteration $i/$MAX_ITER — $PENDING tasks remaining"
  claude -p "$PROMPT" --allowedTools "Bash(*)" "Read(*)" "Write(*)" "Edit(*)" "Grep(*)" "Glob(*)"
done
```

### 3. `--resume` flag

Finds existing `.reasoning-gaps.json` task files, shows progress summary, and re-runs the loop script for remaining tasks.

### 4. Key design decisions

- **Self-contained bash loop**: No ralph-wiggum dependency, no stop hooks, no stdin conflicts with other hooks (emdash). Each iteration is a fresh `claude -p` call.
- **JSON task file as source of truth**: Machine-parseable state tracking. The bash loop checks completion via `jq`, not promise tags.
- **Markdown report is read-only**: Never modified by the loop.
- **`max_iterations` = ceil(N × 1.5) + 1**: 50% retry budget plus 1 for safety.

## Verification

1. `claude plugin validate .` passes
2. Pick option 1 after analysis — bash loop script is written and starts running
3. Each iteration implements one task and updates the JSON
4. Loop exits when all tasks are complete or max iterations reached
5. `--resume` restarts the loop for remaining tasks
