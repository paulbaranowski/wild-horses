---
description: Run pyright on the Python code in this project and systematically fix the errors using documented fix patterns. Supports optional strictness override (basic/standard/strict), persisting the level to config, progressive ratcheting, scoped runs, fix-intent selection, and an optional "suggested improvements" section at the end.
argument-hint: "[basic|standard|strict] [--persist] [--ratchet] [--scope <path>] [--intent silence|improve|bugs-only] [--no-suggestions]"
---

# Pyright: Run and Fix

Run pyright on the Python code in this project and fix the errors it finds, following the rule-specific patterns in the bundled playbook.

**Pattern catalog.** The playbook is split across five files at `${CLAUDE_PLUGIN_ROOT}`:

- `reference.md` — index, setup, triage, suppression policy, narrowing artifacts, documented-API preference, CI, parallel dispatch, external-finding verification, editor-autofix warning, serialization compat, config-intent principle, assert-vs-raise, **consolidation pass (orchestrator-level)**.
- `rules.md` — fix recipes keyed on pyright rule name (`reportOptionalMemberAccess`, `reportArgumentType`, `reportTypedDictNotRequiredAccess`, …).
- `libraries.md` — library-stub workarounds (bitstring, scipy, tornado, matplotlib, Beanie, Supabase, litellm, pydantic, PIL, tenacity, pymongo, …).
- `bugs.md` — signals that pyright has uncovered a _real bug_ (to flag for the user, not silence).
- `suggestions.md` — Phase 5 "Suggested improvements" procedure: signals to combine, sort order, save flow.

Read `reference.md` at the start of the run for the index and the triage/policy framework. Re-consult `rules.md` and `libraries.md` when triage surfaces specific rules or library names. If `${CLAUDE_PLUGIN_ROOT}` isn't substituted in this context, find the files with `Glob "**/pyright/reference.md"` and read the siblings alongside it.

**Arguments:** "$ARGUMENTS"

---

## Phase 1 — Parse args and verify setup

### Step 1: parse `$ARGUMENTS`

Expected tokens, all optional:

- **Positional `level`** ∈ {`basic`, `standard`, `strict`}. Overrides the config's `typeCheckingMode` for this invocation.
- **`--persist`** — if a level is given and the run reaches zero errors, write that level back to config.
- **`--ratchet`** — climb `basic` → `standard` → `strict`, fixing to zero at each rung. Mutually exclusive with an explicit `level`; implies `--persist` at each rung.
- **`--scope <path>`** — restrict pyright invocation and fix work to a subpath. Default: the config's `include`.
- **`--intent <silence|improve|bugs-only>`** — declares the lean Phase 3 should take when fixing errors. See Phase 3 § "Apply the chosen intent" for the full, authoritative definitions. If omitted, the command prompts in Phase 2 after showing triage.
- **`--no-suggestions`** — skip the "Suggested improvements" section in Phase 5. Default: include it.

Validation:

- If `--ratchet` is given with an explicit `level`, stop and ask the user to clarify.
- If `level` is given but not in the allowed set, stop and ask.
- If `--intent` is given with a value not in `{silence, improve, bugs-only}`, stop and ask.
- If no args, proceed with the current config's level, no persistence, no ratchet, and prompt for intent in Phase 2.

### Step 2: verify pyright is installed

Run `pyright --version`. If missing:

- Detect package manager: presence of `uv.lock` → `uv`; `poetry.lock` → `poetry`; `Pipfile.lock` → `pipenv`; else `pip`.
- Propose the matching install command (`uv add --dev pyright`, `poetry add --group dev pyright`, `pip install pyright`, etc.).
- Ask the user to run it. Stop here until pyright is available.

### Step 3: check and optionally create config

Look for pyright config in order:

1. `[tool.pyright]` table in `pyproject.toml`.
2. `pyrightconfig.json` in the project root.

If neither exists, propose writing `[tool.pyright]` to `pyproject.toml`:

```toml
[tool.pyright]
include = ["<detected-source-dirs>"]    # src, lib, or the top-level package directory
typeCheckingMode = "basic"
pythonVersion = "<detected>"             # match the project's runtime
```

Ask for approval before writing. `basic` is the right starting mode for adopting pyright — see `reference.md` § "Setup" for the reasoning.

### Step 4: resolve effective level

- If `level` positional arg given → effective level = that arg.
- If `--ratchet` given → effective level = current config's `typeCheckingMode`.
- Else → effective level = current config's `typeCheckingMode`.

### Step 5: apply level override (if needed)

Pyright has **no CLI flag** for `typeCheckingMode`. The only way to override is to modify the config file. Mechanism:

1. Read the current `typeCheckingMode` from config. Save it to `/tmp/pyright_original_level.txt` so mid-run crashes are recoverable.
2. If the effective level differs from the current config value, write the effective level to config (`pyproject.toml` or `pyrightconfig.json`, whichever is in use).
3. After the run completes in Phase 5, restore the original value unless `--persist` fired on a zero-error run.

Echo the resolved parameters back to the user before proceeding: effective level, fix intent (or "will prompt in Phase 2"), suggestions on/off, persist-on-zero, ratchet on/off, scope, and whether the config file was modified (with a note that it will be restored at end unless persisted).

---

## Phase 2 — Baseline

Save pyright output to a file so it survives downstream tool calls:

```bash
pyright [<scope>] > /tmp/pyright_full.txt 2>&1
```

Total error count:

```bash
grep -cE '^\s+/' /tmp/pyright_full.txt
```

Bucket by rule (top 20):

```bash
grep -oE 'report[A-Za-z]+\)' /tmp/pyright_full.txt | sort | uniq -c | sort -rn | head -20
```

Bucket by file (top 20):

```bash
grep -E '^\s+/' /tmp/pyright_full.txt | sed 's|:[0-9].*||' | sort | uniq -c | sort -rn | head -20
```

Present a triage summary to the user:

```
Level:         <level>
Scope:         <scope>
Total errors:  <N>
Top rules:     <rule>  <count>   ...
Top files:     <file>  <count>   ...
```

If total is 0, skip to Phase 4.

Otherwise **stop and show the user** before beginning fixes.

### Confirm fix intent

If `--intent` was supplied in Phase 1, echo the chosen lean and skip the prompt. Otherwise ask the user to choose an approach:

> **How would you like me to approach these errors?**
>
> - **`silence`** — suppress and cast; fastest to zero. Leaves suppressions for a later improvement pass (Phase 5 will list them).
> - **`improve`** — widen, annotate, extract factories; pause for semantically loaded decisions. Slower, larger diff, more durable.
> - **`bugs-only`** — fix only real bugs (see `bugs.md`); batch-suppress the rest with a TODO marker. Zero type churn, high suppression count.

If the user asks for a deeper explanation of any option, quote the matching bullet from Phase 3 § "Apply the chosen intent" — that is the authoritative definition. Do **not** paraphrase from this prompt.

Record the chosen intent for use in Phase 3 and Phase 5.

Briefly describe the planned strategy (inline vs. parallel dispatch) so the user can course-correct before dispatch.

---

## Phase 3 — Build the task list

This phase produces a structured markdown report with one task per file with errors. It does **not** apply fixes — fixes happen in Phase 4 via the `task-list-runner` skill.

### Apply the chosen intent

**This section is the authoritative source for intent behavior.** Phase 1's `--intent` flag blurb and Phase 2's interactive prompt are summaries only; if any of them drifts from this section, this section wins. When editing an intent's behavior, update _here first_ and reconcile the summaries after. The matching bullet is copied verbatim into each per-task `what` field in Step 2 below; the matching template from `task-list-templates.md` populates each per-task `agentValidations` array.

The intent from Phase 2 (or `--intent`) shapes how each per-task agent fixes its file. This is the primary lean; the per-rule recipes in `rules.md` / `libraries.md` still apply within that lean.

- **`silence`** — default to rule-specific suppressions (`# pyright: ignore[rule]` + one-line why) and `cast()` at boundaries when the recipe allows it. Still prefer documented-API alternatives where they exist (e.g., `cookies.pop()` over `cookies.set(name, None)`) and still flag `bugs.md`-class items. **Do not** rewrite signatures, widen types, or introduce new factories under this intent — those belong to `improve`.
- **`improve`** — prefer the semantically richer fix: widen over coerce, annotate over cast, extract a factory over repeated `cast(T, ...)` (see `rules.md` § "When to extract a test factory"). Pause and ask the user before changing anything semantically loaded (e.g., `bool | None → bool`, tristate collapses). Expect a larger diff and fewer suppressions. **Also:** before considering a touched file done, scan it for `dict[str, Any]` values read through 3+ distinct literal keys; if any are found, consult `rules.md` § "Opaque `dict[str, Any]` with repeated key reads" and propose TypedDict/Pydantic extraction to the user. This scan is pyright-clean code (no error triggered it) — skipping it is not a pyright regression, so approval is required before writing the new type.
- **`bugs-only`** — fix only rules matching patterns in `bugs.md`. For every other error, add a rule-specific suppression with the trailing marker `# TODO(types): revisit under --intent improve` so the locations are grep-able later. No widenings, no casts beyond what the suppression needs.

### Step 1 — Partition errors per file

Group `/tmp/pyright_full.txt` errors by the file they're in. **One task per file**, regardless of error count:

- Per-task `verifySteps: pyright <file>` only works if the per-task scope is one file. Multi-file groupings would force multi-file `pyright` invocations, moving verification away from the resolution boundary.
- The runner's atomicity guarantees apply per-task. One file per task means a partial cleanup (3 of 8 files done) leaves the runner in a clean state — every completed task corresponds to a verifiably clean file.

For each unique file with errors, build:

- `<file-path>` (repo-relative; strip any local-machine prefix)
- `<line-list>` (every `file:line` for this file from `/tmp/pyright_full.txt`)
- `<rule-set>` (distinct rule names involved at this file)
- `<error-count>` (used to derive `effort`)

Files with `<error-count>` ≥ 30 produce a heavy task; flag those when the `task-list-builder` previews the plan so the user can split them manually if desired (the builder supports per-task editing during preview).

### Step 2 — Resolve task fields per file

For each file from Step 1, build the per-finding fields the `task-list-builder` ingests (per its hard rules in `${CLAUDE_PLUGIN_ROOT}/../harness/skills/task-list-builder/SKILL.md`; find via `Glob "**/harness/skills/task-list-builder/SKILL.md"` if cross-plugin path doesn't resolve):

- **`title`** — `Fix <rule>[, <rule>] in <file-path>` listing the rules when `<rule-set>` has ≤ 2 rules; otherwise `Fix pyright errors in <file-path>` (rule-agnostic). This avoids titles like `Fix A and B and C and D and E in src/foo.py`.
- **`what`** — copy the matching intent's bullet **verbatim** from § "Apply the chosen intent" above. Don't paraphrase. The per-task agent reads `what` to decide its lean — paraphrasing drops constraints (e.g., omitting "still flag `bugs.md`-class items" from `silence`) and leans the task wrong.
- **`resolves`** — repo-relative `file:line` entries from `<line-list>`.
- **`effort`** — `low` if `<error-count>` ≤ 3; `medium` if 4–10; `high` if ≥ 11.
- **`createsNewCode`** — always `false` for pyright tasks. Hard-code `false`; don't derive it. Pyright fixes don't create new callable code; if a future intent does, this rule must be revisited.
- **`verifySteps`** (per-task override) — `[{name: typecheck, command: pyright <file-path>}]`. Scopes verification to one file so the verify gate doesn't deadlock on other files' errors. **Don't** include `tests` or any project-wide step here; those would trigger the deadlock the per-task override exists to prevent.
- **`agentValidations`** — copy the `## Template: <intent>` block **verbatim** from `${CLAUDE_PLUGIN_ROOT}/task-list-templates.md`, where `<intent>` is the resolved intent. **Don't** add per-file or per-rule entries — the templates are intent-keyed and apply uniformly across all of the run's files. **Don't** synthesize entries the template doesn't list — the templates are the source of truth for what each intent's tasks must validate.

### Step 3 — Render the structured markdown report inline in conversation

Render the report as a chat message — do **not** write it to a file. Phase 4 will invoke `task-list-builder --md-body-from-context`, which copies the most recent rendered report from conversation verbatim as the MD body. Sections in order:

1. **Scope** — repo-relative file paths from Step 1's `<file-path>` set, as a bulleted list.
2. **Triage Summary** — copy the Phase 2 triage block (level, scope, total errors, top rules, top files).
3. **Findings** — bucket the errors by rule, then by file. For each bucket, list `<file>:<line> <rule> — <message>`. This is the human-readable error catalog; the runner ignores it but the user reads it during the builder's preview.
4. **Interventions** — one `### Fix ... in <file>` block per file from Step 1, with `**Resolves:**`, `**Effort:**`, `**CreatesNewCode:**`, `**What:**`, `**VerifySteps:**`, and `**AgentValidations:**` fields from Step 2. The `task-list-builder` ingests these per-finding fields per its hard rules.

**Don't** include YAML frontmatter — `task-list-builder` adds its own in Phase 6 with the `task_file:` and `generated:` fields populated from the run-id and timestamp it generates. **Don't** write the report to a file — the builder finds it in conversation context (per its `--md-body-from-context` flag contract), so writing it to disk creates two copies that can drift.

After rendering the report, proceed to Phase 4.

---

## Phase 4 — Build the task list and run it

Phase 3 rendered a markdown report in this conversation. This phase invokes `task-list-builder` to materialize the paired `.md` + `.json` under `docs/exec-plans/active/`, then `task-list-runner` to execute, then post-loop consolidation + verification before Phase 5. The runner is strictly serial (foreground Agent calls); per-task verification uses the per-task `pyright <file>` step from Phase 3 Step 2.

### Step 1 — Capture the pre-loop HEAD

Capture the pre-loop HEAD so the post-loop diff base is unambiguous (the runner's per-task agents commit individually, so `HEAD~N` won't reach the pre-loop state):

```bash
PRE_LOOP_HEAD="$(git rev-parse HEAD)"
echo "$PRE_LOOP_HEAD" > /tmp/pyright_pre_loop_head.txt
```

### Step 2 — Build the JSON task list via `task-list-builder`

Hand off to the `task-list-builder` skill with arguments `--slug pyright --md-body-from-context`. The builder owns:

- Run-ID generation, naming convention, and output paths (`docs/exec-plans/active/<DATE>-<RUN_ID>-<short-description>.pyright.{md,json}`)
- `verifySteps` discovery (Phase 2 of its SKILL.md — though our top-level `verifySteps` will be inherited from the project default; per-task `verifySteps` come from the rendered report)
- Per-finding ingestion of `**Resolves:**`, `**Effort:**`, `**CreatesNewCode:**`, `**What:**`, `**VerifySteps:**`, `**AgentValidations:**` per its Phase 4 hard rules
- Schema validation (`load_and_validate`)
- The Phase 5 user preview where the user can edit task fields, split heavy tasks (≥30 errors), or cancel before any files are written

`--md-body-from-context` directs the builder to copy the report rendered in Phase 3 Step 3 verbatim as the MD body — preserving the Scope / Triage Summary / Findings / Interventions sections. If the report is missing from conversation, the builder halts and asks; **don't** silently fall back to the synthesized body.

`--slug pyright` overrides the default `task-list-builder` filename suffix so the deliverables retain pyright provenance. The slug must match `[a-z][a-z0-9-]*`; `pyright` qualifies.

Find the builder's `SKILL.md` at `${CLAUDE_PLUGIN_ROOT}/../harness/skills/task-list-builder/SKILL.md`; fall back to `Glob "**/harness/skills/task-list-builder/SKILL.md"` if the cross-plugin path doesn't resolve. Re-read it for the up-to-date procedure.

When the builder finishes, three outcomes are possible:

- **proceed** — the `.json` and `.md` are written. Continue to Step 3 with the JSON's absolute path.
- **cancel** — no files written; the user aborted at the preview. Skip to Phase 5 with `"task-list build cancelled"` in the summary; no fixes were applied. Restore the config-level override if one was made in Phase 1 Step 5.
- **edit** — the user adjusted task fields. The builder re-validates and either proceeds or cancels per above; this branch is internal to the builder.

### Step 3 — Run the task list

Hand off to the `task-list-runner` skill, passing the absolute path to the `.json` from Step 2 with the `--all` flag. The runner owns the Agent loop, the `MAX_ITER` math, the Task Implementation Prompt (per-task agent instructions, including the per-task `verifySteps` and `agentValidations` evaluation flow), and the final summary. Re-read its `SKILL.md` (find via `Glob "**/harness/skills/task-list-runner/SKILL.md"` if needed) for the up-to-date procedure.

The runner is synchronous from this orchestrator's perspective — control returns when every task is `complete` or `failed` (or `MAX_ITER` is hit). Don't proceed past this step until the runner returns.

### Step 4 — Post-loop consolidation pass

Run the orchestrator-level consolidation pass on the loop's diff. The diff base is `$PRE_LOOP_HEAD` (Step 1); the head is `HEAD`. The full procedure (scan commands, repetition thresholds, cross-partition inconsistency check, decision flow) lives in `${CLAUDE_PLUGIN_ROOT}/reference.md` § "Consolidation pass (orchestrator-level)" — follow it there, but pass the diff as `$PRE_LOOP_HEAD..HEAD` rather than re-scanning the whole tree.

If a threshold fires, pause and present the counts to the user before proceeding. If none fires, log `"consolidation pass: clean (post-loop)"` in the summary and continue to Step 4.5.

### Step 4.5 — Behavior-change audit

The fix loop's per-task agents apply pyright fixes locally; this orchestrator-level pass scans the cumulative diff for tokens that change observable behavior. Cheap (one grep over the diff), catches behavior-change-disguised-as-typing-fix at exactly the moment it would be hardest to spot in review. Doesn't block — forces an explicit "yes, I meant to add this exception" before the run lands.

Background and policy: `${CLAUDE_PLUGIN_ROOT}/reference.md` § "Type-only by default" defines the three buckets (type-only / narrowing-only / behavior-changing). This step finds candidate behavior-changing tokens in the diff and routes each one through that taxonomy.

Scan the loop's diff against `$PRE_LOOP_HEAD` (Step 1):

```bash
git diff --unified=0 "$PRE_LOOP_HEAD..HEAD" -- '*.py' | grep -E '^\+' | grep -E \
  'raise [A-Z]|^\+ *assert |\bor \{\}|\bor \[\]|\bor ""|\bor '\'\''|\bor 0\b|else: *return|else: *continue|\bor False\b|\bor True\b'
```

For each hit, open the cited `file:line`, read enough surrounding context to classify, and route per `reference.md` § "Type-only by default":

- **False positive** — `raise` was already present in a moved block; `assert` is on a value made provably non-None by an upstream guard or by `__init__`; `or []` is on a literal that can never be `None`. Skip; log as such.
- **Type-only alternative available** — propose the alternative (e.g. `cast(str, obj.field)` instead of `if obj.field is None: raise`; declared default value instead of `or {}`; widening the parameter to `Optional[T]` instead of swallowing the None). Offer to apply.
- **Behavior change is the right design** — keep, but record a one-line justification in the run summary that names what the function is now responsible for (e.g. _"this function is the validation point; downstream callers no longer need to guard"_).

Present the audit table to the user before proceeding to Step 5:

> Behavior-change audit (N candidates):
>
> 1. `path/to/file.py:42` — added `raise ValueError("source.foo is required")` to satisfy `reportOptionalMemberAccess`.
>    - Type-only alternative: `cast(str, obj.field)` at the call site
>    - `[a]pply alternative` / `[k]eep with justification` / `[s]kip (false positive)`
> 2. ...

If zero hits, log `"behavior-change audit: clean"` in the summary and continue to Step 5.

This step parallels Step 4 (consolidation pass) — both run on the loop's cumulative diff and look for cross-partition patterns the per-task agents couldn't see. Step 4 catches "many duplicate suppressions = one missing root-cause fix"; Step 4.5 catches "behavior changes wearing typing-fix clothing."

### Step 5 — Post-loop verify

Full `pyright [<scope>]` run at the effective level. Use the count for Phase 5's persist decision and for the summary's "errors before/after" line.

**If count is zero:** proceed to Phase 5.

**If count is non-zero,** classify the residual into three buckets:

- **Library-stub gaps** (see `libraries.md`): stubs are wrong but runtime works. Add `# pyright: ignore[specificRule]` with a one-line why. These can iterate without user input.
- **Design decisions** needing user input: e.g., a tristate `bool | None` where the consumer currently treats it as `bool`. Semantically loaded — flag for the user before changing.
- **Genuine bugs pyright uncovered** (see `bugs.md`): dead attribute reads, method shadowing, repeated side-effectful calls. Do NOT silently fix these — flag with `file:line` pointers for the user.

Present the residual to the user; ask whether to:

- Take on another iteration (re-run `/pyright:run-and-fix` on the residual scope)
- Accept the residual as the run's final state and proceed to Phase 5
- Hand off the residual interventions to `/harness:reasoning-gaps` per the standard end-of-run pointer in Phase 5

---

## Phase 5 — Persist, ratchet, summarize

### Persist

If `--persist` was set AND the run reached zero errors AND the level was overridden:

- Write `typeCheckingMode = "<level>"` to the config (`pyproject.toml` or `pyrightconfig.json`).
- Report: "Persisted `typeCheckingMode = <level>` to `<config-file>`."

If the level was overridden but `--persist` was NOT set (or the run did not reach zero): **restore** the original level from `/tmp/pyright_original_level.txt`. Report the restore.

### Ratchet

If `--ratchet` was set AND the run reached zero at the current level AND current level < `strict`:

- Bump level: `basic` → `standard` → `strict`.
- Write the new level to config (ratchet implies persist at each rung).
- Loop back to **Phase 2** with the new level.

Stop ratcheting when any of:

- (a) Zero errors at `strict` — clean climb complete.
- (b) User aborts.
- (c) Progress stalls — if a new level introduces >100 errors with no clear path (many unrelated rules dominating), present the triage and ask the user whether to continue.

### Summary

Always produce a final summary, even if zero fixes were applied. The summary is a markdown block titled `## Pyright Run Summary` with these sections, in order:

- **Header lines** — `Scope`, `Level(s) run` (with `<before> → <after>` repeated per ratchet rung), `Fix intent`, `Config persisted` (or "restored to <original>").
- `### Files changed (N)` — one bullet per file with a one-line description of what changed.
- `### Suppressions added (N)` — one bullet per `<file>:<line>  # pyright: ignore[<rule>]  — <reason>`.
- `### Design changes` — one bullet per semantic change (e.g., "Widened `Record.flag` to `bool | None` — None means unknown distinct from False").
- `### Deferred for user review (N)` — one bullet per `<file>:<line>  <description>  — <why deferred>`.

### Suggested improvements

Unless `--no-suggestions` was given, follow the procedure in `${CLAUDE_PLUGIN_ROOT}/suggestions.md` to emit a list of structural improvements the run did **not** apply. This is _advice_, not actions — no edits are made in this phase.

`suggestions.md` covers: the four signals to combine (Phase 3.5 repetition counts re-applied at lower thresholds, `Any` escapes, unannotated public `def`s, intent-scoped additions), the per-suggestion format, the deterministic sort order (Groups A/B/C), and the offer-to-save flow that writes to `docs/exec-plans/active/pyright-improvements-<YYYY-MM-DD-HHMM>.md` (never committed).

### Next step

After the summary (and suggestions, if any), **always** print a single-line handoff pointer, regardless of intent, whether zero was reached, or whether `--no-suggestions` was set:

```text
Next: pyright covers the **typing axis**. The **implicit-flow** and **structure/docs** axes belong to /harness:reasoning-gaps — which ideally runs *before* pyright, since it does the type design that this command then propagates. If you haven't run it yet, run /harness:reasoning-gaps and re-run this command after.
```

This is a plain text pointer — no coupling, no shared state, no arguments passed. The user decides whether to run it.

---

## Recovery: config in overridden state

If the command was interrupted with the config overridden but no `--persist`:

- On re-invocation (with no args), detect by comparing current config level against `/tmp/pyright_original_level.txt`.
- If they differ, ask the user whether to restore before proceeding.

---

## Rules

1. **Consult `rules.md` / `libraries.md` before inventing a fix.** Every common rule has a documented recipe; `reference.md` holds the index and policy.
2. **Never bare-suppress.** Always `# pyright: ignore[specificRule]` with a one-line why when the rule isn't self-explanatory.
3. **`--persist` only fires on zero errors.** Never commit a level the code doesn't actually pass.
4. **No agent touches a file outside its partition.** The partitioning is a contract; violations produce merge conflicts.
5. **Stop and ask before writing to `pyproject.toml` or `pyrightconfig.json`.** Config changes are user-visible commits-in-waiting.
6. **Restore an overridden level if the run didn't reach zero** (unless `--persist` AND zero).
7. **Do not silently "fix" genuine bugs pyright uncovers.** Dead attributes, shadowed methods, repeated side-effectful calls — flag for user review.
8. **Run Phase 3.5 after every parallel dispatch.** Partition agents can't see cross-partition repetition; the orchestrator must. See Phase 3.5 for the counts and thresholds.
9. **Resolve fix intent before dispatching.** Phase 3 must know whether the user wants `silence`, `improve`, or `bugs-only` — either from `--intent` or from the Phase 2 prompt. Dispatched agents receive the intent in their prompt and lean accordingly.
10. **Never commit the suggestions planning file.** `docs/exec-plans/active/pyright-improvements-*.md` is a handoff artifact, not source. Do not `git add` it, do not include it in a summary of "files to commit," do not suggest committing it. This matches the project-wide convention for exec plans.
