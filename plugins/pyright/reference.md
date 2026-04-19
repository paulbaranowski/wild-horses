# Pyright learnings

Notes from adopting pyright (`typeCheckingMode = "basic"`) on a large existing Python codebase. Captures what worked, what bit us, and the patterns to reuse next time.

This file is the entry point: it holds the index, the setup/triage process, policy, and dispatch guidance. Rule-specific and library-specific recipes live in sibling files so agents fixing a narrow set of errors don't have to load the whole playbook.

## Quick index

### By pyright rule → `rules.md`

- `reportOptionalMemberAccess` / `reportOptionalSubscript`
- `reportArgumentType` (includes `cast()` vs `isinstance()` decision rule)
- TypedDict ↔ `dict[str, Any]` asymmetry
- `reportCallIssue` / no overloads match
- `reportTypedDictNotRequiredAccess`
- Reading *undeclared* keys on a TypedDict
- `reportAttributeAccessIssue` on class-level fields
- Attribute typing in `__init__` (starts-None, conditional-init)
- The `def f(x: str = None)` antipattern
- Dataclass mutable defaults
- `Protocol` methods missing `self`
- `asyncio.gather(..., return_exceptions=True)` returns `BaseException | T`
- Pydantic v1 → v2 field-argument renames
- `cast(Model, payload)` at pydantic list boundaries
- `reportGeneralTypeIssues` / `"None" is not iterable` (also see libraries.md for bitstring, PIL, tornado variants)
- `reportOptionalOperand`
- `reportMissingImports` (also see libraries.md § "Optional-runtime dependencies")
- Assigning `bool | None` to a `bool` field
- When `Optional[T] → T` coercion *is* fine
- Stale `@overload` stacks become noise under strict

### By library / package → `libraries.md`

- `bitstring` (iteration stubs wrong)
- `scipy.stats` (result types are `_`)
- `tornado` (missing `connection.stream`, `RequestHandler` mixin attributes)
- `matplotlib` (`plt.hist(bins=...)` rejects `np.ndarray`)
- Beanie (MongoDB ODM) — `Indexed`, sort syntax, delete results, Document construction, `.collection`, `List[Self]`
- Supabase — auth narrowing, `.data` cast, `SignInWithIdTokenCredentials`
- `litellm` (`completion()` union return)
- `pic_prompt` (return-type lies)
- Dynaconf (`Validator(messages={...})`)
- PIL (`ImageCms.profileToProfile` Optional)
- Tenacity (`retry_state.outcome` Optional)
- `pymongo` (`has_error_label` over private attr)
- Optional-runtime dependencies (inline import + suppress)

### Bug classes pyright uncovers → `bugs.md`

Real bugs pyright surfaces (not recipes — flag these for user review, don't silence):

- Attribute read that never existed
- Subclass attribute shadows inherited method
- Repeated side-effectful call in a loop
- Dead field referenced through a suppressed `# pyright: ignore`
- Dead module / class constants
- Reversed dict-direction lookups
- Parameter the callee doesn't accept

### By topic → this file

- [Setup](#setup)
- [Triage process for a large error count](#triage-process-for-a-large-error-count)
- [Prefer a documented API over "works at runtime" tricks](#prefer-a-documented-api-over-works-at-runtime-tricks)
- [Suppression policy](#suppression-policy)
- [Narrowing artifacts vs runtime checks](#narrowing-artifacts-vs-runtime-checks)
- [Harness / CI integration](#harness--ci-integration) (includes Assert vs raise)
- [Parallel agent dispatch pattern](#parallel-agent-dispatch-pattern)
- [Verifying external type findings before acting](#verifying-external-type-findings-before-acting)
- [If your editor / hook auto-fixes on save](#if-your-editor--hook-auto-fixes-on-save)
- [Serialization backward compat when removing fields](#serialization-backward-compat-when-removing-fields)
- [Configuration intent as source of truth](#configuration-intent-as-source-of-truth)

## Setup

In `pyproject.toml`:

```toml
[tool.pyright]
include = ["src", "tests"]   # adjust to your layout
typeCheckingMode = "basic"
pythonVersion = "3.13"        # match your runtime
```

`basic` mode catches real bugs (undefined attributes, wrong argument counts, Optional misuse) without the noise of `strict` mode's "everything must be explicitly typed" enforcement. Right starting point for retrofitting types onto an existing codebase; consider `standard` or `strict` as a future tightening.

Install pyright as a dev dependency with whatever package manager the project uses (`uv`, `poetry`, `pip`, etc.).

## Triage process for a large error count

When facing hundreds of errors, don't dive in. Bucket first.

1. **Run pyright and save output to a file:** `pyright > /tmp/pyright_full.txt 2>&1`.
2. **Count by rule:** `grep -oE 'report[A-Za-z]+\)' /tmp/pyright_full.txt | sort | uniq -c | sort -rn`. Reveals which patterns dominate.
3. **Count by file:** `grep -E '^  /' /tmp/pyright_full.txt | sed 's|:[0-9].*||' | sort | uniq -c | sort -rn`. Reveals concentration.
4. **Split into disjoint file groups** of roughly equal error count.
5. **Fix production code first, then tests.** Two reasons:
   - **Cascade savings (project-shape-dependent).** When a function's return type becomes precise, tests destructuring that return often start type-checking without further changes. Strong cascades appear in projects with fixture-heavy tests built from prod types; weak or zero cascades in projects where tests hand-build dicts/mocks inline. Don't size Phase B on the cascade assumption — measure the residual after Phase A. One observed adoption moved 290 prod errors → 0 with zero change to the 536 test errors.
   - **Ordering a moving target.** Even when cascades are small, prod-first prevents test agents from encoding whatever prod types exist at their dispatch time — which would then change under them. Test fixes should land against final prod signatures, not in-flight ones.
6. **Parallelize across contributors or agents.** The critical constraint is *disjointness*: two workers editing the same file will conflict. One file per worker is the simplest partition; for big files, one rule per worker on that file also works.

## Prefer a documented API over "works at runtime" tricks

When a call type-errors because it's using a runtime behavior the stubs don't document, first look for a documented alternative that satisfies the stubs — don't reach for `cast` or `# pyright: ignore`.

Example: `requests.Session.cookies.set(name, None)` deletes the cookie at runtime (the `None` is treated as a sentinel), but the stubs type `set()` as `(name: str, value: str | Morsel, ...)`. Options:

1. `cookies.pop(name, None)` — documented delete API, type-clean, semantically identical.
2. `del cookies[name]` — also documented, but raises if absent.
3. `cast(str, None)` or `# pyright: ignore[reportArgumentType]` — hides the fact that there's a cleaner path.

The first path is almost always there. Signals that you're using a "happens to work" trick: the call passes a `None` / sentinel / unusual type that a reader wouldn't predict from the function name, and silencing the type error requires a cast or ignore rather than a narrower local type. In those cases, grep the library's public API (`set`, `pop`, `update`, `clear`, `get`, etc.) for an operation that expresses your intent more directly.

This pattern bleeds over into "bugs pyright uncovers": sometimes the reason the existing call works is purely accidental (e.g., a future library version may tighten the runtime behavior), and moving to the documented API hardens against that too.

## Suppression policy

A `# pyright: ignore[...]` is acceptable when:
- The issue is in a third-party library's type stubs, not your code.
- The runtime behavior is verified (tests pass) and the type checker is the one that's wrong.
- The alternative (restructuring just to satisfy the type checker) would make the code worse.

Not acceptable:
- Bare `# pyright: ignore` with no rule name. Always specify: `# pyright: ignore[reportOptionalMemberAccess]`.
- `# type: ignore` with or without brackets. That's mypy syntax. Pyright uses `# pyright: ignore[...]`.
- Suppressing a rule to avoid fixing a real bug. If pyright flags a `None` access, either narrow or fix the declared type. Don't paper over it.

When suppressing, add a one-line comment explaining WHY if the reason isn't obvious from the rule name:
```python
# bitstring's stubs declare __iter__ wrong; iteration works at runtime
for bit in bits:  # pyright: ignore[reportGeneralTypeIssues]
```

## Narrowing artifacts vs runtime checks

Not every `isinstance(x, T)` or `if x is None: return` in a codebase is a runtime defense — some exist purely to narrow a type for pyright. The two look identical but have different implications for change and review.

Illustrative shape:

```python
err = data.get("error") if isinstance(data, dict) else None  # pyright: ignore[reportUnknownMemberType]
...
table.add_row("known_field", str(data.get("known_field", 0)))
```

The `isinstance(data, dict)` on the first line looks defensive against a non-dict response, but the second line uses `data.get(...)` without any guard. That asymmetry is not a bug — it's a narrowing artifact:

- `data` is typed as a `TypedDict` with known keys.
- `"error"` is not a declared key, so `data.get("error")` triggers `reportUnknownMemberType`.
- `isinstance(data, dict)` narrows to plain `dict` where `.get(<arbitrary-key>)` is typed `Any | None` — silences the warning.
- `"known_field"` *is* a declared key, so `data.get("known_field", 0)` type-checks without narrowing.

**Signals that an isinstance/None check is a pyright artifact rather than runtime defense:**

1. A trailing `# pyright: ignore[...]` comment on the same line.
2. `git blame` points to a pyright-adoption / pyright-fix commit rather than a bug-fix or hardening commit.
3. Inconsistent application — guard present here, absent on an adjacent access of the same variable.
4. The "untyped" branch returns a value that immediately routes into code that assumes the typed shape anyway (so the guard doesn't prevent a crash, only relocates it).

**Implication for review:** a finding that says "this guard is asymmetric, add matching guards elsewhere" is often wrong when the guard is an artifact. Adding more `isinstance` checks to match the artifact's style doesn't improve safety, it spreads the artifact. The right response is to notice the pattern, verify via blame/ignore-comment, and push back on the finding.

## Harness / CI integration

### CI

```yaml
# in your CI config, e.g. .github/workflows/ci.yml
- run: pyright
```

Pyright exits non-zero on any error; no extra flag needed.

### If using an LLM coding harness (Claude Code, Cursor, etc.)

A naive setup runs pyright as a *pre-edit* or *per-edit* hook that blocks on errors. That's the wrong level for multi-file refactors: intermediate states are broken by construction (remove a symbol's definition, then remove its usages) and the hook refuses to let work progress.

Better design:

- **Auto-formatter / linter auto-fix stays on per-edit.** Fast, non-blocking, auto-fixes. Incremental format-as-you-go is better than a big end-of-turn batch where the formatter might remove unused imports the next edit was about to reference.
- **Pyright moves to end-of-turn (a `Stop`-style hook).** Runs once on the whole project. Non-zero exit tells the agent "you're not done, here's what to fix." Mid-refactor intermediate states no longer block.

Example (Claude Code `.claude/settings.json`):

```json
"Stop": [{
  "hooks": [{
    "type": "command",
    "command": "out=$(cd \"$CLAUDE_PROJECT_DIR\" && pyright 2>&1); rc=$?; if [ $rc -ne 0 ]; then echo \"$out\" >&2; exit 2; fi",
    "timeout": 120,
    "statusMessage": "Running pyright..."
  }]
}]
```

Adapt the command to the project's package manager and to the harness's hook semantics.

### Assert vs raise for type narrowing

`assert x is not None` and `if x is None: raise ...` both narrow for pyright. Which to use:

- **`assert`** for invariants the type checker needs to see (e.g. the line above just assigned `self._x = SomeValue`, and pyright doesn't follow that through a method call). Documents intent, no runtime safety concern.
- **`raise`** for genuinely-invalid states that should fail loudly at runtime, including under `python -O` which strips asserts.

For public library code, or code that might run with `-O` enabled, prefer explicit raises. For internal invariants that defend pyright's narrowing analysis, `assert` is fine (and more concise), but remember it disappears under `-O`.

## Parallel agent dispatch pattern

For large bulk fixes, the reusable pattern:

1. Bucket errors by rule and file.
2. Define disjoint file groups for each parallel agent. Each agent must own its files end-to-end.
3. Give each agent: (a) the specific file list, (b) a pointer to pre-split per-file error lists on disk, (c) the fix-pattern recipes (`rules.md`, `libraries.md`), (d) project conventions (line length, naming, formatting), (e) validation steps (`pyright <files>`, the project's linter, the test runner).
4. Dispatch agents in parallel rather than sequentially so they start together.
5. Wait for all to complete before the next phase. Check overall pyright count in between phases, not just trust the reports.
6. **Foundational files need a stability constraint, not just disjointness.** When one agent owns `api.py`, `core/base.py`, or an exception module that the other agents' files import, disjointness alone isn't enough — a "local" signature widening ripples into the other partitions. Add to the foundational-file agent's prompt: *prefer adding types over changing them; do not widen or narrow public signatures*. Otherwise a change like `def f(x: str) -> T` → `def f(x: str | None) -> T` looks safe locally but requires every caller in the other partitions to be updated, and those files are off-limits. The contract: partition owns the right to *edit*, but public signatures are frozen for the duration of the dispatch. This matters most under strict mode, where the dominant "Unknown" rule family fires at definition sites and tempts signature-widening fixes.
7. **Require each agent to return a "cascading changes" section.** Agents that change public signatures, widen return types, or modify shared Protocols inside their partition *will* affect files outside it — disjointness doesn't eliminate type cascades. Ask each agent to list, in its final report: "signature/schema changes worth noting because they may cascade to other groups." This gives the orchestrator a decision point *before* the full-project re-run rather than hunting down surprise errors afterwards. Observed value: one refactor's agents independently flagged Protocol-method changes, `List→Sequence` widenings, and exception-constant call-site rewrites that would have otherwise surfaced as unexplained errors in the verification run.
8. **Dispatch granularity: ~70–80 errors per agent × 4 agents is a good default for mid-sized projects.** Smaller groups (3 × ~100 errors) give less parallelism and each agent takes longer; larger (6 × ~50 errors) increases cross-partition conflict risk as agents make structurally similar schema changes independently (e.g., two agents both updating the same Protocol from different sides). 4 agents is also the sweet spot for monitoring — you can read each final report without losing track.
9. **Split the error file with `grep -F` (fixed-string), not `-E`.** File paths contain `.` and `/` characters that pattern-grep interprets as regex. `grep -F "/${file}:" /tmp/pyright_full.txt` pins the filename literally and avoids silent under-matches that produce empty per-group files. Also guard against blank/comment lines in the file list: `case "$f" in ""|"#"*) continue;; esac`.

## Verifying external type findings before acting

Code-review tools and LLM-generated PR comments produce type-flavoured findings that sound authoritative but are frequently wrong in ways the type checker can adjudicate in seconds. The type checker is the ground truth for claims about types; treat findings as hypotheses and check them before rewriting code.

General verification sequence:

1. **Run pyright on the targeted file first.** If it reports no errors for the cited code, the claimed type problem does not exist. Stop there — no rewrite needed.
2. **Read the cited line.** Line numbers drift with rebases and branch movement; findings often point at code that no longer matches the description (e.g. a finding describing `bool(x)` coercion pointing at a line that just passes `x` through).
3. **Check the actual class, not the claimed one.** Findings commonly confuse `TypedDict` with pydantic `BaseModel`, `NamedTuple` with `dataclass`, or protocol with ABC. A "fix" proposing `.model_validate()` on a TypedDict, or `.copy(deep=True)` on a NamedTuple, will fail at runtime. Read the class definition before acting on any method-call-style suggestion.
4. **Test empirically, then revert if wrong.** When a finding claims "cast X is unnecessary" or "assert Y is unnecessary," remove it and rerun pyright. If errors appear, the construct was load-bearing — revert. This beats reasoning about third-party stubs from memory (they change between stub versions, and pyright's resolution differs from mypy's).
5. **Weigh findings against project conventions.** Suggestions to add defensive `or []` / explicit `if x is None: raise` / preemptive `isinstance` often conflict with a "don't defend against impossible states" stance. Re-check the project's contributor guide before scattering guards.

Findings framed as prescriptive fixes ("replace X with Y") tempt direct implementation. Reframe them as claims: "the fix's author believes X is wrong. What's the evidence?" Most fail the evidence test.

## If your editor / hook auto-fixes on save

Tools that run autofixers on every edit (ruff with `--fix`, isort, IDE format-on-save, or harness-level post-edit hooks) are helpful for net-forward progress but hostile to "edit, observe, revert" exploration. The specific trap:

1. You remove a symbol (e.g. a `cast(Any, ...)` wrapper) to test whether it was load-bearing.
2. The autofixer detects the now-unused import and strips it.
3. Your change turns out to be wrong. You revert the edit.
4. The revert restores the symbol use but not the import the autofixer removed. The type checker now reports a *different* error (`reportUndefinedVariable`) that looks unrelated to the original finding.

This applies beyond imports — any autofix that cleans up "unused" artifacts (variables, parameters, `# noqa` lines whose flagged rule no longer triggers) can leave a reverted file in a broken state the autofix-free version would have preserved. Two mitigations:

- After reverting an exploratory edit, re-check the file against the pre-edit state, not against what you typed in the revert. Imports and unused-artifact suppressions are the usual casualties.
- For findings that touch a symbol used at only one call site, reason through the claim or test on a scratch copy before editing the real file. The autofix can't unwind what wasn't edited.

## Serialization backward compat when removing fields

Removing a field from a dataclass that has a `from_dict` classmethod is backward-compatible IF the `from_dict` uses `.get(key, default)` for every field and ignores unknown keys. Old saved JSON containing the removed key will still load: the extra key is ignored by `from_dict`.

Not safe: if `from_dict` used `d["field"]` directly, removing the field while old saved data exists would raise `KeyError`.

Before removing any field referenced by persisted data, audit the loader path.

## Configuration intent as source of truth

Sometimes the codebase has two contradictory statements about a field:

- **Docstring:** "feature X is always on and not configurable."
- **Code:** reads `config.x` in several places.

When these conflict, the docstring is usually a deliberate design statement and the code is vestigial. Match the code to the docstring (remove the vestigial references), not the other way around (add `x` to the dataclass just to make the code valid). The docstring represents intent; the code may represent history.

Corollary: when you see `# pyright: ignore` suppressing "attribute does not exist" on a field that should exist, investigate whether the field belongs on the class or whether the reference is dead code. The suppression is a signal, not a fix. See `bugs.md` § "Dead field referenced through a `# type: ignore` or `# pyright: ignore`" for the bug-class framing.
