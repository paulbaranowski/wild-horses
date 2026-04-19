# Library typing gaps

These are pyright-facing bugs in third-party stubs or packages, not your code. The right move is a targeted `# pyright: ignore[specificRule]` with a short comment if the rule isn't self-explanatory.

Rule-specific fix recipes (e.g. `reportOptionalMemberAccess`, `reportArgumentType`) live in `rules.md`. Suppression policy lives in `reference.md` § "Suppression policy".

## `bitstring.BitArray` iteration

The library's own type stubs declare `BitArray.__iter__ -> Iterable[bool]` instead of `Iterator[bool]`, so pyright sees `for bit in ba:` as iterating a non-iterable. At runtime it works fine.

```python
for bit in bits:  # pyright: ignore[reportGeneralTypeIssues]
    ...
```

Alternative: `for i in range(len(ba)): bit = ba[i]` or `iter(ba)`.

## `scipy.stats` results typed as `_`

Functions like `ks_2samp`, `ttest_ind` return an under-annotated `_` placeholder instead of the real `KstestResult` / `TtestResult`. Accessing `.statistic` / `.pvalue` trips pyright even though they exist at runtime (namedtuple fields).

Options (preferred first):
1. Cast to `Any` then access: `res = cast(Any, ks_2samp(...))`.
2. Cast to the specific result class if you can import it: `cast(scipy.stats._stats_py.TtestResult, res)`.
3. `# pyright: ignore[reportAttributeAccessIssue]` if both feel worse than just suppressing.

When using option 1, remember to import `cast` and `Any` from `typing`. It's an easy miss because the cast "feels like syntax" — and the resulting errors cascade. An undefined `cast(Any, ...)` produces a downstream `Cannot access attribute "statistic" for class "_"` on the result, which reads like a separate problem until you notice the root `reportUndefinedVariable` at the call site. Fix root errors first, recheck, then chase what remains.

## `tornado.httputil.HTTPConnection.stream` missing from stubs

Tornado's stubs omit `connection.stream`, which exists at runtime. Pattern:

```python
stream = self.request.connection.stream  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess]
```

If the access is already defended by `try/except AttributeError`, the suppression doesn't hide a bug.

## `matplotlib` `plt.hist(bins=...)` expects `int | Sequence[float] | str | None`, rejects `np.ndarray`

Even though `NDArray[float64]` is a `Sequence[float]` at runtime, matplotlib's stubs don't accept it. Cast:

```python
plt.hist(data, bins=cast(Sequence[float], edges))
```

Or pre-convert: `bins=edges.tolist()`.

## Tornado `RequestHandler` mixin attributes

If a class is declared as a bare `class FooMixin:` but uses `self.request`, `self.set_status`, `self.write` assuming it will be mixed into `RequestHandler`, pyright can't see those attributes. Fix by inheriting directly from `tornado.web.RequestHandler` (making it a concrete base, not just a mixin), or protocol-type the `self` via an intersection (overkill for most cases).

## Beanie (MongoDB ODM)

Beanie wraps pydantic + motor for MongoDB. It has its own ecosystem of pyright-unfriendly patterns — worth knowing as a cluster because any Beanie-using project will hit most of them.

**`Indexed(T)` pseudo-type.** The shorthand makes pyright see the field as `Any`; the `Annotated` form preserves the type. Beanie docs treat both as equivalent:

```python
# Wrong — field type becomes Any; downstream reads lose typing
class UserDoc(Document):
    email: Indexed(str, unique=True)

# Right
class UserDoc(Document):
    email: Annotated[str, Indexed(unique=True)]
    session_id: Optional[Annotated[str, Indexed()]] = None
```

**Sort string syntax over `-cls.field`.** `sort(-cls.created_at)` produces unary-minus-on-a-field-descriptor that pyright can't type; `sort("-created_at")` is string-based and clean.

**Delete results: `deleted_count: int | None`.** Always guard before use:

```python
result = await SomeDoc.find(...).delete()
count = result.deleted_count if result is not None else 0
```

**Document construction requires explicit `None` for every `Optional` field.** Pyright's strict view of pydantic construction flags missing kwargs even when the runtime would accept them via defaults:

```python
# Wrong — pyright complains about missing updated_at, read_at, etc.
doc = NotificationDocument(user_id=uid, message=msg)

# Right — pass None explicitly for every Optional field, even ones with defaults
doc = NotificationDocument(user_id=uid, message=msg, read_at=None, updated_at=None)
```

**`.collection` returns loose types; use `.get_motor_collection()`.** The latter is stub-typed; the former is `Any`.

**Return types: `List[Self]` → `Sequence[Self]` on classmethods.** `List` is invariant, so `List[Subclass]` is not assignable to `List[Base]`. When a classmethod declares `-> List[Self]`, subclass callers that annotate the result as `List[BaseDoc]` break. `Sequence[Self]` is covariant and fixes both the call-site assignment and pyright's complaint:

```python
@classmethod
async def get_by_user(cls, user_id: str) -> Sequence[Self]:
    return await cls.find(cls.user_id == user_id).to_list()
```

## Supabase (auth + query client)

**Auth responses: narrow `response.user` and `response.session` before use.** Both are `Optional` after `sign_in_with_password` / `sign_up`, contrary to what calling code tends to assume:

```python
response = client.auth.sign_in_with_password({"email": e, "password": p})
if response.session is None or response.user is None:
    raise HerdsHTTPException(status_code=401, error_type="auth_failed", detail="...")
# both narrow from here
```

**Query `.data` is loose `Any`.** Cast at the boundary rather than operating on `Any`:

```python
response = client.table("users").select("*").execute()
rows = cast(list[dict[str, Any]], response.data)
```

**`SignInWithIdTokenCredentials` TypedDict boundary.** Building the credentials dict and passing it to `sign_in_with_id_token` requires a cast because the parameter is a TypedDict, not `dict[str, Any]`. See `rules.md` § "TypedDict ↔ `dict[str, Any]` asymmetry".

## `litellm.completion()` returns `ModelResponse | CustomStreamWrapper`

When you know streaming is off, cast to `ModelResponse` — otherwise `.choices` access trips pyright on the union:

```python
from litellm import completion
from litellm.types.utils import ModelResponse

response = cast(ModelResponse, completion(model=..., messages=..., stream=False))
content = response.choices[0].message.content  # pyright happy
```

## `pic_prompt.get_prompt()` return-type lies

Declared `-> str`, returns `List[Any]` at runtime. Cast at the call site:

```python
prompt = cast(List[Any], pic_prompt.get_prompt(...))
```

## Dynaconf `Validator(messages={...})` accepts arbitrary keys

Stubs type `messages` as `dict[str, str]` but the validator stores arbitrary documentation blocks under non-standard keys. Suppress the single call with a one-line why:

```python
Validator(
    "MY_FEATURE_FLAG",
    messages={"operations": "Long doc block here..."},  # pyright: ignore[reportArgumentType]
)
```

## PIL `ImageCms.profileToProfile` may return `None`

Stubs declare `Optional[Image]`; runtime code usually dereferences the result without guarding. Add the None check:

```python
transformed = ImageCms.profileToProfile(img, src_profile, dst_profile)
if transformed is None:
    raise RuntimeError("ICC profile conversion failed")
```

## Tenacity retry-callback state

Inside a `retry=` / `before_sleep=` callback, `retry_state.outcome` can be `None` before the first attempt has completed. Pyright flags the access. Extract a small helper that asserts tenacity's invariants and keeps the retry-config code readable:

```python
def _log_retry(retry_state: RetryCallState) -> None:
    outcome = retry_state.outcome
    assert outcome is not None  # tenacity guarantees this inside before_sleep
    exc = outcome.exception()
    assert exc is not None
    logger.warning("Retry %d after %s", retry_state.attempt_number, exc)
```

## pymongo `OperationFailure` — `has_error_label` over private attr

Reading `e._OperationFailure__details.get("errorLabels", [])` to detect transient transactions is a name-mangled private-attribute access; pyright flags it, and pymongo can rearrange internals between versions. The public API is documented:

```python
# Wrong — private, pyright flags, fragile across pymongo versions
if "TransientTransactionError" in e._OperationFailure__details.get("errorLabels", []):
    ...

# Right
if e.has_error_label("TransientTransactionError"):
    ...
```

Variant of the "prefer documented API" pattern in `reference.md` § "Prefer a documented API over 'works at runtime' tricks", with a concrete pymongo recipe.

## Pydantic BaseModel fields with TypedDict types enforce at runtime

A `BaseModel` field annotated with a TypedDict is not a pure type-checker annotation. Pydantic reads the TypedDict's schema and validates every key at runtime:

```python
from typing import TypedDict
from pydantic import BaseModel

class ImageRecord(TypedDict):
    id: str
    user_id: str
    path: Optional[str]
    # ... 25 more required keys

class ProcessingResult(BaseModel):
    image_record: ImageRecord   # pydantic validates ALL 27 keys at construction
```

**Breakage pattern.** Tests passing partial dicts to `ProcessingResult(image_record={"id": "x", "user_id": "y"})` will suddenly fail with pydantic validation errors (`image_record.path: Field required [type=missing]`) once the field is tightened from `dict` to a TypedDict. The mental model "TypedDict annotations are metadata, dicts are dicts at runtime" is wrong inside pydantic.

**Diagnostic.** If tightening `field: dict` to `field: SomeTypedDict` in a `BaseModel` is followed by test failures with `type=missing` validation errors on partial-dict fixtures, this is the cause — not your test changes.

**Fix.** Union with `Dict[str, Any]` to keep static tightness without runtime enforcement:

```python
class ProcessingResult(BaseModel):
    image_record: ImageRecord | Dict[str, Any]
```

**Why the union works at runtime.** Pydantic tries union branches in declared order and accepts the first that validates. `Dict[str, Any]` accepts any dict, so partial-dict fixtures pass validation against that branch without invoking the TypedDict's per-key checks. Production code matching `ImageRecord` validates against the stricter branch first and retains full schema enforcement.

**Static-typing side.** Pyright narrows to `ImageRecord` at call sites where the value matches it, and to `Dict[str, Any]` otherwise — so the union doesn't lose static precision for well-formed callers.

**Not applicable to.** Function parameters, function return types, *vanilla* `@dataclasses.dataclass` fields, and raw module-level annotations — the runtime enforcement only happens through pydantic's model-construction pathway. Note: `@pydantic.dataclasses.dataclass` *does* enforce at runtime, so the same union workaround applies there. Tightening `def foo(x: dict)` to `def foo(x: SomeTypedDict)` is a pure type-checker change with no runtime effect.

**Operational guidance.** If you're annotating a `BaseModel` field with a TypedDict for the first time in the codebase, run the test suite immediately after — not just pyright. The failures are runtime-only and won't appear in the type-checker's report.

## Optional-runtime dependencies: inline import + suppress

Modules that are *intentionally* optional at runtime (the app should still load if the package isn't installed) must be imported inside a `try/except ImportError` — which means inline in a function, not at module level. The suppression here is correct and different from the `sys.path`-manipulation case:

```python
def read_qr(img: Image.Image) -> Optional[str]:
    try:
        from qreader import QReader  # pyright: ignore[reportMissingImports]
    except ImportError:
        return None
    ...
```

The `# pyright: ignore[reportMissingImports]` with a one-line why — "optional runtime dep; module must still load without it" — is the right shape. Distinguish from the `sys.path` case by whether the import is truly optional (qreader feature degrades) vs. genuinely required (`sys.path` trick to find it).
