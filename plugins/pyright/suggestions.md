# Pyright Suggestions — Phase 5 helper

This file holds the full procedure for the "Suggested improvements" section emitted at the end of `/pyright:run-and-fix`. The command's Phase 5 dispatches to this file unless `--no-suggestions` was given. **No edits are made in this phase** — the output is advice the user can act on later.

The orchestrator should follow the steps below in order.

---

## What this phase produces

A list of structural improvements the run did not apply, sourced cheaply from data the orchestrator already has plus two small greps. There is **no artificial cap** on length — the list is for reading, not for acting on in one session.

---

## Signals to combine

### 1. Phase 3.5 repetition counts re-applied to the full touched set

Every suppression rule with **≥ 5 sites**, and every `cast(T, ...)` target type with **≥ 3 sites**, becomes a suggestion — even if it was below the Phase 3.5 "pause" threshold. This is where `silence` runs produce the longest list: items you chose to defer now surface as actionable.

**De-dup rule against Phase 3.5.** Phase 3.5 already raised items meeting its stricter thresholds (≥ 10 suppressions, ≥ 5 casts). For each of those:

- If the user **applied** the root-cause fix in Phase 3.5, the item is resolved — omit from Phase 5 entirely.
- If the user **deferred** the item ("accepted, will revisit at ratchet time"), include it here — Phase 5 is where deferrals surface as follow-up work.

Items below Phase 3.5's threshold but at or above Phase 5's lower threshold (≥ 5 / ≥ 3) only appear in Phase 5.

### 2. `Any` escapes in touched files

```bash
files=$(git diff --name-only -- '*.py')
grep -nE ': Any\b|-> Any\b|cast\(Any,' $files
```

Each hit is a candidate for narrowing to `TypeVar`, `TypedDict`, or a concrete type.

### 3. Unannotated public `def` in touched files

```bash
grep -nE '^def [a-z]|^    def [a-z]' $files \
  | grep -v '->' | grep -v '__'
```

Public callables without return annotations are low-cost annotation wins — especially if a downstream caller had to `cast()` the result.

### 4. Intent-scoped additions

- Under `silence`: every site tagged with `# pyright: ignore[...]` in this run's diff appears as a "suggested future widening" entry, rule-grouped:

  ```bash
  grep -rnE "# pyright: ignore\[[A-Za-z]+\]" $files
  ```

- Under `bugs-only`: every site carrying the TODO marker from Phase 3 appears as a suggestion, file-grouped:

  ```bash
  grep -rnF "TODO(types): revisit under --intent improve" $files
  ```

- Under `improve`: the list is usually short — most suggestions were acted on inline.

### 5. Comment-style type annotations in touched files

```bash
grep -rnE '#\s*[Tt]ype:\s*[A-Z]' $files
```

Each hit is a parameter or local declared with a `# Type:` / `# type:` comment in place of a real annotation (e.g. `def f(self, config,  # Type: MapleConfigInput`). These comments are documentation only — pyright treats the bound name as `Any` and silently bypasses type checking on every downstream attribute access of that value.

**High-priority signal** — a single hit often hides large clusters of latent type errors that would otherwise drive `improve`-intent fixes. Suggest converting to a real annotation, using `"Foo"` (string-quoted) if the comment was working around a circular import.

---

## Format each suggestion as

```text
N. <file>:<line>  —  <one-line what>
   How: <one-line how>
   Impact: <concrete outcome — e.g., "removes 14 reportOptionalMemberAccess suppressions",
           "unlocks standard mode on workers/ package", "single source of truth for FastAPI app cast">
```

---

## Sort order (deterministic — same run produces the same ordering)

1. **Group A** — type-checker bypass (signal #5): sort by file path, asc; then line number, asc. Listed first because each hit may unmask a cluster of `improve`-intent work.
2. **Group B** — repetition-driven (signal #1 and the `silence`/`bugs-only` intent-scoped items from signal #4): sort by (a) suppressions/sites removed, desc; (b) total site count, desc; (c) file path, asc.
3. **Group C** — `Any` escapes (signal #2): sort by file path, asc; then line number, asc.
4. **Group D** — missing annotations (signal #3): sort by file path, asc; then line number, asc.

Emit Group A, then Group B, then Group C, then Group D. No cap on length.

---

## Offer to save the suggestions

After printing the suggestions, ask the user:

> **Save these suggestions to a planning file?**
>
> Default path: `docs/exec-plans/active/pyright-improvements-<YYYY-MM-DD-HHMM>.md`
>
> This is a handoff artifact, not source. The project convention is that files under `docs/exec-plans/active/` are NOT committed — they're consumed, then deleted or moved out of `active/`.

If the user accepts:

1. Verify `docs/exec-plans/active/` exists. If it doesn't, create it with `mkdir -p`.
2. Write the suggestion list (verbatim, preserving the numbered format above) to the path, prefaced with this header:

   ```text
   # Pyright improvement suggestions
   Run: <YYYY-MM-DD HH:MM>
   Scope: <scope>
   Level: <level>
   Intent: <intent>
   Source: /pyright:run-and-fix
   ```

3. Report the absolute path to the user.
4. Do **not** `git add` the file. Do **not** suggest committing it. This rule is non-negotiable — see the project convention on exec plans.

If the user declines, do not persist anything. The suggestions are already in the conversation and can be re-generated by re-running the command.
