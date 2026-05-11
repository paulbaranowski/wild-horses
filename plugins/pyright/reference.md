# Pyright learnings

Notes from adopting pyright (`typeCheckingMode = "basic"`) on a large existing Python codebase. Captures what worked, what bit us, and the patterns to reuse next time.

This file is the entry point: it holds the index, the setup/triage process, policy, and dispatch guidance. Rule-specific and library-specific recipes live in sibling files so agents fixing a narrow set of errors don't have to load the whole playbook.

## Quick index

### By pyright rule → `rules.md`

- `reportOptionalMemberAccess` / `reportOptionalSubscript`
- Repeated `self.<attr>` narrowing across awaits
- `reportArgumentType` (includes `cast()` vs `isinstance()` decision rule)
- TypedDict ↔ `dict[str, Any]` asymmetry
- `reportCallIssue` / no overloads match
- `reportTypedDictNotRequiredAccess`
- Narrowing across nested scopes — walrus and rebind
- Stub-runtime type disagreement: widen to `Any`, don't cast
- Reading _undeclared_ keys on a TypedDict
- Discriminated unions with `Literal` + `TypedDict`
- `reportAttributeAccessIssue` on class-level fields
- Attribute typing in `__init__` (starts-None, conditional-init)
- The `def f(x: str = None)` antipattern
- Dataclass mutable defaults
- `Protocol` methods missing `self`
- When to reach for `Protocol` vs `ABC` vs plain duck typing
- `asyncio.gather(..., return_exceptions=True)` returns `BaseException | T`
- Pydantic v1 → v2 field-argument renames
- Pydantic `Field()` positional defaults (including the "Arguments missing" diagnostic)
- `cast(Model, payload)` at pydantic list boundaries
- Schema projection via `model_validate`, not `cast`
- `reportGeneralTypeIssues` / `"None" is not iterable` (also see libraries.md for bitstring, PIL, tornado variants)
- `reportOptionalOperand`
- `reportMissingImports` (also see libraries.md § "Optional-runtime dependencies")
- Third-party library intake flow (typeshed → useLibraryCodeForTypes → --createstub → scoped suppression)
- `TYPE_CHECKING` for type-only imports
- Assigning `bool | None` to a `bool` field
- When `Optional[T] → T` coercion _is_ fine
- Stale `@overload` stacks become noise under strict
- Prefer `@staticmethod` over module-level free functions for class-adjacent helpers

### By library / package → `libraries.md`

- `bitstring` (iteration stubs wrong)
- `scipy.stats` (result types are `_`)
- `tornado` (missing `connection.stream`, `RequestHandler` mixin attributes)
- `matplotlib` (`plt.hist(bins=...)` rejects `np.ndarray`)
- Beanie (MongoDB ODM) — `Indexed`, sort syntax, delete results, Document construction, `.collection`, `List[Self]`, `Document.id` is `PydanticObjectId`
- Supabase — auth narrowing, `.data` cast, `SignInWithIdTokenCredentials`, `created_at` stub/runtime disagreement
- `litellm` (`completion()` union return)
- `pic_prompt` (return-type lies)
- Dynaconf (`Validator(messages={...})`)
- PIL (`ImageCms.profileToProfile` Optional)
- Tenacity (`retry_state.outcome` Optional)
- `pymongo` (`has_error_label` over private attr)
- Pydantic BaseModel fields with TypedDict types enforce at runtime
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
- [Gradual adoption via narrow `include`](#gradual-adoption-via-narrow-include)
- [File-level overrides](#file-level-overrides)
- [Triage process for a large error count](#triage-process-for-a-large-error-count)
- [Prefer a documented API over "works at runtime" tricks](#prefer-a-documented-api-over-works-at-runtime-tricks)
- [`cast(Required, None)` at construction is a refactor signal, not a suppression](#castrequired-none-at-construction-is-a-refactor-signal-not-a-suppression)
- [Type-only by default](#type-only-by-default)
- [Suppression policy](#suppression-policy)
- [Narrowing artifacts vs runtime checks](#narrowing-artifacts-vs-runtime-checks)
- [Harness / CI integration](#harness--ci-integration) (includes Assert vs raise)
- [Parallel agent dispatch pattern](#parallel-agent-dispatch-pattern)
- [Verifying external type findings before acting](#verifying-external-type-findings-before-acting)
- [Verify boundary models against the real producer](#verify-boundary-models-against-the-real-producer)
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

## Gradual adoption via narrow `include`

Turning pyright on project-wide from day one produces a PR too big to review meaningfully — hundreds of errors across dozens of files, each with its own judgment call. The fix is to narrow `include` to one module, land that, and expand the list in follow-up PRs:

```toml
[tool.pyright]
include = ["src/module_a"]     # first module under pyright
typeCheckingMode = "basic"
pythonVersion = "3.13"
```

Each subsequent PR appends a directory to `include` and fixes its errors. Reviewers see one config-line change plus one module's worth of type fixes — not a thousand-file diff.

**Why `include` beats file-level comments for adoption.** `# pyright: strict` / `# pyright: basic` at file tops also works, but it scatters adoption state across N files instead of gathering it in one config block. A reviewer asking "what's currently under pyright?" has to grep for the comments; with `include`, they read one list. File-level comments are the right tool for _mode_ differences within an already-adopted codebase (see § File-level overrides below) — not for drawing the adoption boundary.

**Why not `typeCheckingMode = "off"` + per-file opt-in.** "Off" is the weakest preset, not a full bypass — it still emits syntax-level diagnostics and some basics. Opting files in with `# pyright: strict` over an "off" baseline works mechanically but inverts the normal default-then-override mental model and makes it harder to reason about what's actually checked. `include` keeps the defaults normal.

### Expansion order

When you can, expand by **dependency depth**: start with leaf modules (few internal imports), then the modules that import them, then entry points. This lets each PR stabilize a module's public signatures before the next PR's callers need them. Alternative heuristics (largest-first, by bug frequency, by test coverage) all work, but dependency-depth order minimizes the "signature I just fixed now cascades into the next PR" churn that turns a clean migration into re-litigation.

**Expect a rework pass at the boundary.** When a module enters `include` for the first time, it suddenly sees the tightened types from modules already inside — and those callers may have been silently relying on the old looser signatures. Budget a small pass for this on each expansion PR; don't plan the PR as "just the new module."

### `executionEnvironments` for mixed-strictness inside `include`

If you want different strictness _within_ the adopted set — e.g., `src/` strict, `tests/` basic — use `executionEnvironments` rather than piling per-file comments:

```toml
[tool.pyright]
include = ["src", "tests"]
typeCheckingMode = "basic"

[[tool.pyright.executionEnvironments]]
root = "tests"
reportUnknownMemberType = "none"
reportMissingTypeStubs = "none"
```

Use this for durable policy decisions (the production-vs-tests split is the canonical case), not as a workaround for a single file that isn't clean yet — for the one-file-temporary case, see § File-level overrides.

## File-level overrides

`# pyright: strict`, `# pyright: basic`, and per-rule `# pyright: reportX=none` comments at the top of a file override the project config for that file. Two legitimate uses:

1. **One file should be stricter (or looser) than the project default** — e.g., a security-critical new module under `strict` in an otherwise-`basic` project. The strictness difference is a durable policy decision about this specific file.
2. **Temporarily downgrade a single file** that isn't clean yet but is blocking a different fix. Scoped to the one file, documented at the top, easy to remove later.

**Signals the file-level comment is being misused:**

- Used to exclude a file from pyright entirely — use `exclude` in the config for that. File-level comments adjust _mode_, they don't skip the file.
- Scattered across many files to work around the same cross-cutting pattern — the pattern's fix is upstream (see `rules.md` § "Repetition as a signal" and the intake-flow recipe).
- Used instead of narrowing `include` during adoption — `include` is cleaner for adoption boundaries (see § Gradual adoption above); file-level comments are for mode differences inside the adopted set.

## Triage process for a large error count

When facing hundreds of errors, don't dive in. Bucket first.

1. **Run pyright and save output to a file:** `pyright > /tmp/pyright_full.txt 2>&1`.
2. **Count by rule:** `grep -oE 'report[A-Za-z]+\)' /tmp/pyright_full.txt | sort | uniq -c | sort -rn`. Reveals which patterns dominate.
3. **Count by file:** `grep -E '^  /' /tmp/pyright_full.txt | sed 's|:[0-9].*||' | sort | uniq -c | sort -rn`. Reveals concentration.
4. **Split into disjoint file groups** of roughly equal error count.
5. **Fix production code first, then tests.** Two reasons:
   - **Cascade savings (project-shape-dependent).** When a function's return type becomes precise, tests destructuring that return often start type-checking without further changes. Strong cascades appear in projects with fixture-heavy tests built from prod types; weak or zero cascades in projects where tests hand-build dicts/mocks inline. Don't size Phase B on the cascade assumption — measure the residual after Phase A. One observed adoption moved 290 prod errors → 0 with zero change to the 536 test errors.
   - **Ordering a moving target.** Even when cascades are small, prod-first prevents test agents from encoding whatever prod types exist at their dispatch time — which would then change under them. Test fixes should land against final prod signatures, not in-flight ones.
6. **Parallelize across contributors or agents.** The critical constraint is _disjointness_: two workers editing the same file will conflict. One file per worker is the simplest partition; for big files, one rule per worker on that file also works.

## Prefer a documented API over "works at runtime" tricks

When a call type-errors because it's using a runtime behavior the stubs don't document, first look for a documented alternative that satisfies the stubs — don't reach for `cast` or `# pyright: ignore`.

Example: `requests.Session.cookies.set(name, None)` deletes the cookie at runtime (the `None` is treated as a sentinel), but the stubs type `set()` as `(name: str, value: str | Morsel, ...)`. Options:

1. `cookies.pop(name, None)` — documented delete API, type-clean, semantically identical.
2. `del cookies[name]` — also documented, but raises if absent.
3. `cast(str, None)` or `# pyright: ignore[reportArgumentType]` — hides the fact that there's a cleaner path.

The first path is almost always there. Signals that you're using a "happens to work" trick: the call passes a `None` / sentinel / unusual type that a reader wouldn't predict from the function name, and silencing the type error requires a cast or ignore rather than a narrower local type. In those cases, grep the library's public API (`set`, `pop`, `update`, `clear`, `get`, etc.) for an operation that expresses your intent more directly.

This pattern bleeds over into "bugs pyright uncovers": sometimes the reason the existing call works is purely accidental (e.g., a future library version may tighten the runtime behavior), and moving to the documented API hardens against that too.

## `cast(Required, None)` at construction is a refactor signal, not a suppression

Related to "prefer a documented API" but distinct in shape: when you see a module-level or class-level instance constructed with a cast on a required dependency, the cast is hiding a design smell, not a stub gap.

```python
# Smell: the class requires a Supabase client, but this caller only uses
# the one method that doesn't touch it.
_url_storage = S3Storage(cast(Client, None), settings.BUCKET_NAME)

# Used only for:
image_path = _url_storage.generate_authenticated_image_url(img, "path")
```

The `cast` silences pyright but lies at runtime — `self.supabase = None` works only because the called method reads `self.bucket_name`, not `self.supabase`. It's load-bearing on a coincidence, not a contract.

**Signals this is the pattern:**

1. A constructor's required typed parameter receives `cast(T, None)` (or `cast(T, <sentinel>)`).
2. The resulting instance is used for a proper subset of the class's methods — specifically the methods that don't touch the faked-None attribute.
3. The construction sits at module scope or class scope (a process-long singleton), not inside the function that uses it.
4. A comment explains why the fake value is "safe" — i.e. the class's API is broader than this caller needs.

**Fix: extract the pure method, don't suppress.** Options, roughly in order of preference:

1. **Static method on the same class.** `@staticmethod` that takes what it needs as params (`bucket_name`, etc.) and has the instance method delegate to it. Keeps the conceptual grouping; callers reach it via `ClassName.method(...)` with no instance. See `rules.md` § "Prefer `@staticmethod` over module-level free functions for class-adjacent helpers".
2. **Module-level free function** in the same file as the class, if the helper is genuinely class-independent.
3. **Split the class** into `S3Storage` (needs client) + `S3UrlBuilder` (doesn't). Biggest change; warranted only if the "pure" method set is large.

The `cast(Client, None)` + comment pattern is the smell; the refactor removes both the cast AND the comment in one edit, while letting pyright verify the new constraint structurally. A `# pyright: ignore` would preserve the smell and add a second lie on top.

**Why this belongs in reference.md, not rules.md.** It's not a recipe for a pyright rule — the existing code is type-clean (the cast makes it so). The signal is the _shape_ of the cast + the narrowing-by-subset use pattern. Triggered by reading, not by a rule firing.

## Type-only by default

A pyright fix should change what the type checker sees, not what the program does. Categorize every candidate fix into one of three buckets before applying it:

| Bucket                | Examples                                                                                                                                                                                       | Behavior change?                                                                                   | Default policy                                                                                |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **Type-only**         | `cast(T, x)`; widening a declared type (`Dict` → `Mapping`); adding `Optional` to a declaration to match reality; `# pyright: ignore[rule]`; fixing wrong stubs; reordering `@overload` stacks | None                                                                                               | Apply freely                                                                                  |
| **Narrowing-only**    | `assert x is not None` where `x` is _provably_ non-None at runtime (e.g., guarded by an upstream `is_none → raise` or set in `__init__`)                                                       | None at runtime, but stripped by `python -O` — so any non-trivial check belongs in the next bucket | Apply with care; flag if the assert could plausibly fire                                      |
| **Behavior-changing** | `raise` (any kind); `else: default_value`; `or {}` / `or []`; early `return None`; replacing a duck-typed call with an `isinstance` branch; coercing `bool \| None` → `bool` with `or False`   | Yes — adds, removes, or relocates a runtime side effect                                            | **Stop.** Treat as a behavior change. Justify against the larger system, not against pyright. |

The rule pyright printed does not authorize the fix. Pyright sees one file at a time; behavior-changing fixes have effects that span files. **A behavior change is a behavior change even when pyright asked for it.**

The most common trap: pyright complains that `obj.field` is `Optional[T]` at a call site that requires `T`. The reflexive fix is `if obj.field is None: raise ValueError(...)`. That's a behavior-changing fix — it adds a new exception path that didn't exist before. Whether that's correct depends on whether the rest of the system expects this function to validate, expects something else to validate, or expects the call to never receive None in the first place. Pyright cannot tell you which. Default to a type-only alternative (`cast`, widening) unless you've actively decided the new exception is the right design.

For per-rule guidance on when each bucket is right, see `rules.md` recipes — each recipe heading carries a one-line classifier (e.g. `_(usually type-only; raise variant is behavior-changing)_`) and behavior-changing variants are labeled in the body. For the orchestrator-level grep that surfaces behavior-change tokens after a parallel fix run, see the command file's Phase 4 § "Behavior-change audit" step.

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

## Repetition as a signal

**If you're about to add the same suppression or the same cast for the third time, stop.** Repetition of a fix is a signal that the leverage is upstream — at the source of the type, not the call site. One root-cause edit can delete dozens of point fixes; continuing to suppress just spreads them.

Worked example. Pydantic's positional-None form `Field(None, description="...")` triggers `reportCallIssue` at every construction of the owning model. In a 50-construction codebase, point-fixing adds 50 permanent suppressions; the root-cause edit adds zero. See `rules.md` § "Pydantic `Field()` positional defaults" for the concrete recipe.

Rules of thumb:

1. **Count before suppressing.** If you're inside a partition and already at 3+ identical suppressions, search the whole codebase: `grep -rn "# pyright: ignore\[<rule>\]" .`. For thresholds that trigger an orchestrator-level consolidation pass, see § "Consolidation pass (orchestrator-level)" below.
2. **Cast repetition is the same signal.** `cast(T, {...})` appearing across many call sites almost always means `T` should have a factory (`def make_T(**overrides) -> T`) or a proper constructor call. See `rules.md` § "When to extract a test factory" for the factory pattern and `rules.md` § "The hidden-missing-field trap" for why `cast(T, {literal})` is a code smell.
3. **Cross-partition cooperation beats strict signature-freeze.** Parallel dispatch uses signature-freeze to prevent agents conflicting, not to forbid upstream fixes. If a partition hits a high-repetition pattern, its report should flag "root-cause fix belongs in `<production-file>`" rather than silently suppressing through. The orchestrator can then dispatch a targeted follow-up or consolidate before committing.
4. **Production edits can be staged before the test run.** For known high-repetition patterns (pydantic `Field(None, ...)`, ad-hoc TypedDict construction, missing shared factories), fix them in a pre-pass commit _before_ dispatching the parallel fix work. Saves the entire cycle of "suppress → notice → undo suppressions → root-cause → re-verify."

### Consolidation pass (orchestrator-level)

After parallel dispatch completes, the orchestrator runs a consolidation pass before full verification. Individual partition agents cannot see cross-partition repetition; the orchestrator can. This is the procedure the command's Phase 3.5 follows.

Scan the changed files (not the whole project — this is about _what the run produced_, not pre-existing suppressions):

```bash
# list Python files the run touched
files=$(git diff --name-only -- '*.py')

# count suppressions added, by rule
grep -rhE "pyright: ignore\[[a-zA-Z]+\]" $files \
  | grep -oE "pyright: ignore\[[a-zA-Z]+\]" \
  | sort | uniq -c | sort -rn | head

# count casts added, by target type
grep -rhE "cast\([A-Z][A-Za-z_]+," $files \
  | grep -oE "cast\([A-Z][A-Za-z_]+," \
  | sort | uniq -c | sort -rn | head

# count re-bind narrowing blocks added, by file
# (heuristic — pre-filter for `<local> = self.<attr>` lines; verify each file's class
#  membership and the following `if <local> is None: raise` by reading the hits)
for f in $files; do
  n=$(grep -cE '^[[:space:]]+[a-z_]+ = self\.[a-z_]+$' "$f")
  [ "$n" -ge 2 ] && echo "$n $f"
done | sort -rn | head
```

**Thresholds (pause and investigate if any fire):**

- **Same suppression rule ≥ 10 sites** → probable single upstream cause (pydantic/Beanie `Field(None, ...)`, a class missing a type annotation, a library-stub gap fixable with one root-cause edit). See § "Repetition as a signal" above.
- **Same `cast(T, ...)` target type ≥ 5 sites** → probable missing factory. See `rules.md` § "When to extract a test factory."
- **Same class with ≥ 2 re-bind narrowing blocks** (`<local> = self.<attr>` followed by `if <local> is None: raise`) → probable misplaced invariant. Two fixes (see `rules.md` § "Repeated `self.<attr>` narrowing across awaits"): lift to `__init__` when the attribute is immutable post-construction; consolidate to one validation point when it's mutated mid-method. Threshold is lower than the suppression/cast counts because each re-bind block is 3-4 lines and the fix is high-leverage — `__init__` validation eliminates the dance from every method on the class.
- **Inconsistency across partitions** — if two agents' reports describe the same problem with different fixes (e.g., one used `cast(FastAPI, self.client.app)`, another used `from app.main import app`), unify on one approach before committing. Diverging solutions to the same problem age badly.

If a threshold fires:

1. Present the counts to the user with `file:line` pointers to a representative sample.
2. Propose the root-cause fix as a separate commit (often a 10-line production change that deletes dozens of downstream suppressions).
3. Once the user decides, either apply the root-cause fix (and remove the now-dead suppressions) or mark explicitly as "accepted, will revisit at ratchet time."
4. Re-run full verification after any consolidation.

If no threshold fires, log "consolidation pass: clean" in the summary and proceed to verification.

### Before dict-shape extraction: check for a data-access class

When the opaque dict comes from — or is returned by — a class method shaped like data access, that class is where the model belongs, not the consumer that reads the dict. Ask this **before** walking the `rules.md` § "Opaque dict[str, Any]" decision tree: **"Is there already a manager / repository / DAO / CRUD class returning raw dicts for this data?"**

**Signal patterns:**

- `class FooManager: def read(...) -> Optional[dict[str, Any]]: ...`
- `class FooRepository: def find_by(...) -> list[dict[str, Any]]: ...`
- A CRUD-shaped class (`upsert`, `insert`, `delete`, `query`) returning raw dicts
- Callers writing `result["field"]` on the return value of such a method

**Fix:** define a Pydantic `BaseModel` for the row shape; change the method's return type; call `Model.model_validate(...)` inside the method. Consumers switch from `result["field"]` to `result.field`.

The model + manager pair forms a "document class"-equivalent — the analog to Beanie's `Document` or SQLAlchemy's declarative classes for data stores that don't have a first-party ODM. **Codebases that have this pattern on one data store and raw dict-shuffling on another are showing a structural asymmetry the extraction closes.** Cross-DB inconsistency is diagnostic: the weaker layer is almost always the one that needs the upgrade.

**Why this is the first question, not a later one.** The `rules.md` decision tree asks about mutation, schema stability, and pydantic availability — all useful, but all answered at the _call site_. When the real fix is at the manager, walking the tree misroutes the work: an agent ends up optimizing the local cast, adding a TypedDict, or building a helper, when one architectural edit at the manager would have deleted every downstream cast. Checking for the manager first collapses N local fixes into 1.

**When the answer is "no":** there's no existing manager/repository class, and the dict flows ad-hoc through a function. Fall through to the `rules.md` decision tree — the boundary-vs-internal split in step 0 still applies, but the fix lives at the function, not at a missing class. (If the data crosses a service boundary and is ad-hoc, consider whether _introducing_ a manager is in scope for the run — usually out of scope for an `--improve` pass, but worth flagging in the end-of-run report.)

### Removing suppressions is a bug-finding operation

A non-trivial fraction of any accumulated `# pyright: ignore[<rule>]` suppression pattern turns out to be hiding real bugs, not just stub noise. Observed range: 5–10% across a single-rule bulk migration. Example shapes:

- String literals passed where enums were declared (`tier="paid"` where `UserTier.PAID` was needed).
- Wrong numeric types (`"12.34"` where `Decimal` was declared).
- Intentional-bad-input tests where pydantic's runtime coercion was silently saving the test from exercising what it intended to exercise.
- Function calls missing parameters that the suppression had been silencing for so long nobody remembered why.

**Plan for the surface.** After a root-cause migration that deletes a suppression pattern, re-run pyright expecting _new_ errors, not a stable zero. Those errors are the bug-finding payoff of the migration — treat them as findings to fix, not "my change broke something." Budget time for this pass when estimating the work; a migration that looks like "search-replace, run tests, done" will surprise you when the type-checker surfaces 5–10% more work behind where the suppressions were.

**Also re-run tests, not just pyright.** Some of the surfaced bugs are runtime validation issues (e.g., pydantic TypedDict validation — see `libraries.md` § "Pydantic BaseModel fields with TypedDict types enforce at runtime") that the type-checker alone won't catch.

### Defensive tests often become redundant after boundary-model extraction

When a test suite has multiple tests checking "what if the input dict is missing a field," "what if a field is `None`," "what if the dict is empty," those are usually a signal that the input should be a validated model — the tests describe scenarios the type system can't rule out, so they're scaffolding around a missing schema.

After extracting a Pydantic model at the **producing** layer (`model_validate` inside a manager's `read()` / `query()` / etc.), those scenarios become structurally impossible at the consumer — the manager either returns a valid model or `None`, never a malformed dict. The defensive tests can usually be deleted.

**Check before deleting:** is the test verifying the _manager's_ behavior (malformed row → `None` or logged `ValidationError`) or the _consumer's_ behavior (malformed dict → safe fallback)? The former stays (and may be worth adding if it doesn't already exist). The latter was scaffolding around an absent schema that now exists.

**Practical consequence:** type-tightening PRs that add a boundary model often **shrink** the test count while improving safety. Don't treat the negative delta as a red flag — it's the right-sizing of a suite that was compensating for a missing schema. Report it in the end-of-run summary as part of the architectural win, not buried as a side effect.

This is the same shape as "Removing suppressions is a bug-finding operation" above, one abstraction layer up: both describe work the root-cause fix deletes, not work it creates. The suppressions were hiding bugs; the defensive tests were hiding the architectural gap.

### Grep for stale comments after type tightening

When you change a function's return type or a field's annotation from a loose type (`dict`, `Dict[str, Any]`) to a specific one (`ImageRecord`, `UserProfile`), nearby comments that narrate the old type become lies. Typical phrasings to sweep:

```
# function X returns Dict[str, Any]; the actual shape matches ImageRecord
# typed as ImageRecord at call sites
# shape matches the ImageRecord TypedDict at runtime
```

These comments were true when written and become actively misleading once the declaration tightens — exactly where future readers look for context. Practical grep:

```bash
grep -rn "returns Dict\|returns dict\|typed as .* at call sites\|shape matches" <changed-dirs>
```

Delete or update each match. Also worth a look: removed `cast(T, x)` calls sometimes left a comment upstream explaining _why_ the cast was needed. If the cast is gone, the comment often should be too.

**Docstrings are a separate sweep.** The comment-drift pattern above covers narrative `#`-style comments. Docstrings are their own class of staleness because `Args:` / `Returns:` blocks describe parameters by _type name_ and don't auto-update when the signature tightens. Typical rot:

```python
# Signature after tightening
def process_raw_event(raw_event: RawScrapedEvent, input_dir: Path) -> EventData:
    """Process a raw event dictionary into structured EventData.

    Args:
        raw_event: Raw event data (dict) from HTML parser
        ...
    """
```

The parameter became a dataclass (or pydantic model, or TypedDict) but the prose still says "dict" / "dictionary" / "raw dict from X". Pyright can't flag this — docstrings are plain text. Worth a post-tightening pass:

```bash
grep -rn 'dict\|tuple\|Dict\[\|Tuple\[' <changed-files>   # review Args: blocks and one-liners
```

The false-positive rate is high (legitimate uses of those words), but each match is cheap to eyeball. Focus on files that changed in the type-tightening PR — drift is strongly correlated with signatures that moved in the same diff.

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
- `"known_field"` _is_ a declared key, so `data.get("known_field", 0)` type-checks without narrowing.

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

A naive setup runs pyright as a _pre-edit_ or _per-edit_ hook that blocks on errors. That's the wrong level for multi-file refactors: intermediate states are broken by construction (remove a symbol's definition, then remove its usages) and the hook refuses to let work progress.

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
6. **Foundational files need a stability constraint, not just disjointness.** When one agent owns `api.py`, `core/base.py`, or an exception module that the other agents' files import, disjointness alone isn't enough — a "local" signature widening ripples into the other partitions. Add to the foundational-file agent's prompt: _prefer adding types over changing them; do not widen or narrow public signatures_. Otherwise a change like `def f(x: str) -> T` → `def f(x: str | None) -> T` looks safe locally but requires every caller in the other partitions to be updated, and those files are off-limits. The contract: partition owns the right to _edit_, but public signatures are frozen for the duration of the dispatch. This matters most under strict mode, where the dominant "Unknown" rule family fires at definition sites and tempts signature-widening fixes.
7. **Require each agent to return a "cascading changes" section.** Agents that change public signatures, widen return types, or modify shared Protocols inside their partition _will_ affect files outside it — disjointness doesn't eliminate type cascades. Ask each agent to list, in its final report: "signature/schema changes worth noting because they may cascade to other groups." This gives the orchestrator a decision point _before_ the full-project re-run rather than hunting down surprise errors afterward. Observed value: one refactor's agents independently flagged Protocol-method changes, `List→Sequence` widenings, and exception-constant call-site rewrites that would have otherwise surfaced as unexplained errors in the verification run.
8. **Dispatch granularity: ~70–80 errors per agent × 4 agents is a good default for mid-sized projects.** Smaller groups (3 × ~100 errors) give less parallelism and each agent takes longer; larger (6 × ~50 errors) increases cross-partition conflict risk as agents make structurally similar schema changes independently (e.g., two agents both updating the same Protocol from different sides). 4 agents is also the sweet spot for monitoring — you can read each final report without losing track.
9. **Split the error file with `grep -F` (fixed-string), not `-E`.** File paths contain `.` and `/` characters that pattern-grep interprets as regex. `grep -F "/${file}:" /tmp/pyright_full.txt` pins the filename literally and avoids silent under-matches that produce empty per-group files. Also guard against blank/comment lines in the file list: `case "$f" in ""|"#"*) continue;; esac`.
10. **Orchestrator owns cross-partition quality; agents own in-partition correctness.** An agent whose partition reaches zero errors has done its job — but reaching zero doesn't make the aggregate good. Only the orchestrator sees all partition reports together, and only the orchestrator can notice that multiple agents added many identical suppressions (one root cause, many duplicate point-fixes) or that two agents picked different solutions for the same underlying problem. Agents that try to do orchestrator work — e.g., editing outside their partition to achieve a root-cause fix — break the dispatch contract and produce merge conflicts. Orchestrators that don't reconcile — trusting N independent zero-error reports as a zero-error whole — produce technically-green runs whose aggregate quality is poor: duplicate suppressions scattered across files, inconsistent fixes for the same upstream issue, repeated casts that should be factories. The green count hides the debt. Concretely: between Phase 3 (agent work) and Phase 4 (full-project verification), run the suppression/cast counts described in the command file's Phase 3.5 and decide whether to consolidate before committing.
11. **Reconciliation signals come from the agent reports themselves.** Before touching any grep, scan the cascading-changes sections (§7) across agents. Two agents independently flagging the same library workaround, the same TypedDict construction issue, or the same widening opportunity is the strongest possible signal that a cross-cutting fix is warranted. The agents' own reports often surface consolidation candidates more cheaply than a grep pass — the grep is the fallback for patterns agents didn't notice were shared.

## Verifying external type findings before acting

Code-review tools and LLM-generated PR comments produce type-flavoured findings that sound authoritative but are frequently wrong in ways the type checker can adjudicate in seconds. The type checker is the ground truth for claims about types; treat findings as hypotheses and check them before rewriting code.

General verification sequence:

1. **Run pyright on the targeted file first.** If it reports no errors for the cited code, the claimed type problem does not exist. Stop there — no rewrite needed.
2. **Read the cited line.** Line numbers drift with rebases and branch movement; findings often point at code that no longer matches the description (e.g. a finding describing `bool(x)` coercion pointing at a line that just passes `x` through).
3. **Check the actual class, not the claimed one.** Findings commonly confuse `TypedDict` with pydantic `BaseModel`, `NamedTuple` with `dataclass`, or protocol with ABC. A "fix" proposing `.model_validate()` on a TypedDict, or `.copy(deep=True)` on a NamedTuple, will fail at runtime. Read the class definition before acting on any method-call-style suggestion.
4. **Test empirically, then revert if wrong.** When a finding claims "cast X is unnecessary" or "assert Y is unnecessary," remove it and rerun pyright. If errors appear, the construct was load-bearing — revert. This beats reasoning about third-party stubs from memory (they change between stub versions, and pyright's resolution differs from mypy's).
5. **Weigh findings against project conventions.** Suggestions to add defensive `or []` / explicit `if x is None: raise` / preemptive `isinstance` often conflict with a "don't defend against impossible states" stance. Re-check the project's contributor guide before scattering guards.

Findings framed as prescriptive fixes ("replace X with Y") tempt direct implementation. Reframe them as claims: "the fix's author believes X is wrong. What's the evidence?" Most fail the evidence test.

## Verify boundary models against the real producer

After extracting a Pydantic `BaseModel` for data from an external producer (DB client, HTTP response, subprocess, file parse), the model reflects **inferred** shape — what you could read from existing consumer code. The producer's actual shape may differ:

- **Extra columns / fields** the code never read (benign — Pydantic drops them by default, but could flag columns you'd want to expose)
- **Type coercions at the client layer** — e.g., Supabase's Python client may hand back `datetime` as ISO-8601 string (not `datetime` object), `uuid` as `str` (not `UUID`), `numeric` as `Decimal` (not `float`). Each becomes a `ValidationError` unless the model accepts the shape the client actually produces
- **Edge-case nulls** — a column declared `NOT NULL` in the DB might still arrive as `None` in specific cases (aggregations, left joins, RLS-filtered reads that coalesce)

### Standard recipe

Before committing a boundary-model refactor, run a one-off verification script that:

1. Reads one live row from the producer
2. Prints the symmetric diff of keys (producer vs. model)
3. Prints each value's runtime Python type
4. Calls `Model.model_validate(row)` and reports success or the full `ValidationError`
5. Exits non-zero if validation fails (so it can be reused as a manual CI smoke-check if the producer is reachable)

Keep the script in-tree as `verify_<model>_schema.py` (or similar) — it's a manual-sanity tool, not a test fixture. Don't run it in CI unless the producer is reachable from CI; do run it locally after any boundary-model change or whenever the producer's schema drifts.

### Why `model_validate`'s result is the real pass/fail

The key-set diff is a **diagnostic** — it tells you where the model and producer disagree on which fields exist. But "a key is missing from the producer" isn't necessarily a bug: Pydantic-optional fields with defaults handle that case. The binding question is **"does the real row pass the declared validators?"** If yes, the model is structurally safe even when columns don't match exactly; if no, Pydantic's error message names the specific bad field — a diagnostic gift a `cast`-based approach doesn't provide.

### Empty-producer trap

The script prints "(no rows found)" when the test user / account has no data in the table. Easy to misread as "all good" — it isn't. Either seed a row first (many codebases already have `__main__` blocks or fixture scripts that do this) or have the script **error out loudly** when it finds zero rows. Silent-pass on empty producers is a real footgun; the whole point of this step is to hit the producer.

### When to run

Boundary-model verification is model-generation-complete, not pyright-complete — pyright can't see whether a Pydantic model matches a runtime producer, because the producer isn't in scope for static analysis. This step belongs to the `--improve` flow, not the basic/standard/strict error-fix flow. Specifically: after the model is written and before the refactor is considered done. An agent completing an opaque-dict → BaseModel extraction at a producer boundary (`rules.md` § "Opaque dict" step 0) should surface this step in the end-of-run report even if the verification requires human execution (e.g., the producer isn't reachable from the agent's environment).

## If your editor / hook auto-fixes on save

Tools that run autofixers on every edit (ruff with `--fix`, isort, IDE format-on-save, or harness-level post-edit hooks) are helpful for net-forward progress but hostile to "edit, observe, revert" exploration. The specific trap:

1. You remove a symbol (e.g. a `cast(Any, ...)` wrapper) to test whether it was load-bearing.
2. The autofixer detects the now-unused import and strips it.
3. Your change turns out to be wrong. You revert the edit.
4. The revert restores the symbol use but not the import the autofixer removed. The type checker now reports a _different_ error (`reportUndefinedVariable`) that looks unrelated to the original finding.

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
