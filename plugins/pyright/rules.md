# Pyright fix patterns by rule

Rule-specific recipes for the pyright errors you'll see most often during adoption. Each subsection is keyed on the pyright rule name in the error output. Policy, triage process, and dispatch guidance live in `reference.md`.

## `reportOptionalMemberAccess` / `reportOptionalSubscript`

Access on a value that might be `None`.

- **In tests:** `assert x is not None` before the use. Pyright narrows through a bare `assert`; unittest's `self.assertIsNotNone(x)` does NOT narrow, it just fails the test at runtime. Different tools. Use both if you want the nice test failure message AND the narrowing, but the bare assert is what gives pyright what it needs.
- **In production:** prefer an explicit guard that raises a descriptive error, not an `assert`. Example:
  ```python
  if self._delegate is None:
      raise RuntimeError("delegate not set before replay")
  ```
  Reason: `python -O` strips asserts. Asserts are for invariants pyright needs to see; raises are for genuine runtime safety. See `reference.md` § "Assert vs raise for type narrowing".
- **If the declared type is wrong:** fix the declaration (e.g. field was `Foo | None` but is always set in `__init__` so should be `Foo`).

## `reportArgumentType`

Passing the wrong type to a function.

- Narrow with `assert x is not None`.
- Cast with `typing.cast(TargetType, value)` at API boundaries where the runtime value is known to match but the declared type is wider.
- **`click.Choice(['a','b'])`** returns `str` at the type level even though values are constrained. Cast to the `Literal` at the CLI boundary: `cast(Literal['a','b'], value)`.
- **Passing a `TypedDict` / pydantic model where `dict[str, Any]` is expected:** `dict(td)` for TypedDict, `model_dump()` for pydantic, or widen the callee's signature if you own it.

**`cast()` vs `isinstance()` narrowing — decision rule.** Both silence `reportArgumentType` at a wide-type boundary, but they have different runtime semantics. Use `cast(T, x)` when the producer is an internal contract you control — e.g. a `Dict[str, Any]` returned by your own API client that you know matches a specific `TypedDict`. Use `isinstance(x, T)` (or `isinstance(x, str)` + conditional reassign) when the value crosses a real trust boundary: HTTP response field, user input, subprocess output. The `isinstance` adds a runtime check that catches producer regressions a `cast` would silently swallow. Rule of thumb: cast when the shape is your own invariant; isinstance when an external producer might break the invariant.

## TypedDict ↔ `dict[str, Any]` asymmetry

Neither direction is assignable without a cast. That's the surprise — intuition says a `TypedDict` "is a" `dict[str, Any]` structurally, so the `TypedDict → dict[str, Any]` direction should be free. It isn't. Pyright treats them as distinct shapes because TypedDict keys are literal-string-constrained while `dict[str, Any]` keys are any `str`.

Practical fixes:

- **`TypedDict → dict[str, Any]`** (passing a typed response to a generic sink like a formatter, JSON encoder, or a helper that takes `dict[str, Any]`): `cast(dict[str, Any], td)` at the call site, or `dict(td)` if a runtime copy is acceptable. Widening the callee's signature is also an option if you own it — see below.
- **`dict[str, Any] → TypedDict`** (e.g. casting `response.json()` to the declared response schema): `cast(SomeTypedDict, raw)`. Expected direction, but note the cast is *unchecked* — downstream `td.get("key")` will succeed statically and fail at runtime if the server doesn't actually send `key`.

The asymmetry means a `reportArgumentType` at a `dict[str, Any]`-typed sink has the *same fix shape* as one at a `TypedDict`-typed sink: a `cast`. Without this in mind, you'll spend time looking for a structural-subtype path that doesn't exist.

### If you own the consumer, widen to `Mapping[str, Any]`, not `Dict[str, Any]`

The natural instinct when a function keeps collecting casts at its callers is "widen the parameter." The trap: widening `Dict[str, Any]` → `Dict[str, Any]` changes nothing because the original asymmetry is about `Dict`. **`Mapping[str, Any]` is different** — every TypedDict *is* assignable to it, because `Mapping` is the read-only protocol every dict (including TypedDicts) satisfies.

```python
# Before — callers need cast(Dict[str, Any], ...) every time
def generate_url(self, image: Dict[str, Any], path_key: str) -> Optional[str]:
    if image.get(path_key):
        ...

# After — callers pass TypedDicts directly; function body unchanged
def generate_url(self, image: Mapping[str, Any], path_key: str) -> Optional[str]:
    if image.get(path_key):
        ...
```

Works when the function only *reads* the dict (`.get()`, `[]`, iteration, `in`). If the function mutates the dict (`image["new_key"] = ...`, `.pop()`, `.update()`), you need `Dict[str, Any]` or a specific TypedDict instead — TypedDicts don't cleanly support arbitrary-key mutation at the type level.

This is the consumer-side analog of "fix the producer's return type" — and it's often the better leverage when you own the consumer but not the producers. One signature change deletes N casts at callers. See `reference.md` § "Repetition as a signal" — when multiple call sites cast the same value through to the same function, widening the function's parameter to `Mapping[str, Any]` is usually the right move.

## `reportCallIssue` / no overloads match

Often caused by `assertAlmostEqual(x, y)` when `x: float | None`. The protocol requires both operands be non-None numbers. Fix by narrowing `x` first:

```python
assert result.some_field is not None
self.assertAlmostEqual(result.some_field, 0.5)
```

If a whole block reads the same `result`, one `assert result is not None` at the top of the block narrows for everything below it.

## `reportTypedDictNotRequiredAccess`

`td["key"]` when `key: NotRequired` in the TypedDict. Two fixes, and they are **not** interchangeable:

- `if "key" in td: td["key"]` — pyright narrows the subscript inside the `if` body.
- `x = td.get("key"); if x: ... x ...` — bind the `.get()` result to a local and use the local.

What doesn't work: a truthy `if td.get("key"):` followed by a `td["key"]` subscript. Pyright does **not** propagate the truthiness check from the `.get()` call to a re-subscript of the same key, so the second access still triggers `reportTypedDictNotRequiredAccess`. Either use the `in`-based form, or keep the `.get()` form but bind it to a local and never re-subscript.

## Reading *undeclared* keys on a TypedDict

Different bug from `reportTypedDictNotRequiredAccess`. That rule is about a *declared-but-NotRequired* key — fix by narrowing with `if "key" in td` or binding `.get()` to a local. This case is different: the key isn't declared on the TypedDict at all.

```python
class UserResponse(TypedDict, total=False):
    id: str
    email: str

u: UserResponse = ...
u.get("email_confirmed_at")   # server sends this, TypedDict doesn't declare it
```

Narrowing won't help — you can't narrow into a key the type doesn't know about. Three options:

1. **Cast to `dict[str, Any]` at the read site** (pragmatic escape hatch):
   ```python
   extra = cast(dict[str, Any], u).get("email_confirmed_at")
   ```
   Honest: it acknowledges the type system doesn't know about this field. Better than `# pyright: ignore` because a reader can see exactly what was widened and why.

2. **Add the key to the TypedDict.** Correct if the server contract actually includes it — but beware of padding TypedDicts with fields you're not sure belong to the real schema.

3. **Suppress with `# pyright: ignore[...]`.** Worst of the three; the cast is cleaner and self-documenting.

Signal that you're in *this* case, not the NotRequired case: pyright's complaint mentions "unknown member" / "no member" / `reportGeneralTypeIssues`-style wording rather than `reportTypedDictNotRequiredAccess`, and `if "key" in td:` doesn't narrow the access.

## `reportAttributeAccessIssue` on class-level fields

When a subclass or related object reads `Cls.some_field` and pyright says "unknown attribute": declare the field as a `ClassVar` in the base class so pyright can see it.

## Attribute typing in `__init__`

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

## The `def f(x: str = None)` antipattern

Widespread in older Python codebases: parameter default lies about the declared type. Pyright correctly flags "Expression of type None cannot be assigned to parameter of type str."

```python
# Wrong — declaration lies; body has been treating x as optional
def generate_signed_url(self, user_id: str = None, ...) -> str:
    if user_id is None:
        ...

# Right — widen to match the body's actual semantics
def generate_signed_url(self, user_id: Optional[str] = None, ...) -> Optional[str]:
    ...
```

Not a cast, not a suppression — the declaration was wrong. Pyright surfaces these in batches during initial adoption; fix by widening signatures to match the body. Often the return type is also wrong (a `-> str` function that `return None`s on an edge path); fix both in one edit.

## Dataclass mutable defaults

Pyright flags `field: list = []` as a type mismatch (the literal `[]` is `list[Never]`, not the declared type) — and the runtime semantics are also wrong (shared mutable state across instances). Fix with `field(default_factory=...)`:

```python
from dataclasses import dataclass, field

# Wrong — pyright flags, runtime bug too
@dataclass
class Ctx:
    data: dict = {}
    items: list[str] = []

# Right
@dataclass
class Ctx:
    data: dict = field(default_factory=dict)
    items: list[str] = field(default_factory=list)
```

The `__init__` attribute-typing recipe above covers instance assignments; this one is dataclass-specific. Pyright surfaces both during adoption; fix mechanism is different.

## `Protocol` methods missing `self`

Easy-to-miss trap with `typing.Protocol`, especially when duck-typing module-level functions into a Protocol shape (a common pattern for pluggable "provider" modules):

```python
# Wrong — pyright treats these as non-instance methods; callers hitting
# provider.add_event(...) get argument-count errors
class CalendarProvider(Protocol):
    def add_event(calendar_id: str, event: dict) -> str: ...

# Right
class CalendarProvider(Protocol):
    def add_event(self, calendar_id: str, event: dict) -> str: ...
```

Runtime duck-typing from module-level functions (`google_provider.add_event(calendar_id, event)`) still satisfies the Protocol because Python doesn't check `self`; the Protocol's only consumer is pyright. Add `self` and the call sites type-check.

## `asyncio.gather(..., return_exceptions=True)` returns `BaseException | T`

`isinstance(result, Exception)` is the instinctive check but it misses `BaseException` subclasses that do land in the results — `KeyboardInterrupt`, `SystemExit`, `asyncio.CancelledError`. Pyright flags the narrower check:

```python
results = await asyncio.gather(*tasks, return_exceptions=True)

# Wrong — misses BaseException-only types
for result in results:
    if isinstance(result, Exception):
        ...

# Right
for result in results:
    if isinstance(result, BaseException):
        ...
```

## Pydantic v1 → v2 field-argument renames

During v1→v2 migrations pyright surfaces the old argument names as type errors on `Field(...)`:

```python
# Pydantic v1
Field(..., min_items=1, max_items=10)
Field(..., regex=r"^\d+$")

# Pydantic v2
Field(..., min_length=1, max_length=10)
Field(..., pattern=r"^\d+$")
```

Easy to fix in bulk once you spot the pattern. Useful signal: if pyright flags several `Field(...)` calls at once after a pydantic upgrade, check the changelog rather than fixing them individually.

## Pydantic `Field()` positional defaults

`Field(None, description="...")` — pydantic's legacy positional-default form — is not recognized by pyright as providing a default. Every construction of the owning model triggers `reportCallIssue`: "Arguments missing for parameters X, Y, Z". Migrate to keyword form:

```python
# Wrong — pyright sees no default, flags every constructor call
class Settings(BaseModel):
    default_calendar: Optional[str] = Field(None, description="...")

# Right — pyright recognizes the default
class Settings(BaseModel):
    default_calendar: Optional[str] = Field(default=None, description="...")
```

**The pattern generalizes beyond `None`.** Every literal positional default has the same issue: `Field(True, ...)`, `Field(False, ...)`, `Field(0, ...)`, `Field("UTC", ...)`, `Field(SomeEnum.MEMBER, ...)`. A migration that only catches `None` will leave the rest flagged.

**Multi-line form evades single-line regex.** Field definitions that span lines — common for long descriptions — look like:

```python
scans_limit_monthly: Optional[int] = Field(
    None,
    description="Monthly scan limit (null = no limit)",
)
```

A `sed 's/Field(None, /Field(default=None, /g'` catches only the single-line form. Use `perl -0777 -i -pe` with a pattern covering both shapes — but **dry-run first** on a small subset. The regex has tail cases (nested parens in defaults, string literals containing `,`, unusual whitespace) that will occasionally match incorrectly; a blind repo-wide run is a recipe for a bad merge.

```bash
# 1. Dry-run: list files that contain at least one match, so you can diff a handful first.
find . -name "*.py" -exec perl -0777 -ne '
  print "$ARGV\n" if /Field\(\s*\n?\s*(None|True|False|-?\d+(?:\.\d+)?|"[^"]*"|'"'"'[^'"'"']*'"'"'),/;
' {} + | sort -u

# 2. Apply on a 5–10 file subset, review with `git diff`, then expand scope.
# 3. Repo-wide pass (only after the subset diff looks right):
find . -name "*.py" -exec perl -0777 -i -pe '
  # Multi-line: Field(\n    LITERAL,
  s/Field\(\s*\n(\s+)(None|True|False|-?\d+(?:\.\d+)?|"[^"]*"|'"'"'[^'"'"']*'"'"'),/Field(\n${1}default=$2,/g;
  # Single-line: Field(LITERAL,
  s/Field\((None|True|False|-?\d+(?:\.\d+)?|"[^"]*"|'"'"'[^'"'"']*'"'"'),\s/Field(default=$1, /g;
' {} +
```

Enum members (`Field(UserTier.FREE, ...)`) and other identifier-valued defaults won't match a literal-only regex — expect a small manual tail after the bulk pass.

### Diagnostic: "Arguments missing for parameters X, Y, Z" on a pydantic/Beanie constructor

When pyright reports `Arguments missing for parameters "default_calendar", "sort_by", "sort_order"` on a `SomeModel(...)` call *and* those fields are declared `Optional[T] = Field(<something>, ...)` in the schema, the schema is the bug — not the caller. The error message reads as "you forgot to pass these" but the root cause is positional-default form pyright can't see. Grep the schema for `Field(<positional>,` — almost always a hit.

This matters because the fix is upstream: one `Field(None, ...)` → `Field(default=None, ...)` in the schema deletes the error at every caller. Point-fixing each caller with `# pyright: ignore[reportCallIssue]` spreads the workaround instead. See `reference.md` § "Repetition as a signal" for the broader pattern.

## `cast(Model, payload)` at pydantic list boundaries

When passing `list[dict]` where `list[SomeModel]` is required, pydantic coerces at runtime via `model_validate`, but pyright needs the hint:

```python
# Pydantic coerces the dict → Model at runtime, but pyright needs the cast
self.events_result = EventsResult(events=cast(List[ProcessedEvent], [saved_event]))
```

Cleaner than building the model eagerly just to satisfy pyright — the runtime validation is the same either way.

### The hidden-missing-field trap

**`cast(T, dict_literal)` is a code smell, not a solution.** The cast silences pyright but also hides missing *required* fields in `T`. The worst-case shape is `cast(List[Order], [{}])` — type-checks cleanly, structurally invalid at runtime, zero compile-time help. Prefer the real constructor:

```python
# Wrong — pyright happy, runtime will explode if required fields missing
orders = cast(List[Order], [{"id": "ord-1"}])

# Right — pyright validates the shape against Order's field set
orders = [Order(id="ord-1", customer="alice", total=Decimal("12.50"))]
```

`cast(T, x)` is appropriate only when `x`'s *runtime* value genuinely matches `T` but pyright can't see it — e.g. crossing a `model_dump()` → re-cast-back boundary, or a dict from an API client you control and have reason to trust. `cast(T, {literal})` is almost never that case; the literal is *your* construction, and a real constructor call would let pyright check it.

### When to extract a test factory

If the same `cast(T, {...})` or TypedDict-literal pattern repeats 5+ times across tests, extract a factory:

```python
# tests/factories.py
def make_user_profile(**overrides) -> UserProfile:
    base: UserProfile = {
        "id": "test-user",
        "email": "test@example.com",
        "tier": "free",
        # ... all required fields, realistic defaults
    }
    return {**base, **overrides}

def make_order(**overrides) -> Order:
    return Order(id="ord-1", customer="alice", total=Decimal("12.50"))
    # Note: real constructor, not cast. Missing required fields fail fast.
```

Replace scattered casts with factory calls. Two benefits that the cast version doesn't give you:
1. **Shape validation surfaces once, in the factory.** A future schema change flags one site, not twenty.
2. **Test intent reads better.** `make_user_profile(tier="premium")` tells the reader "I care about tier here, everything else is a default." `cast(UserProfile, {"tier": "premium"})` hides that intent behind a type assertion — and also hides whether the other required fields are set.

Count before deciding: 1-2 sites, inline `cast` is fine. 5+, factory. See `reference.md` § "Repetition as a signal" for the broader pattern.

## Opaque `dict[str, Any]` with repeated key reads

**`--intent improve` only.** Pyright does not emit an error here — the code is fully type-clean — so this recipe is triggered by file scan, not by a rule name.

A `dict[str, Any]`-typed value (parameter, local, return, or attribute) read through **3+ distinct literal keys** inside one function body or file is a signal that an un-named data contract is being smuggled through a generic container. Extraction gives the contract a name pyright and future readers (human or agent) can trace.

### Decision tree

1. **Is the dict mutated at many sites?** (`d["k"] = ...`, `.pop()`, `.update()`, `del d["k"]`.) If yes, **keep `dict[str, Any]`.** TypedDicts don't cleanly support arbitrary-key mutation at the type level — the same caveat flagged in the `Mapping[str, Any]` section above. Do not extract.
2. **Is the source a stable server/data contract?** (JSON payload from a documented endpoint, message-broker event, config file schema.) Extract a **`TypedDict`**. Keys that may be absent become `NotRequired`.
3. **Is pydantic already imported in the file, and would validation/defaults/coercion help?** Extract a **`BaseModel` subclass** and replace `d["k"]` reads with attribute access after `Model.model_validate(d)` at the boundary.
4. **Otherwise** (mixed-shape dict, keys determined at runtime, or the dict flows through too many generic helpers to re-plumb) — keep `dict[str, Any]` and move on.

### Pause and ask before writing

This is semantically loaded in the same class as `bool | None → bool` — the extraction surfaces four user-visible decisions the recipe cannot make alone:

1. **Name** of the new type (`UserProfile` vs. `UserRecord` vs. `UserDict`).
2. **Location** (`models.py`, a new `schemas/` module, colocated with the caller).
3. **Optional keys**: `NotRequired[T]` vs. `T | None` — they are not equivalent. `NotRequired` means "may be absent"; `T | None` means "present, but may be null."
4. **Migration radius**: does this extraction force call-site updates elsewhere, and is that in scope for this run?

Present these to the user as a short proposal and wait for approval. If approval isn't forthcoming, do nothing — the code was already type-clean, leaving it alone is a valid outcome.

### Before / after

```python
# Before — three reads, shape implicit
def summarize(payload: dict[str, Any]) -> str:
    user = payload["user_id"]
    tier = payload.get("tier", "free")
    active = payload["is_active"]
    return f"{user} ({tier}, {'on' if active else 'off'})"
```

```python
# After — TypedDict makes the shape part of the function's signature
class SummaryPayload(TypedDict):
    user_id: str
    is_active: bool
    tier: NotRequired[str]

def summarize(payload: SummaryPayload) -> str:
    user = payload["user_id"]
    tier = payload.get("tier", "free")
    active = payload["is_active"]
    return f"{user} ({tier}, {'on' if active else 'off'})"
```

Now a caller passing a dict missing `user_id` fails at the call site, not inside `summarize`. The key reads don't change — only the contract got a name.

### Why this isn't "test factories, part 2"

The test-factory recipe above and this one share the "N-repetition triggers extraction" shape, but the trigger condition is different:

- **Test factory**: repeated `cast(T, {...})` or TypedDict-literal *construction* (already pyright-errored or suppressed).
- **Opaque dict**: repeated *reads* off a generically-typed source (pyright-clean, no error).

Don't merge them — their decision trees and targets differ.

## `reportGeneralTypeIssues` / `"None" is not iterable`

`for x in maybe_none:` when `maybe_none: list[...] | None`. Add `assert maybe_none is not None` before the loop, or use `for x in maybe_none or []:` if empty-iteration is the desired fallback.

## `reportOptionalOperand`

`result + 1` when `result: int | None`. Same fix: narrow with an assert first.

## `reportMissingImports`

Real issue. Usually `sys.path`-manipulated imports, or genuinely-missing modules. If the `sys.path` manipulation is intentional, `# pyright: ignore[reportMissingImports]` is acceptable.

## Assigning `bool | None` to a `bool` field

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

## When `Optional[T] → T` coercion *is* fine

The inverse case of the `bool | None` warning: when `None` genuinely means "unset, use sensible default," a simple `x or default` at the assignment site is clean and preserves information. Example: a `Config.timezone: Optional[str]` flowing into a TypedDict field `timezone: str`.

```python
ctx: HerdsContext = {
    "timezone": config.timezone or "UTC",
    ...
}
```

This is safe because the fallback (`"UTC"`) is semantically meaningful — it's the intended default when the user hasn't configured a timezone — not a coerced placeholder that destroys a meaningful `None`. Same question as the `bool | None` section, different answer: *does `None` carry different meaning than the falsy value?* Here, no — "unset" and "UTC" are the intended same-state. Coerce.

The distinction between the two sections collapses into one rule: coerce `Optional[T] → T` when the default is the intended treatment of `None`; widen the destination to `Optional[T]` when `None` carries a distinct meaning that downstream consumers rely on.

## Stale `@overload` stacks become noise under strict

Instinct in strict mode is to add types, not remove them. But an `@overload` stack written earlier — under `basic` or `standard`, or copied from a typing cookbook — can turn actively harmful once strict flags the callers.

```python
@overload
def sanitize(data: Dict[str, Any]) -> Dict[str, Any]: ...
@overload
def sanitize(data: List[Any]) -> List[Any]: ...
@overload
def sanitize(data: None) -> None: ...
def sanitize(data: Any) -> Any:
    # recursive body
    ...
```

Under strict, every caller passing an `Any`-typed value (e.g. from `response.json()` or another untyped source) gets `reportCallIssue` / "no overload matches" because `Any` doesn't match any single overload cleanly. The overloads promise specificity the body doesn't actually deliver.

Fix: delete the overload stack and keep `def sanitize(data: Any) -> Any`. The overloads were load-bearing only when callers passed a *statically-typed* `Dict`, `List`, or `None` — which is rarer than it looks once you audit the call sites.

Signals the overload stack is stale:

- Overloads differ only in what they pass through (identity-preserving shape; each branch returns the type it took in).
- The runtime body has an `isinstance`-dispatch over the same shapes — the overloads duplicate the runtime check at the type level.
- Strict flags most callers as "no overload matches" even though basic/standard were clean.

When in doubt, remove the overloads and re-run pyright. If new errors appear at call sites that specifically relied on overload-specificity, restore them targetedly. More often, they don't.
