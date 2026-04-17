# Pyright Learnings

Notes from adopting pyright (`typeCheckingMode = "basic"`) on a large existing Python codebase. Captures what worked, what bit us, and the patterns to reuse next time.

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
5. **Fix production code first, then tests.** Production fixes can cascade: when a function's return type becomes precise, tests destructuring that return often start type-checking without further changes. Tests-first would mean redoing work.
6. **Parallelize across contributors or agents.** The critical constraint is *disjointness*: two workers editing the same file will conflict. One file per worker is the simplest partition; for big files, one rule per worker on that file also works.

## Fix patterns by rule

### `reportOptionalMemberAccess` / `reportOptionalSubscript`

Access on a value that might be `None`.

- **In tests:** `assert x is not None` before the use. Pyright narrows through a bare `assert`; unittest's `self.assertIsNotNone(x)` does NOT narrow, it just fails the test at runtime. Different tools. Use both if you want the nice test failure message AND the narrowing, but the bare assert is what gives pyright what it needs.
- **In production:** prefer an explicit guard that raises a descriptive error, not an `assert`. Example:
  ```python
  if self._delegate is None:
      raise RuntimeError("delegate not set before replay")
  ```
  Reason: `python -O` strips asserts. Asserts are for invariants pyright needs to see; raises are for genuine runtime safety. See the "assert vs raise" section below.
- **If the declared type is wrong:** fix the declaration (e.g. field was `Foo | None` but is always set in `__init__` so should be `Foo`).

### `reportArgumentType`

Passing the wrong type to a function.

- Narrow with `assert x is not None`.
- Cast with `typing.cast(TargetType, value)` at API boundaries where the runtime value is known to match but the declared type is wider.
- **`click.Choice(['a','b'])`** returns `str` at the type level even though values are constrained. Cast to the `Literal` at the CLI boundary: `cast(Literal['a','b'], value)`.
- **Passing a `TypedDict` / pydantic model where `dict[str, Any]` is expected:** `dict(td)` for TypedDict, `model_dump()` for pydantic, or widen the callee's signature if you own it.

**`cast()` vs `isinstance()` narrowing — decision rule.** Both silence `reportArgumentType` at a wide-type boundary, but they have different runtime semantics. Use `cast(T, x)` when the producer is an internal contract you control — e.g. a `Dict[str, Any]` returned by your own API client that you know matches a specific `TypedDict`. Use `isinstance(x, T)` (or `isinstance(x, str)` + conditional reassign) when the value crosses a real trust boundary: HTTP response field, user input, subprocess output. The `isinstance` adds a runtime check that catches producer regressions a `cast` would silently swallow. Rule of thumb: cast when the shape is your own invariant; isinstance when an external producer might break the invariant.

### `reportCallIssue` / no overloads match

Often caused by `assertAlmostEqual(x, y)` when `x: float | None`. The protocol requires both operands be non-None numbers. Fix by narrowing `x` first:

```python
assert result.some_field is not None
self.assertAlmostEqual(result.some_field, 0.5)
```

If a whole block reads the same `result`, one `assert result is not None` at the top of the block narrows for everything below it.

### `reportTypedDictNotRequiredAccess`

`td["key"]` when `key: NotRequired` in the TypedDict. Two fixes, and they are **not** interchangeable:

- `if "key" in td: td["key"]` — pyright narrows the subscript inside the `if` body.
- `x = td.get("key"); if x: ... x ...` — bind the `.get()` result to a local and use the local.

What doesn't work: a truthy `if td.get("key"):` followed by a `td["key"]` subscript. Pyright does **not** propagate the truthiness check from the `.get()` call to a re-subscript of the same key, so the second access still triggers `reportTypedDictNotRequiredAccess`. Either use the `in`-based form, or keep the `.get()` form but bind it to a local and never re-subscript.

### `reportAttributeAccessIssue` on class-level fields

When a subclass or related object reads `Cls.some_field` and pyright says "unknown attribute": declare the field as a `ClassVar` in the base class so pyright can see it.

### Attribute typing in `__init__`

Two `__init__` patterns that confuse pyright and produce misleading follow-on errors:

**"Starts `None`, filled in later."** Raw `self.auth_code = None` in `__init__` makes pyright infer the attribute's type as literal `None` — any later `self.auth_code = "abc"` becomes `reportAttributeAccessIssue` ("Expression of type `str` cannot be assigned to attribute of type `None`"). Fix: annotate the attribute at the assignment site.

```python
# Wrong — pyright infers self.auth_code as None-only
self.auth_code = None
self.error_message = None

# Right — annotation unlocks later assignment
self.auth_code: Optional[str] = None
self.error_message: Optional[str] = None
```

The annotation lives inside `__init__` on the assignment line; no separate class-body declaration is required.

**Conditional init with different subtypes per branch.** When both branches of an `if/else` assign `self.x` with different types, pyright infers the *union* of the two branch types, not the type you intended. Example: one branch sets `self.client_id = config.google_client_id` (typed `str`), the other sets `self.client_id = os.getenv("HERDS_GOOGLE_CLIENT_ID")` (typed `Optional[str]`). Downstream code that expects a consistent `Optional[str]` sees a messy union. Fix by forward-declaring the attribute's type before the `if/else`:

```python
self.client_id: Optional[str]
if config:
    self.client_id = config.google_client_id        # narrows str → Optional[str]
else:
    self.client_id = os.getenv("HERDS_GOOGLE_CLIENT_ID")
```

The forward declaration gives pyright a single target type; both branch assignments widen into it.

### `reportGeneralTypeIssues` / `"None" is not iterable`

`for x in maybe_none:` when `maybe_none: list[...] | None`. Add `assert maybe_none is not None` before the loop, or use `for x in maybe_none or []:` if empty-iteration is the desired fallback.

### `reportOptionalOperand`

`result + 1` when `result: int | None`. Same fix: narrow with an assert first.

### `reportMissingImports`

Real issue. Usually `sys.path`-manipulated imports, or genuinely-missing modules. If the `sys.path` manipulation is intentional, `# pyright: ignore[reportMissingImports]` is acceptable.

### Assigning `bool | None` to a `bool` field

The tempting shortcut is `dst.field = bool(src.field)` (or `src.field or False`) to silence the type error. This destroys the tristate: `None` (unknown) collapses into `False` (invalid), which matters if any consumer distinguishes the two — JSON reports serialize `false` instead of `null`, aggregates over "unknown" get lumped into "failed", and `x is False` comparisons lose information.

The correct fix is to widen the destination field to `bool | None` and drop the coercion. Pair with any `from_dict`-style loader: `d.get("field")` not `d.get("field", False)`, so a missing key round-trips as `None` rather than silently upgrading to `False`. Truthy consumers (`if r.field:`, `sum(1 for r in runs if r.field)`) behave identically under `None` and `False` — only strict-equality or JSON serialization surfaces the distinction.

```python
# Wrong — silences pyright, destroys information
record.flag = bool(source.flag)

# Right — widen destination to match source
@dataclass
class Record:
    flag: bool | None = None   # not bool = False
```

Applies to any `Optional[T]` → `T` assignment where the `None` case carries meaning (not just "absent"). Before coercing, ask: does `None` mean something different from the falsy value of `T`? If yes, widen.

### When `Optional[T] → T` coercion *is* fine

The inverse case of the `bool | None` warning: when `None` genuinely means "unset, use sensible default," a simple `x or default` at the assignment site is clean and preserves information. Example: a `Config.timezone: Optional[str]` flowing into a TypedDict field `timezone: str`.

```python
ctx: HerdsContext = {
    "timezone": config.timezone or "UTC",
    ...
}
```

This is safe because the fallback (`"UTC"`) is semantically meaningful — it's the intended default when the user hasn't configured a timezone — not a coerced placeholder that destroys a meaningful `None`. Same question as the `bool | None` section, different answer: *does `None` carry different meaning than the falsy value?* Here, no — "unset" and "UTC" are the intended same-state. Coerce.

The distinction between the two sections collapses into one rule: coerce `Optional[T] → T` when the default is the intended treatment of `None`; widen the destination to `Optional[T]` when `None` carries a distinct meaning that downstream consumers rely on.

## Library typing gaps

These are pyright-facing bugs in third-party stubs or packages, not your code. The right move is a targeted `# pyright: ignore[specificRule]` with a short comment if the rule isn't self-explanatory.

### `bitstring.BitArray` iteration

The library's own type stubs declare `BitArray.__iter__ -> Iterable[bool]` instead of `Iterator[bool]`, so pyright sees `for bit in ba:` as iterating a non-iterable. At runtime it works fine.

```python
for bit in bits:  # pyright: ignore[reportGeneralTypeIssues]
    ...
```

Alternative: `for i in range(len(ba)): bit = ba[i]` or `iter(ba)`.

### `scipy.stats` results typed as `_`

Functions like `ks_2samp`, `ttest_ind` return an under-annotated `_` placeholder instead of the real `KstestResult` / `TtestResult`. Accessing `.statistic` / `.pvalue` trips pyright even though they exist at runtime (namedtuple fields).

Options (preferred first):
1. Cast to `Any` then access: `res = cast(Any, ks_2samp(...))`.
2. Cast to the specific result class if you can import it: `cast(scipy.stats._stats_py.TtestResult, res)`.
3. `# pyright: ignore[reportAttributeAccessIssue]` if both feel worse than just suppressing.

When using option 1, remember to import `cast` and `Any` from `typing`. It's an easy miss because the cast "feels like syntax" — and the resulting errors cascade. An undefined `cast(Any, ...)` produces a downstream `Cannot access attribute "statistic" for class "_"` on the result, which reads like a separate problem until you notice the root `reportUndefinedVariable` at the call site. Fix root errors first, recheck, then chase what remains.

### `tornado.httputil.HTTPConnection.stream` missing from stubs

Tornado's stubs omit `connection.stream`, which exists at runtime. Pattern:

```python
stream = self.request.connection.stream  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess]
```

If the access is already defended by `try/except AttributeError`, the suppression doesn't hide a bug.

### `matplotlib` `plt.hist(bins=...)` expects `int | Sequence[float] | str | None`, rejects `np.ndarray`

Even though `NDArray[float64]` is a `Sequence[float]` at runtime, matplotlib's stubs don't accept it. Cast:

```python
plt.hist(data, bins=cast(Sequence[float], edges))
```

Or pre-convert: `bins=edges.tolist()`.

### Tornado `RequestHandler` mixin attributes

If a class is declared as a bare `class FooMixin:` but uses `self.request`, `self.set_status`, `self.write` assuming it will be mixed into `RequestHandler`, pyright can't see those attributes. Fix by inheriting directly from `tornado.web.RequestHandler` (making it a concrete base, not just a mixin), or protocol-type the `self` via an intersection (overkill for most cases).

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

## Bug classes pyright uncovers

Documenting these because they're the return on investment. Each is a bug class that sits dormant until pyright forces you to look at it.

### Attribute read that never existed

Code reads `obj.some_field` in multiple places, but `some_field` was never declared on the class (perhaps removed during a refactor, or renamed, or only ever planned). Every read would raise `AttributeError` at runtime. The bug persists because the code path isn't exercised by tests.

Lesson: when pyright flags an attribute as missing, check whether the code reaches that attribute via a pathway tests cover. Often it doesn't, which is why the bug survived.

### Subclass attribute shadows inherited method

Inside a `unittest.TestCase` subclass, assigning `self.run = some_value` clobbers the inherited `TestCase.run()` method. Tests still pass because they don't invoke the shadowed method after assignment, but the shadowing is a latent landmine. Pyright flags it as a type mismatch (method vs. data value).

More generally: assigning a plain attribute with the same name as an inherited method is almost always a bug. Rename the attribute.

### Repeated side-effectful call in a loop

`while data[next_point()] is not None: use(next_point())` calls `next_point()` twice per iteration because the result is read in the condition AND consumed in the body. Pyright's "subscript of None" complaint on the second call often makes the pattern visible. Bind the result once:

```python
while (p := next_point()) is not None:
    use(p)
```

### Dead field referenced through a `# type: ignore` or `# pyright: ignore`

A `# pyright: ignore[reportAttributeAccessIssue]` on a line that reads a nonexistent field is a signal, not a fix. Investigate whether the field should exist on the class, or whether the reference is dead code to be removed. Suppressions can mask the "attribute never existed" bug class above.

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
3. Give each agent: (a) the specific file list, (b) a pointer to pre-split per-file error lists on disk, (c) the fix-pattern recipes, (d) project conventions (line length, naming, formatting), (e) validation steps (`pyright <files>`, the project's linter, the test runner).
4. Dispatch agents in parallel rather than sequentially so they start together.
5. Wait for all to complete before the next phase. Check overall pyright count in between phases, not just trust the reports.

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

Corollary: when you see `# pyright: ignore` suppressing "attribute does not exist" on a field that should exist, investigate whether the field belongs on the class or whether the reference is dead code. The suppression is a signal, not a fix.
