# pyright

Run pyright on a Python codebase and fix what it finds, using a documented playbook instead of ad-hoc guesses.

Install:

```text
/plugin install pyright@wild-horses
```

## Command

### `/pyright:run-and-fix`

One command drives the whole flow: detect config, run pyright, triage errors, fix them using rule-specific recipes, verify, and summarize.

```text
/pyright:run-and-fix [basic|standard|strict] [--persist] [--ratchet]
                     [--scope <path>] [--intent silence|improve|bugs-only]
                     [--no-suggestions]
```

Key flags:

- **level** (`basic` / `standard` / `strict`) — one-shot override of `typeCheckingMode` without editing config yourself.
- **`--persist`** — only writes the level back to config if the run reaches zero errors. No lying commits.
- **`--ratchet`** — climbs `basic → standard → strict`, fixing to zero at each rung. Mutually exclusive with an explicit level.
- **`--scope <path>`** — limit to a subpath. Useful for adopting pyright one package at a time.
- **`--intent`** — picks the lean (see below). If omitted, the command prompts after showing triage.
- **`--no-suggestions`** — skip the "suggested improvements" list printed at the end.

## Fix intents

The same error gets fixed three different ways depending on intent:

| Intent      | Lean                                                                                                                      | Output shape                                    |
| ----------- | ------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `silence`   | Rule-specific suppressions (`# pyright: ignore[rule]`) + `cast()` at boundaries. Still flags real bugs.                   | Fastest to zero; small diff; many suppressions. |
| `improve`   | Widen over coerce, annotate over cast, extract a factory over repeated `cast(T, …)`. Pauses on semantically loaded calls. | Slower, larger diff, durable.                   |
| `bugs-only` | Fixes only `bugs.md`-class items; everything else gets a suppression + `# TODO(types): revisit under --intent improve`.   | Zero type churn; grep-able follow-up list.      |

## Types of fixes it makes

The three bundled pattern files carry the recipes. The lists below are a summary, not the full text — see the files themselves for code.

### Pyright-rule recipes (`rules.md`)

General typing patterns, keyed on the rule name pyright prints:

- **`reportOptionalMemberAccess` / `reportOptionalSubscript`** — narrow with `assert x is not None` when the check is checker-only, `raise` when it's a runtime invariant.
- **`reportArgumentType`** — widen call-site types (often `Mapping[str, Any]` over `Dict[str, Any]`) instead of casting.
- **TypedDict ↔ `dict[str, Any]` asymmetry** — widening direction that works with pyright's variance rules.
- **`reportCallIssue` / no overloads match** — reorder / re-shape args; sometimes a stale `@overload` stack is the culprit.
- **`reportTypedDictNotRequiredAccess`** — `.get(key)` with an explicit default, or narrow via membership check.
- **Narrowing across nested scopes** — walrus for single-expression narrowing; pre-bind to a fresh local before comprehensions / closures. Unifies several rule names (`reportOperatorIssue`, `reportOptionalOperand`, `reportArgumentType`) under one fix shape.
- **Stub-runtime type disagreement** — when stubs declare `T1` but the runtime may produce `T1 | T2 | ...`, widen once to `Any` on a named local and let `isinstance` ladders narrow. Distinct from trust-boundary `cast()`.
- **Undeclared keys on TypedDict** — decide between widening the TypedDict or switching to a regular dict at the boundary.
- **Discriminated unions with `Literal` + `TypedDict`** — model each variant as its own TypedDict and use a `Literal` field to let pyright narrow the whole shape from one check.
- **`reportAttributeAccessIssue` on class-level fields** — annotate the class attribute; don't rely on `__init__` assignment to infer it.
- **`def f(x: str = None)` antipattern** — fix to `x: str | None = None`.
- **Dataclass mutable defaults** — `field(default_factory=list)` over `= []`.
- **`Protocol` methods missing `self`** — add it; pyright will stop complaining and the Protocol will actually be callable.
- **`Protocol` vs `ABC` vs plain duck typing** — when to reach for each; Protocol is usually the right default for pluggable interface contracts.
- **`asyncio.gather(..., return_exceptions=True)`** — split the return into `BaseException | T` branches instead of casting.
- **Pydantic v1 → v2 field renames** (`min_items` → `min_length`, `regex` → `pattern`, …).
- **Pydantic `Field()` positional defaults** — "Arguments missing for parameters X, Y, Z" on pydantic/Beanie constructors.
- **`cast(Model, payload)` at pydantic list boundaries** — and the hidden-missing-field trap; when to extract a test factory instead.
- **Schema projection via `model_validate`, not `cast`** — `cast(B, a.model_dump(include=...))` type-checks but returns a dict at runtime; `B.model_validate(...)` builds a real instance. Decision rule by target type (TypedDict / BaseModel / vanilla dataclass).
- **`@staticmethod` over module-level free functions for class-adjacent helpers** — paired with the `cast(Required, None)` refactor signal (reference.md); test-patch-target durability is the tiebreaker.
- **`reportGeneralTypeIssues` / "None is not iterable"**, **`reportOptionalOperand`**, **`reportMissingImports`**.
- **Third-party library intake flow** — ordered fallbacks when a new library pyright can't type: typeshed stubs → `useLibraryCodeForTypes` → `pyright --createstub` → scoped `allowedUntypedLibraries`.
- **`TYPE_CHECKING` for type-only imports** — guarded imports for circular references and heavy type-only dependencies; pair with `from __future__ import annotations` to avoid runtime `NameError`.
- **`bool | None` → `bool` coercion** — when it's safe, and when coercing destroys the "unknown" vs "false" distinction.
- **Stale `@overload` stacks** — prune overloads that no longer match the implementation.
- **Opaque `dict[str, Any]` with repeated key reads** — `--intent improve` only. Scans touched files for `dict[str, Any]` values read through 3+ distinct literal keys and proposes extracting a TypedDict or Pydantic model. Pyright doesn't flag these (they're type-clean) but they block data-flow tracing; extraction gives the contract a name. Asks for approval before writing — the decision tree pauses on name, location, optional-key shape, and migration radius.

### Library-stub workarounds (`libraries.md`)

Cases where pyright is correctly following the stubs but the stubs are wrong or under-specified. Fixes are narrow and targeted at the library in question:

- **bitstring** — `BitArray` iteration yielding unknown types.
- **scipy.stats** — results typed as `_` placeholder.
- **tornado** — `HTTPConnection.stream` missing from stubs; `RequestHandler` mixin attributes.
- **matplotlib** — `plt.hist(bins=…)` rejecting `np.ndarray`.
- **Beanie** (MongoDB ODM) — document field typing and query return shapes.
- **Supabase** — auth + query client response wrappers.
- **litellm** — `completion()` returns `ModelResponse | CustomStreamWrapper`.
- **pic_prompt** — `get_prompt()` return-type lies.
- **Dynaconf** — `Validator(messages={…})` accepts arbitrary keys.
- **PIL** — `ImageCms.profileToProfile` may return `None`.
- **tenacity** — retry-callback state accessors.
- **pymongo** — prefer `has_error_label` over private attributes on `OperationFailure`.
- **pydantic** — BaseModel fields with TypedDict types enforced at runtime; test-factory pattern and `Dict[str, Any]` stopgap.
- **Optional-runtime dependencies** — inline import + rule-specific suppress pattern.

### Real-bug signals (`bugs.md`)

Patterns where pyright has uncovered a genuine runtime bug. These are **flagged for the user, not silenced** — the command will not auto-fix these:

- Attribute reads on fields that never existed (typos, renames the code missed).
- Subclass attribute shadowing an inherited method (silently breaks the method at runtime).
- Repeated side-effectful call in a loop where the result was meant to be cached.
- Dead field referenced through an existing `# type: ignore` / `# pyright: ignore`.
- Dead module / class constants — unreferenced after a refactor.
- Reversed dict-direction lookups (indexing a dict with a value instead of a key).
- Parameter the callee doesn't accept (pyright catches it; the runtime would `TypeError`).

## Phases at a glance

1. **Parse args and verify setup** — locate config, detect package manager if pyright isn't installed, apply level override (pyright has no CLI flag for `typeCheckingMode`, so the command edits the config file and records the original level for restore).
2. **Baseline** — full pyright run saved to `/tmp/pyright_full.txt`, bucketed by rule and by file, triage summary shown, intent confirmed.
3. **Fix** — inline for `<20` errors; parallel agent dispatch with disjoint file partitions for `≥20`. Every dispatched agent is given the verbatim intent definition so partitions don't drift.
4. **Consolidation pass (3.5)** — greps the combined diff for high-repetition suppressions and casts. ≥10 of the same suppression or ≥5 of the same cast target trigger a "this is one upstream fix, not N downstream suppressions" prompt.
5. **Verify** — re-run pyright, classify any residual into library-stub gaps, design decisions, or genuine bugs.
6. **Persist, ratchet, summarize** — persist only on zero-error runs; restore original config level otherwise. Print a summary and (unless `--no-suggestions`) a prioritized list of structural improvements the run deferred.

## Suggested-improvements artifact

At the end of a run, the command offers to save the improvement suggestions to `docs/exec-plans/active/pyright-improvements-<timestamp>.md`. That file is a handoff artifact — **not committed**, per the project convention for exec plans.

## Relationship to `/harness:reasoning-gaps`

Pyright (especially under `--intent improve`) owns the **typing axis** of what reasoning-gaps analyzes: annotations, `Any` escapes, opaque `dict[str, Any]` containers. It does not cover the other two axes reasoning-gaps inspects — **implicit control flow** (decorators, signals, dynamic dispatch, metaclasses) and **structure / documentation** (missing docstrings, long functions, deep nesting).

Running `/pyright:run-and-fix --intent improve` first resolves the typing-axis gaps rigorously so reasoning-gaps' attention lands on the flow and docs axes it uniquely sees. Every run ends with a plain-text pointer at `/harness:reasoning-gaps` as the natural next step. The two commands do not share state or flags — the handoff is a recommendation, not a coupling.
