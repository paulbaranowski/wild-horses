---
description: Run pyright on the Python code in this project and systematically fix the errors using documented fix patterns. Supports optional strictness override (basic/standard/strict), persisting the level to config, progressive ratcheting, and scoped runs.
argument-hint: "[basic|standard|strict] [--persist] [--ratchet] [--scope <path>]"
---

# Pyright: Run and Fix

Run pyright on the Python code in this project and fix the errors it finds, following the rule-specific patterns in the bundled playbook.

**Pattern catalog.** The playbook is split across four files at `${CLAUDE_PLUGIN_ROOT}`:

- `reference.md` — index, setup, triage, suppression policy, narrowing artifacts, documented-API preference, CI, parallel dispatch, external-finding verification, editor-autofix warning, serialization compat, config-intent principle, assert-vs-raise.
- `rules.md` — fix recipes keyed on pyright rule name (`reportOptionalMemberAccess`, `reportArgumentType`, `reportTypedDictNotRequiredAccess`, …).
- `libraries.md` — library-stub workarounds (bitstring, scipy, tornado, matplotlib, Beanie, Supabase, litellm, pydantic, PIL, tenacity, pymongo, …).
- `bugs.md` — signals that pyright has uncovered a *real bug* (to flag for the user, not silence).

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

Validation:
- If `--ratchet` is given with an explicit `level`, stop and ask the user to clarify.
- If `level` is given but not in the allowed set, stop and ask.
- If no args, proceed with the current config's level, no persistence, no ratchet.

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

Show the user the effective parameters before proceeding:
```
Effective level:   <level>
Persist on zero:   yes/no
Ratchet:           yes/no
Scope:             <path or "project include">
Config modified:   yes/no  (will restore at end unless persisted)
```

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

Otherwise **stop and show the user** before beginning fixes. Briefly describe the planned strategy (inline vs. parallel dispatch) so the user can course-correct.

---

## Phase 3 — Fix

### Decide strategy by error count

**< 20 errors — inline.**
- Fix production code first, tests after. Production fixes cascade into tests; the reverse would mean redoing work.
- For each file: read it, read the relevant error lines from `/tmp/pyright_full.txt`, consult `rules.md` / `libraries.md` for the specific rule(s) or library, apply fixes.
- After each file: `pyright <file>` to verify before moving on.

**≥ 20 errors — parallel dispatch.**
- Partition files into **disjoint** groups of roughly equal error count. Aim for 3–5 groups.
- Split per-file errors into `/tmp/pyright_group_<N>.txt` using `grep` against the file paths in each group.
- Dispatch general-purpose agents **in parallel** — single message with multiple Agent tool calls, not sequential.
- Each agent prompt includes:
  - Its file list. **Hard rule: the agent MUST NOT touch any file outside this list.**
  - Path to its pre-split error file (`/tmp/pyright_group_<N>.txt`).
  - Pointers to `${CLAUDE_PLUGIN_ROOT}/rules.md` and `${CLAUDE_PLUGIN_ROOT}/libraries.md` for recipes. The agent should read only the files its errors require — an agent fixing rule-keyed errors can skip `libraries.md` and vice versa. `${CLAUDE_PLUGIN_ROOT}/reference.md` for suppression policy and assert-vs-raise if suppression comes up.
  - Project conventions: read `CLAUDE.md`, `AGENTS.md`, or the project's contributor guide and summarize line length, naming, formatting.
  - Validation: run `pyright <files>` before finishing; re-run if errors remain.
- Wait for all agents to complete, then re-run `pyright` over the touched file set.
- If a new rule now dominates, re-triage and dispatch another round.

### Fix principles

These apply throughout Phase 3, in inline and dispatched modes. Many expand on recipes in `rules.md` / `libraries.md`:

1. **Consult `rules.md` (or `libraries.md` for library-stub issues) for the specific rule.** Don't guess at fixes — each rule has a documented recipe.
2. **Production code before tests.** Production type fixes cascade; tests-first means redoing work.
3. **Rule-specific suppressions only.** `# pyright: ignore[reportOptionalMemberAccess]` — never bare `# pyright: ignore`, never `# type: ignore` (that's mypy syntax).
4. **Prefer documented API alternatives** over `cast` or `# pyright: ignore`. Example: `cookies.pop(name)` beats `cookies.set(name, None)` — the pop form is type-clean and semantically identical.
5. **Widen, don't coerce, when `None` carries meaning.** `bool | None` stays `bool | None` if `None` means "unknown" distinct from `False`. Coercing destroys information.
6. **`assert x is not None` for checker-only narrowing; `raise` for runtime invariants.** Asserts disappear under `python -O` — use `raise` when the check is genuine runtime safety.
7. **Disjoint file partitions.** No two agents touch the same file. Same for any manual parallel work.
8. **Verify external type findings before rewriting.** If a review tool or PR comment claims a type error, run pyright on the file first — findings drift with rebases and stubs change between versions.
9. **`cast()` vs `isinstance()` narrowing.** `cast` when the shape is your own invariant; `isinstance` when the value crosses a real trust boundary (HTTP, user input, subprocess). Catches producer regressions a cast would silently swallow.

---

## Phase 4 — Verify

Full `pyright [<scope>]` run at the effective level.

**If the count is zero:** proceed to Phase 5.

**If the count is non-zero,** classify the residual:

- **Library-stub gaps** (see `libraries.md`): stubs are wrong but runtime works. Add `# pyright: ignore[specificRule]` with a one-line why. Iterate on these without user input.
- **Design decisions** needing user input: e.g. a tristate `bool | None` where the consumer currently treats it as `bool`. Semantically loaded — flag for the user before changing.
- **Genuine bugs pyright uncovered** (see `bugs.md`): dead attribute reads, method shadowing, repeated side-effectful calls. Do NOT silently fix these — flag with `file:line` pointers for the user.

After another pass at the auto-resolvable items, re-run pyright. If the residual is now entirely "design decisions" and "genuine bugs," present it to the user and ask which to take on.

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

Always produce a final summary, even if zero fixes were applied:

```
## Pyright Run Summary

Scope:              <scope>
Level(s) run:       <level> (<before> → <after>)   [repeat per ratchet rung]
Config persisted:   <level> → <config-file>        [or "restored to <original>"]

### Files changed (N)
- <file>: <one-line what changed>
...

### Suppressions added (N)
- <file>:<line>  `# pyright: ignore[<rule>]`  — <reason>
...

### Design changes
- Widened `Record.flag` to `bool | None` — None means "unknown" distinct from False
...

### Deferred for user review (N)
- <file>:<line>  <description>  — <why deferred>
...
```

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
