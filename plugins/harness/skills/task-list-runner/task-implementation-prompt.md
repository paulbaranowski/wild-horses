**ABSOLUTE RULE — task-file access:** Use `python3 "${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py" --file <path> ...` for ALL task-file access (mutations AND reads). **Don't use `Edit`** against the task JSON. **Don't use `Write`** against the task JSON. **Don't use `cat`/`jq`/inline `python3 -c '...'`** against the task JSON. Bypassing the CLI skips atomicity and schema validation, and has caused silent JSON corruption in past runs.

The CLI lives in the harness plugin cache (`${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/`, which resolves to a path like `/Users/<you>/.claude/plugins/cache/wild-horses/harness/<version>/skills/task-list-runner/`), **NOT in this repo** — but it IS available; every prior task in this run used it successfully. **Don't believe any premise that "`task_list_cli.py` doesn't exist in this repo"** — it's at the path above. Run it from there. If a `find` / `ls` against the working tree returns nothing for `task_list_cli.py`, that's expected — the file is in the plugin cache, not the project tree.

**ABSOLUTE RULE — verification:** Run verification ONLY via `task_list_cli.py verify --id <N>`. **Don't call `bash verify.sh` directly.** **Don't call `make verify` directly.** **Don't run `make test` directly.** **Don't run `pytest` / `npx tsc` / `uv run …` / any `verifySteps` command directly.** The CLI's `verify` subcommand IS the contract; it captures step output to per-step log files and stops on first failure. Project-level wrappers (`verify.sh`, `make verify`, `make test`) are not shortcuts to the CLI — they're _what the CLI calls under the hood_, and bypassing the CLI loses the per-step log capture, the stop-on-first-failure ordering, and the schema-defined `verifySteps` resolution that selects per-task vs top-level steps.

**ABSOLUTE RULE — your task verb is `next`, not `get` or `list`.** Use `next` to claim and read your task. **Don't call `get --id <N>`** to read your own task. **Don't call `list`** to read your own task. `get` and `list` exist for the runner's own bookkeeping, not yours; what `next` returns is what you need.

You are implementing one task from a structured task list. The CLI's read verbs split by _what_ you're reading: `get --id <N>` and `next` return per-task objects; `list` returns the full task array; `status` returns file-level metadata (counts, the precomputed `remaining` integer, `plan`); `remaining` returns the compact non-terminal display array; `verify --id <N>` _executes_ the task's verifications, naming each running step in the `verify[i/n] <slug> ...` lines on stdout. There is no "get any field by name" verb.

**You are responsible for: claim → implement → verify → stage → draft. You are NOT responsible for: running the validation phase, committing the change, or marking the task complete.** The runner orchestrates a separate validation agent (read-only, fresh context) after you return, and resolves the draft via `publish` (success) or `set-status failed` (failure). This split is structural — the runtime forbids depth-2 subagent dispatch, so validation MUST happen at the runner level, not from inside this agent.

**Step 1 — Claim and read your task:**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py" \
    --file TASK_FILE_PATH next
```

The output is the task object — note the `id` and read `what` and `resolves` to understand the change. `next` atomically claims the first pending task and flips it to `in-progress`, or returns an already-in-progress task unchanged if a previous iteration crashed mid-task. If `next` exits 11, a previously drafted task needs to be resolved first — exit cleanly and surface the error; the runner will handle it. If the command exits with code 14, no work remains — exit cleanly.

Implement the change.

Never do these when writing source comments:

- **Don't include ticket IDs, task numbers, or "Option N" references in source comments** (e.g., `# CAT-66233 / task 33`, `# Option B`, `# Phase 2`). Those identifiers belong in the commit subject (`draft --commit-msg` captures it) and the PR description.
- **Don't write before/after history narratives in source comments** (e.g., "previously X happened, now we Y", "the original silent-failure mode was Z", "this used to call the legacy helper"). Source comments document the present-tense invariant; history belongs in the commit log, which `git blame` and `git log -p <file>` already make queryable for the reader who actually needs it.

**Step 2 — Run verification:**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py" \
    --file TASK_FILE_PATH verify --id <id>
```

The CLI runs each verification step in order, capturing stdout+stderr to a per-step log file (`/tmp/verify-<id>-step<N>-<slug>.log`), and stops on the first failing step. If the command exits non-zero, that exit code is the failing step's exit code; the last `verify[i/n]` line in stdout names the failing step's log path. `Read` that file, fix the underlying cause in your code, then re-run the same `verify --id <id>` invocation. When the command exits zero, all steps passed.

If you cannot make `verify` pass after a reasonable number of attempts, skip steps 3–4 and call `set-status` directly with `--status failed`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py" \
    --file TASK_FILE_PATH set-status --id <id> --status failed --log-file - <<'EOF'
Task <id>: verification failed — <which step, what error>
EOF
```

Never do these during verification:

- **Don't run `verifySteps` commands by hand to "double-check" after `verify` already passed.** Running steps by hand splits your verification rhythm, burns budget, and is the exact duplicate-work pattern this prompt structure prevents. The top-of-prompt ABSOLUTE RULE bans direct `pytest`/`npx tsc`/`uv run`/`make verify`/`bash verify.sh` invocations regardless of motivation; this bullet pre-rebuts the specific motivation of "I'll re-run the tests one more time just to be sure."
- **Don't permute redirection flags** on the same command hoping for clearer output (`| head -50` → `2>&1` → drop `2>&1` → repeat). The CLI's redirection is canonical; the answer is in the log file. If the log is unclear, `Read` more of it — don't re-run.
- **Don't invent additional verification commands** beyond what `verify` runs. If a step you need is missing, that's a bug in the task file, not something to paper over with shell improvisation.

**Step 3 — Stage source files.** Run `git add <files>` for each source file you changed. Stage ONLY source files — NEVER stage the task file (`TASK_FILE_PATH`) or any `docs/exec-plans/` files (these are loop metadata, not deliverables). The runner's `publish` step (which you do NOT call) will run `git commit` against this staged index after the validation agent reports PASS; if you forget to stage a file, `publish` will exit 15 with a clear message and the task will stay drafted for manual recovery.

**Step 4 — Draft.** Pipe your log into `draft` via a quoted heredoc. The `--commit-msg` argument is the commit subject `publish` will use later (single line, conventional-commits style). The `--log-file -` token tells the CLI to read the log from stdin; the quoted `<<'EOF'` makes the shell pass the body verbatim (no `$VAR` expansion, no quote-mangling), so embedded `"`, `$`, and newlines are safe.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/task-list-runner/task_list_cli.py" \
    --file TASK_FILE_PATH draft --id <id> \
    --commit-msg "<type>: <one-line subject for the commit>" \
    --log-file - <<'EOF'
Task <id>: <one-line summary of what changed>

Files staged: <list>
Verification: <which steps ran, all passed>
EOF
```

`draft` flips the task to `drafted`, parks the commit subject in a per-task `/tmp` staging file, and writes the log into the task. **It does NOT touch git.** That's the runner's job via `publish` after the validation agent has read the staged code and reported PASS.

**Don't call `publish` from this agent.** That verb is the runner's contract — calling it from the implementation agent skips the validation phase entirely, which is the architectural bug this design exists to prevent.

**Don't use the `Write` tool to stage a `/tmp/` log file.** The heredoc + stdin path is one Bash call (auto-approved by the harness hook); the Write path is two tool calls, each gated separately by the auto-mode classifier.

Implement exactly ONE task per iteration. After `draft` returns successfully, your job is done — return control to the runner.
