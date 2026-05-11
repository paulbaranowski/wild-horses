# Pyright fix patterns by rule

Rule-specific recipes for the pyright errors you'll see most often during adoption. Each subsection is keyed on the pyright rule name in the error output. Policy, triage process, and dispatch guidance live in `reference.md`.

## `reportOptionalMemberAccess` / `reportOptionalSubscript` _(usually type-only; raise variant is behavior-changing)_

Access on a value that might be `None`. The reflexive fix is `if x is None: raise ...`, but that's a behavior change — it adds a new exception path that didn't exist before. Pick the bucket first (see `reference.md` § "Type-only by default"), then the recipe:

- **Type-only fix (default).** `cast(T, value)` at the call boundary, when validation lives elsewhere (an upstream constructor, a request validator, the producer's own contract). The cast documents the call site's expectation without altering runtime behavior. If the declared type is wrong — e.g. field is annotated `Foo | None` but is always set in `__init__` — fix the declaration instead. That's the most leveraged type-only fix.

- **Narrowing-only fix.** `assert x is not None` _after_ a guard or initialization that proves None is impossible. Pyright narrows through a bare assert; behavior is unchanged. Two caveats: `python -O` strips asserts (so don't rely on them for genuine runtime safety), and unittest's `self.assertIsNotNone(x)` does NOT narrow — it fails the test at runtime but pyright still sees the wide type. Use the bare `assert` for narrowing; optionally pair it with `assertIsNotNone` for the nicer failure message in tests.

- **Behavior-changing fix.** `if x is None: raise ValueError(...)` — _only when this function is the validation point_. Adds a new exception path. Before adding, confirm two things: (a) the surrounding orchestrator doesn't assume this function can accept None and report failure through a different channel (a database `STATE_FAILED` row, a structured error response, an outer try/except that classifies the error); and (b) the raise sits in the right place — a raise added before the existing call site bypasses any create-before-validate ordering downstream code depends on. See `reference.md` § "Assert vs raise for type narrowing" for the test-vs-production angle.

The primary axis is **"is this function the validation point?"** — not "is this production or test code?" If validation belongs elsewhere, default to the type-only fix even in production code.

## Repeated `self.<attr>` narrowing across awaits _(behavior-changing — adds or relocates raises; lift to `__init__` when the attribute is immutable post-construction)_

Symptom: a method body re-binds `local = self.some_attr` then `if local is None: raise` before passing `local` to a callee that requires non-None. The comment usually says _"narrowing does not survive intervening awaits."_ Triggers `reportOptionalMemberAccess` on the attribute access or `reportArgumentType` at the callee — same root cause either way.

Pyright drops narrowing on `self.<attr>` across `await` because the awaited code could mutate `self`. The same loss happens (less commonly) across calls pyright thinks could mutate `self`, and across cross-module calls it can't see through.

The re-bind is the right tactical fix when used **once**. When the same dance appears in 2+ methods of the same class, the invariant is being enforced in the wrong place — moving it to `__init__` (or to one consolidated point) deletes more lines from method bodies than it adds at the new validation site. Two fixes:

- **Validate-at-construction.** If the attribute is set before `__init__` and never mutated afterward, extend `__init__`'s validation and store as a typed non-Optional attribute:

  ```python
  def __init__(self, source: SomeInput):
      if source.foo is None or source.bar is None:
          raise ValueError(...)
      self.foo: str = source.foo
      self.bar: int = source.bar
  ```

  Pyright reads the declared `str` / `int` directly — no narrowing, no awaits to worry about.

- **Consolidate to one validation point.** If the attribute _is_ mutated during the method (e.g., a download side-effect rewrites it), lift the narrowing to a single point after the last mutation and use the local throughout. Don't repeat the dance at each error site.

When refactoring, watch for `from e` clauses that may have lived on an unreachable `ValueError` — they need to move to the actual user-facing `raise` or the exception chain is lost.

For orchestrator-level detection across a partition, see `reference.md` § "Consolidation pass (orchestrator-level)".

## `reportArgumentType` _(usually type-only via `cast` or signature widening; `isinstance` variant is behavior-changing — see decision rule below)_

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
- **`dict[str, Any] → TypedDict`** (e.g. casting `response.json()` to the declared response schema): `cast(SomeTypedDict, raw)`. Expected direction, but note the cast is _unchecked_ — downstream `td.get("key")` will succeed statically and fail at runtime if the server doesn't actually send `key`.

The asymmetry means a `reportArgumentType` at a `dict[str, Any]`-typed sink has the _same fix shape_ as one at a `TypedDict`-typed sink: a `cast`. Without this in mind, you'll spend time looking for a structural-subtype path that doesn't exist.

### If you own the consumer, widen to `Mapping[str, Any]`, not `Dict[str, Any]`

The natural instinct when a function keeps collecting casts at its callers is "widen the parameter." The trap: widening `Dict[str, Any]` → `Dict[str, Any]` changes nothing because the original asymmetry is about `Dict`. **`Mapping[str, Any]` is different** — every TypedDict _is_ assignable to it, because `Mapping` is the read-only protocol every dict (including TypedDicts) satisfies.

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

Works when the function only _reads_ the dict (`.get()`, `[]`, iteration, `in`). If the function mutates the dict (`image["new_key"] = ...`, `.pop()`, `.update()`), you need `Dict[str, Any]` or a specific TypedDict instead — TypedDicts don't cleanly support arbitrary-key mutation at the type level.

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

## Narrowing across nested scopes — walrus and rebind

Pyright's narrowing (`isinstance`, `is None`, truthy check) is anchored to a **name** in a **lexical scope**. Two patterns break that: repeating the expression (each call is a fresh un-narrowed value) and capturing a narrowed name inside a comprehension / lambda / nested `def` (narrowing may not carry across the closure boundary). Both have the same fix: bind once, use the local.

### Walrus for single-expression narrowing

Same `.get()` or attribute access needs to be checked-then-used inline:

```python
# Wrong — two independent .get() calls; second is still Optional[V]
[n for n in items if n.get("created_at") and n.get("created_at") > cutoff]

# Right — one binding; pyright narrows `created` to non-None after the truthy check
[n for n in items if (created := n.get("created_at")) and created > cutoff]
```

The perf win (one lookup, not two) is a bonus. The primary reason is that `created` is a single name with a single narrowed type; `n.get("created_at")` called twice is two independent un-narrowed values.

### Rebind to a local before a nested scope

Outer `if x:` narrows `x` in that scope, but the narrowing may not carry into a nested comprehension / lambda / `def` — especially across closure boundaries. Pre-binding a fresh local guarantees the inner scope sees the narrowed type:

```python
# Risky — narrowing of params.since may not follow into the comprehension
if params.since:
    filtered = [x for x in rows if x.created > params.since]

# Right — fresh local's inferred type is T (not Optional[T]); follows into inner scope
if params.since:
    since_value = params.since
    filtered = [x for x in rows if x.created > since_value]
```

### Diagnostic

Error wording varies with the blocked operation (`reportOperatorIssue`, `reportOptionalOperand`, `reportArgumentType`). Signal that you're in _this_ pattern rather than a missing guard: the outer `if x:` guard is already there, but the error fires inside a nested scope or on a re-access of the same `.get()` / attribute expression.

The `reportTypedDictNotRequiredAccess` recipe above is one instance of this rule — "bind `.get()` to a local, don't re-subscript" is the walrus/rebind idea in TypedDict flavor.

## Stub-runtime type disagreement: widen to `Any`, don't cast

When a library's stubs declare `T1` but the SDK may hand back `T1 | T2 | ...` at runtime, the instinct is to `cast` to whichever type you think came back. Don't — the cast is an unchecked lie in one direction and misses the other. Widen once to `Any` on a named local and let an `isinstance` ladder do the narrowing:

```python
# Stub says response.user.created_at is datetime; SDK sometimes returns an ISO string.
raw_created_at: Any = response.user.created_at
if isinstance(raw_created_at, str):
    created_at = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
elif isinstance(raw_created_at, datetime):
    created_at = raw_created_at
else:
    created_at = None
```

Without the `Any` widening, pyright rejects the `str` branch as `reportUnnecessaryIsinstance` (on a declared-`datetime` value, `isinstance(x, str)` is "provably false" to pyright — even when it isn't at runtime).

**Distinct from the trust-boundary `cast()` pattern** (see `reportArgumentType` above): with a trust-boundary cast you know _which_ type the runtime produces and commit to it. Here you don't — the whole point is that runtime may produce either — so you widen and branch.

**General rule:** when stubs declare `T1` but the runtime may produce `T1 | T2 | ...`, widen to `Any` on a named local. Don't cast to one side.

Concrete examples live in `libraries.md` (e.g. Supabase `User.created_at`). This pattern generalizes to any library whose JSON-conversion layer undercuts the declared stub type.

## Reading _undeclared_ keys on a TypedDict

Different bug from `reportTypedDictNotRequiredAccess`. That rule is about a _declared-but-NotRequired_ key — fix by narrowing with `if "key" in td` or binding `.get()` to a local. This case is different: the key isn't declared on the TypedDict at all.

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

Signal that you're in _this_ case, not the NotRequired case: pyright's complaint mentions "unknown member" / "no member" / `reportGeneralTypeIssues`-style wording rather than `reportTypedDictNotRequiredAccess`, and `if "key" in td:` doesn't narrow the access.

## Discriminated unions with `Literal` + `TypedDict`

When a dict's shape depends on the value of a single field (`status: "success"` → has `data`; `status: "error"` → has `error_code`), modeling each variant as its own `TypedDict` with a `Literal` discriminator lets pyright narrow the whole shape from one check:

```python
from typing import Any, Literal, TypedDict

class SuccessResponse(TypedDict):
    status: Literal["success"]
    data: dict[str, Any]

class ErrorResponse(TypedDict):
    status: Literal["error"]
    error_code: int

Response = SuccessResponse | ErrorResponse

def handle(r: Response) -> None:
    if r["status"] == "success":
        process(r["data"])           # narrowed to SuccessResponse
    else:
        log_error(r["error_code"])   # narrowed to ErrorResponse
```

Without the `Literal`, pyright can't distinguish the variants; every branch needs `if "data" in r:` / `if "error_code" in r:` guards _and_ still fails `reportTypedDictNotRequiredAccess` if the fields are modeled as `NotRequired`. With the discriminator, one field check narrows the entire shape.

**What discriminates:**

- Literal strings (most common).
- Literal ints (`version: Literal[1] | Literal[2]`) and literal bools.
- `str`-backed enum members (`status: Literal[Status.SUCCESS]`).

**What _doesn't_ discriminate:**

- Plain `str` (pyright can't match specific values at the type level).
- `None` vs. present — use `NotRequired[T]` for "maybe absent" rather than `T | None` in a union, and see § "`reportTypedDictNotRequiredAccess`".

**When the ceremony pays off.** 2+ call sites consume the response and need the distinction; the shape comes from an external producer (so you can't collapse to a class hierarchy); readers benefit from the variants being named. For a 1-site consumer reading 2-3 keys once, an inline conditional is simpler and should stay — don't introduce two TypedDicts for a pattern that touches one function.

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

**Conditional init with different subtypes per branch.** When both branches of an `if/else` assign `self.x` with different types, pyright infers the _union_ of the two branch types, not the type you intended. Example: one branch sets `self.client_id = config.google_client_id` (typed `str`), the other sets `self.client_id = os.getenv("HERDS_GOOGLE_CLIENT_ID")` (typed `Optional[str]`). Downstream code that expects a consistent `Optional[str]` sees a messy union. Fix by forward-declaring the attribute's type before the `if/else`:

```python
self.client_id: Optional[str]
if config:
    self.client_id = config.google_client_id        # narrows str → Optional[str]
else:
    self.client_id = os.getenv("HERDS_GOOGLE_CLIENT_ID")
```

The forward declaration gives pyright a single target type; both branch assignments widen into it.

## The `def f(x: str = None)` antipattern _(type-only — declaration fix only; no runtime behavior change)_

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

## When to reach for `Protocol` vs `ABC` vs plain duck typing

The recipe above covers the missing-`self` trap once you've chosen Protocol. The upstream question — _should_ this be a Protocol? — comes up during adoption when you're deciding how to type a pluggable surface (providers, storage backends, notifiers).

**Use `Protocol` when:**

- Multiple unrelated classes or modules satisfy the same shape and you don't control all of them. Structural match means nothing has to inherit; anything that duck-types fits, including module-level functions and third-party classes you can't modify.
- You want existing objects recognized _without_ wrapping or adapter layers.

**Use `ABC` (abstract base class) when:**

- You own all the implementations and want them forced to inherit — ABC runs the check at instantiation, Protocol does not.
- You need shared concrete implementation alongside the abstract interface. ABC can mix `@abstractmethod` declarations with concrete helper methods that subclasses inherit; Protocol cannot provide inheritable behavior.
- The type is public API and you want callers to explicitly opt in via inheritance (declaration visible in the MRO).

**Use plain duck typing (no formal type) when:**

- The shape is used in one place, by one caller, and lifting it into a Protocol adds more ceremony than insight.

**Common mistake: defaulting to ABC.** Teams coming from Java / C# reach for ABC because "interfaces should be declared somewhere." In Python, Protocol is usually the better default for interface-like contracts — it costs nothing to add, existing code fits without changes, and `@runtime_checkable` can be added if `isinstance` support matters. Reserve ABC for when the _forcing_ matters (you want to block subclasses that don't implement required methods) or when you genuinely need shared concrete methods.

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

When pyright reports `Arguments missing for parameters "default_calendar", "sort_by", "sort_order"` on a `SomeModel(...)` call _and_ those fields are declared `Optional[T] = Field(<something>, ...)` in the schema, the schema is the bug — not the caller. The error message reads as "you forgot to pass these" but the root cause is positional-default form pyright can't see. Grep the schema for `Field(<positional>,` — almost always a hit.

This matters because the fix is upstream: one `Field(None, ...)` → `Field(default=None, ...)` in the schema deletes the error at every caller. Point-fixing each caller with `# pyright: ignore[reportCallIssue]` spreads the workaround instead. See `reference.md` § "Repetition as a signal" for the broader pattern.

## `cast(Model, payload)` at pydantic list boundaries

When passing `list[dict]` where `list[SomeModel]` is required, pydantic coerces at runtime via `model_validate`, but pyright needs the hint:

```python
# Pydantic coerces the dict → Model at runtime, but pyright needs the cast
self.events_result = EventsResult(events=cast(List[ProcessedEvent], [saved_event]))
```

Cleaner than building the model eagerly just to satisfy pyright — the runtime validation is the same either way.

### The hidden-missing-field trap

**`cast(T, dict_literal)` is a code smell, not a solution.** The cast silences pyright but also hides missing _required_ fields in `T`. The worst-case shape is `cast(List[Order], [{}])` — type-checks cleanly, structurally invalid at runtime, zero compile-time help. Prefer the real constructor:

```python
# Wrong — pyright happy, runtime will explode if required fields missing
orders = cast(List[Order], [{"id": "ord-1"}])

# Right — pyright validates the shape against Order's field set
orders = [Order(id="ord-1", customer="alice", total=Decimal("12.50"))]
```

`cast(T, x)` is appropriate only when `x`'s _runtime_ value genuinely matches `T` but pyright can't see it — e.g. crossing a `model_dump()` → re-cast-back boundary, or a dict from an API client you control and have reason to trust. `cast(T, {literal})` is almost never that case; the literal is _your_ construction, and a real constructor call would let pyright check it.

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

## Schema projection via `model_validate`, not `cast`

When projecting from a wider pydantic model `A` to a narrower model `B` where B's fields are a subset of A's, the natural first attempt casts the dumped dict to `B`:

```python
# Wrong — cast says "this is B" but runtime is a dict
user_settings = cast(UserSettings, settings.model_dump(
    include=set(UserSettings.model_fields.keys())
))
```

Pyright is happy, but every downstream consumer that expects B's attribute access / methods / `isinstance` check gets a dict at runtime. `user_settings.notifications_enabled` raises `AttributeError`. This is the same class of shape lie as `cast(List[Order], [{}])` in "The hidden-missing-field trap" above — pyright-clean, runtime-broken.

**Fix: round-trip through `B.model_validate()`.** The canonical pydantic-v2 idiom for dict-to-model conversion:

```python
# Right — constructs a real B instance, runs B's validators
user_settings = UserSettings.model_validate(settings.model_dump(
    include=set(UserSettings.model_fields.keys())
))
```

Now `user_settings` is genuinely a `UserSettings` — attribute access, `isinstance` checks, and B's validators all work. `include=` projects A → dict-of-B's-keys; `model_validate` then builds the dict into an actual B instance.

### Decision rule by target type

- **Target is a `TypedDict`** → `cast(T, dict)` is acceptable. TypedDicts _are_ dicts at runtime; there's no behavior to wrap.
- **Target is a `BaseModel` or `@pydantic.dataclasses.dataclass`** → always `Model.model_validate(...)`. The target has methods, validators, and serialization behavior that a plain dict doesn't provide.
- **Target is a vanilla `@dataclasses.dataclass`** → use the real constructor (`Model(**dict_value)`); dataclass has no `model_validate`.

The principle: **casts lie about shape; `model_validate` builds the real thing.** The runtime cost of one constructor call is negligible next to the debugging cost of "why is `.field` raising `AttributeError` when pyright says it's a `Model`?"

### Where projection shows up

Most commonly at API boundaries: an internal model gets projected onto a public response schema. The `include=` set names the intersection; `model_validate` re-runs the public schema's validators on the projected dict. That second validation is often _desirable_ — public-facing fields may have stricter validators than the internal superset (email format, length limits, enum membership) that the internal model doesn't enforce.

## Opaque `dict[str, Any]` with repeated key reads

**`--intent improve` only.** Pyright does not emit an error here — the code is fully type-clean — so this recipe is triggered by file scan, not by a rule name.

A `dict[str, Any]`-typed value (parameter, local, return, or attribute) read through **3+ distinct literal keys** inside one function body or file is a signal that an un-named data contract is being smuggled through a generic container. Extraction gives the contract a name pyright and future readers (human or agent) can trace.

### Decision tree

0. **Does this dict come directly from an external producer?** (DB client response like Supabase/PostgREST `.data`, `httpx`/`requests` `.json()`, subprocess `stdout`, file-parse output, cross-service RPC response, Postgres `fetchall()` rows.) If yes, extract a **Pydantic `BaseModel`** and call `Model.model_validate(...)` at the boundary. **Skip the rest of the tree** — boundary validation is the goal, and TypedDict's unchecked-claim semantics are the wrong tool for data crossing a trust boundary. A producer regression (schema drift, server-side bug, wire-format change) that `TypedDict` would silently admit becomes a clear `ValidationError` at the boundary with `BaseModel`. Steps 1–4 apply only to **internal** dicts — values your own code constructs or static configs.

1. **Is the dict mutated at many sites?** (`d["k"] = ...`, `.pop()`, `.update()`, `del d["k"]`.) If yes, **keep `dict[str, Any]`.** TypedDicts don't cleanly support arbitrary-key mutation at the type level — the same caveat flagged in the `Mapping[str, Any]` section above. Do not extract.
2. **Is the source a stable internal contract?** (A dict your own code constructs from a known schema — e.g., a config-loader result, a state-machine payload.) Extract a **`TypedDict`**. Keys that may be absent become `NotRequired`.
3. **Is pydantic already imported in the file, and would validation/defaults/coercion help** even for an internal value? Extract a **`BaseModel` subclass** and replace `d["k"]` reads with attribute access after `Model.model_validate(d)` at the construction site.
4. **Otherwise** (mixed-shape dict, keys determined at runtime, or the dict flows through too many generic helpers to re-plumb) — keep `dict[str, Any]` and move on.

**Signal patterns that land in step 0.** Any of these in the source near the repeated reads is strong evidence of a boundary case:

- `cast(list[dict[str, Any]], response.data)` — Supabase / PostgREST client
- `cast(SomeShape, response.json())` — `httpx` / `requests` / similar
- `cast(Dict[str, Any], json.loads(process.stdout))` — subprocess output
- `cast(Dict[str, Any], cursor.fetchone())` — DB-API raw access
- The dict is returned from a class method named `read()`, `get()`, `find()`, `list()`, `upsert()`, `insert()`, `query()`, or similar data-access shape — the "document class"-equivalent pattern belongs at _that_ class, not at the consumer. See `reference.md` § "Before dict-shape extraction: check for a data-access class".

### Pause and ask before writing

This is semantically loaded in the same class as `bool | None → bool` — the extraction surfaces four user-visible decisions the recipe cannot make alone:

1. **Name** of the new type (`UserProfile` vs. `UserRecord` vs. `UserDict`).
2. **Location** (`models.py`, a new `schemas/` module, colocated with the caller).
3. **Optional keys**: `NotRequired[T]` vs. `T | None` — they are not equivalent. `NotRequired` means "may be absent"; `T | None` means "present, but may be null."
4. **Migration radius**: does this extraction force call-site updates elsewhere, and is that in scope for this run?

Present these to the user as a short proposal and wait for approval. If approval isn't forthcoming, do nothing — the code was already type-clean, leaving it alone is a valid outcome.

### Before / after — producer boundary (step 0)

The canonical shape for a dict coming off an external producer. Producer-agnostic — the same pattern applies to DB clients, HTTP clients, subprocess JSON, file parses, RPC responses. For a concrete Supabase instance see `libraries.md` § "Supabase" (query `.data`).

```python
# Before — opaque dict from an external producer, reads scattered across consumers
def load_user(user_id: str) -> dict[str, Any]:
    response = external_client.fetch(user_id)          # returns loose JSON
    return cast(dict[str, Any], response.body)

user = load_user("u1")
email = user["email"]
tier = user.get("tier", "free")
created = user["created_at"]
```

```python
# After — BaseModel validated at the boundary; consumers get attribute access
from pydantic import BaseModel

class UserRecord(BaseModel):
    email: str
    created_at: datetime
    tier: Optional[str] = None          # DB-managed / absent in some rows

def load_user(user_id: str) -> Optional[UserRecord]:
    response = external_client.fetch(user_id)
    if response.body is None:
        return None
    return UserRecord.model_validate(response.body)    # boundary check

user = load_user("u1")
if user is None:
    raise LookupError("user u1 not found")
email = user.email
tier = user.tier or "free"
created = user.created_at
```

Schema drift at the producer now surfaces as a named `ValidationError` at the `model_validate` call, not as a silent `KeyError` deep in a consumer. The `cast` is gone: it was an unchecked claim, replaced by a verified one. Before running this against real rows, see `reference.md` § "Verify boundary models against the real producer".

### Before / after — internal contract (step 2)

For a dict your own code constructs (not an external producer), a `TypedDict` is usually enough — no runtime validation, just a shape pyright enforces at the call site.

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

- **Test factory**: repeated `cast(T, {...})` or TypedDict-literal _construction_ (already pyright-errored or suppressed).
- **Opaque dict**: repeated _reads_ off a generically-typed source (pyright-clean, no error).

Don't merge them — their decision trees and targets differ.

## Prefer `@staticmethod` over module-level free functions for class-adjacent helpers

Triggered when you extract a pure method from an instance method — typically to eliminate a `cast(Required, None)` construction smell (see `reference.md` § "`cast(Required, None)` at construction is a refactor signal"). You now have a pure function that uses none of `self`'s state; where should it live?

```python
# Original
class S3Storage:
    def __init__(self, supabase: Client, bucket_name: str):
        self.supabase = supabase
        self.bucket_name = bucket_name

    def generate_authenticated_image_url(self, image, path_key) -> Optional[str]:
        # Reads only self.bucket_name, not self.supabase
        ...
```

Extract options:

```python
# Option A: @staticmethod on the class (preferred for class-adjacent helpers)
class S3Storage:
    def generate_authenticated_image_url(self, image, path_key) -> Optional[str]:
        return S3Storage.build_authenticated_image_url(image, path_key, self.bucket_name)

    @staticmethod
    def build_authenticated_image_url(image, path_key, bucket_name) -> Optional[str]:
        ...

# Option B: module-level free function next to the class
def build_authenticated_image_url(image, path_key, bucket_name) -> Optional[str]: ...

class S3Storage:
    def generate_authenticated_image_url(self, image, path_key):
        return build_authenticated_image_url(image, path_key, self.bucket_name)
```

**Prefer Option A when** the helper is class-adjacent — operates on the class's domain, would naturally be looked for on the class, or is a pure variant of an existing instance method. The `@staticmethod` form keeps the conceptual grouping visible at the call site (`S3Storage.build_authenticated_image_url(...)`) and avoids polluting the module namespace.

**Prefer Option B when** the helper is genuinely class-independent — e.g. a pure parsing function (`parse_month_day(date_str)`) that existed before the class and would exist without it.

### Test-patch-target durability as the tiebreaker

When it's a judgment call, the test-mock implication is the deciding factor:

- `@patch("app.services.s3_storage.S3Storage.build_authenticated_image_url")` — stable across module reorganization. The helper can move between files without breaking the patch target; only a _rename_ does.
- `@patch("app.routes.image_endpoints.build_authenticated_image_url")` — the helper was imported into that module's namespace. If the helper is later moved to a different file, every test patching it through this module breaks, even though nothing about the helper itself changed.
- `@patch("app.services.s3_storage.build_authenticated_image_url")` — middle ground. Stable against consumer refactors but not against helper-file moves.

Class-attachment gives the patch target a stable fully-qualified name (`module.ClassName.helper`) that tracks the class, not the filesystem. For helpers you expect to be widely mocked in tests, this alone can justify Option A over B.

### Codify the preference

If this matches your team's style, add it to the project's contributor guide / `CLAUDE.md` / equivalent. The fork is invisible in pyright output — type-clean either way — so without a codified preference, different agents/contributors will pick different shapes on the same codebase. Example codification:

> **Static helpers live on their class, not as module-level functions.** When a helper is class-adjacent (operates on the same domain, or is a pure variant of an instance method), put it on the class as a `@staticmethod`. Callers reach it via `ClassName.helper(...)`. Reserve module-level functions for genuinely class-independent helpers.

## `reportGeneralTypeIssues` / `"None" is not iterable`

`for x in maybe_none:` when `maybe_none: list[...] | None`. Add `assert maybe_none is not None` before the loop, or use `for x in maybe_none or []:` if empty-iteration is the desired fallback.

## `reportOptionalOperand` _(usually type-only; raise variant is behavior-changing — same principle as `reportOptionalMemberAccess`)_

`result + 1` when `result: int | None`. Same fix: narrow with an assert first.

## `reportMissingImports`

Real issue. Usually `sys.path`-manipulated imports, or genuinely-missing modules. If the `sys.path` manipulation is intentional, `# pyright: ignore[reportMissingImports]` is acceptable.

## Third-party library intake flow

When a new library arrives and pyright complains about missing types (`reportMissingTypeStubs`, `reportUnknownMemberType` on its symbols, or cascading `Unknown` into call chains), four fixes exist. Try them in this order — each step down is a step toward "the type checker can't help me here":

1. **Install typeshed stubs: `pip install types-<library>`.** Real type information, zero config change. Common names: `types-requests`, `types-PyYAML`, `types-cachetools`, `types-python-dateutil`. Search PyPI for `types-<name>` before assuming stubs don't exist.

2. **Enable `useLibraryCodeForTypes`.** Pyright infers types from the library's source when stubs are absent. Works well for libraries with thorough inline annotations; degrades to partial types (leaking `Unknown`) for libraries that annotate inconsistently.

   ```toml
   [tool.pyright]
   useLibraryCodeForTypes = true
   ```

   Fine under `basic` mode; under `strict`, the partial-types leak becomes visible and you'll likely want to pair it with `allowedUntypedLibraries` for the worst offenders.

3. **Generate stubs with `pyright --createstub <module>`.** Writes skeleton `.pyi` files to a local `typings/` directory, which you commit and maintain. Treat the generated stub as a starting point — fill in real types for the functions you actually call; the `Unknown` placeholders are useless until you do. Worth it for a library that's deeply depended on and whose inferred types are inadequate.

4. **Scoped suppression.** Last resort. Prefer `allowedUntypedLibraries` (per-library) over `reportMissingTypeStubs = "none"` (project-wide): the scoped form names which library lacks types, so a future maintainer can revisit.

   ```toml
   [tool.pyright]
   allowedUntypedLibraries = ["some_lib", "some_lib.submodule"]
   ```

**Why the order.** Stubs give real types; `useLibraryCodeForTypes` gives inferred (often partial) types; generated stubs give a skeleton you own; suppression gives nothing. Stop at the first step that solves the problem; don't jump to suppression because it's fastest — that silences the error but also silences the type checker for everything that library touches downstream. See the related `libraries.md` recipes for library-specific stub fixes; this flow is the "what do I try _first_" layer before any of those.

**When to contribute stubs upstream.** If you're maintaining `.pyi` files for a library you've patched or extended, contributing those stubs upstream (to typeshed or the library itself) is almost always better than keeping them in your `typings/` forever — one commit, every consumer benefits.

## `TYPE_CHECKING` for type-only imports

Pyright sees imports inside `if TYPE_CHECKING:` blocks when type-checking but skips them at runtime. Two places this matters:

1. **Circular imports where each side only needs the other for annotations.** Module A imports B for a parameter type; B imports A for the same reason. Runtime breaks with `ImportError`. Fix: put one side's import under `TYPE_CHECKING` and quote the annotation (or use `from __future__ import annotations` to make every annotation a string automatically).

   ```python
   from __future__ import annotations
   from typing import TYPE_CHECKING

   if TYPE_CHECKING:
       from app.services.billing import BillingClient

   def process(client: BillingClient) -> None:
       ...
   ```

2. **Heavy imports whose only runtime cost is annotations.** Big modules (`pandas`, `torch`) imported only for type names. Same pattern — guard the import, quote or `__future__`-annotate.

**The gotcha.** Without `from __future__ import annotations` (PEP 563), bare annotations still evaluate at runtime — `BillingClient` would raise `NameError` because the import didn't happen. Two fixes: quote the annotation (`def process(client: "BillingClient") -> None:`), or add the future import at the top of the file. The future import is usually cleaner — it makes _all_ annotations strings, not just the guarded one.

**Don't overuse.** `TYPE_CHECKING` hides imports from readers who grep for dependencies. Use it for the two cases above, not as a generic "this import is only for annotations" cleanup. One-off type-only imports in an otherwise normal module don't need the machinery — normal imports are fine.

## Assigning `bool | None` to a `bool` field _(behavior-changing — picks a default for None; type-only alternative is to widen the field to `Optional[bool]`)_

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

## When `Optional[T] → T` coercion _is_ fine _(behavior-changing — `or default` picks a value for None; safe only when the default is the intended treatment of None)_

The inverse case of the `bool | None` warning: when `None` genuinely means "unset, use sensible default," a simple `x or default` at the assignment site is clean and preserves information. Example: a `Config.timezone: Optional[str]` flowing into a TypedDict field `timezone: str`.

```python
ctx: HerdsContext = {
    "timezone": config.timezone or "UTC",
    ...
}
```

This is safe because the fallback (`"UTC"`) is semantically meaningful — it's the intended default when the user hasn't configured a timezone — not a coerced placeholder that destroys a meaningful `None`. Same question as the `bool | None` section, different answer: _does `None` carry different meaning than the falsy value?_ Here, no — "unset" and "UTC" are the intended same-state. Coerce.

The distinction between the two sections collapses into one rule: coerce `Optional[T] → T` when the default is the intended treatment of `None`; widen the destination to `Optional[T]` when `None` carries a distinct meaning that downstream consumers rely on.

## Stale `@overload` stacks become noise under strict _(type-only — deletes type-level signatures only; runtime body is unchanged)_

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

Fix: delete the overload stack and keep `def sanitize(data: Any) -> Any`. The overloads were load-bearing only when callers passed a _statically-typed_ `Dict`, `List`, or `None` — which is rarer than it looks once you audit the call sites.

Signals the overload stack is stale:

- Overloads differ only in what they pass through (identity-preserving shape; each branch returns the type it took in).
- The runtime body has an `isinstance`-dispatch over the same shapes — the overloads duplicate the runtime check at the type level.
- Strict flags most callers as "no overload matches" even though basic/standard were clean.

When in doubt, remove the overloads and re-run pyright. If new errors appear at call sites that specifically relied on overload-specificity, restore them targetedly. More often, they don't.
