# Bug classes pyright uncovers

Documenting these because they're the return on investment. Each is a bug class that sits dormant until pyright forces you to look at it. These are distinct from the fix recipes in `rules.md`: a rule recipe tells you how to silence a pyright complaint, but the signals below tell you when the complaint points at a *real bug* that should be flagged to the user rather than silenced.

## Attribute read that never existed

Code reads `obj.some_field` in multiple places, but `some_field` was never declared on the class (perhaps removed during a refactor, or renamed, or only ever planned). Every read would raise `AttributeError` at runtime. The bug persists because the code path isn't exercised by tests.

Lesson: when pyright flags an attribute as missing, check whether the code reaches that attribute via a pathway tests cover. Often it doesn't, which is why the bug survived.

## Subclass attribute shadows inherited method

Inside a `unittest.TestCase` subclass, assigning `self.run = some_value` clobbers the inherited `TestCase.run()` method. Tests still pass because they don't invoke the shadowed method after assignment, but the shadowing is a latent landmine. Pyright flags it as a type mismatch (method vs. data value).

More generally: assigning a plain attribute with the same name as an inherited method is almost always a bug. Rename the attribute.

## Repeated side-effectful call in a loop

`while data[next_point()] is not None: use(next_point())` calls `next_point()` twice per iteration because the result is read in the condition AND consumed in the body. Pyright's "subscript of None" complaint on the second call often makes the pattern visible. Bind the result once:

```python
while (p := next_point()) is not None:
    use(p)
```

## Dead field referenced through a `# type: ignore` or `# pyright: ignore`

A `# pyright: ignore[reportAttributeAccessIssue]` on a line that reads a nonexistent field is a signal, not a fix. Investigate whether the field should exist on the class, or whether the reference is dead code to be removed. Suppressions can mask the "attribute never existed" bug class above.

## Dead module / class constants

A variant of "attribute read that never existed," specific to class- or module-level constants:

```python
# app/exceptions.py
class ErrorTypes:
    NOT_FOUND = "not_found"
    AUTH_FAILED = "auth_failed"
    # VALIDATION_ERROR never declared

# app/routes/notification_endpoints.py
raise HerdsHTTPException(error_type=ErrorTypes.VALIDATION_ERROR, ...)  # AttributeError at runtime
```

Persists because the raise-path isn't exercised by tests (usually exists only on the error branch of a rarely-hit route). Pyright's `reportAttributeAccessIssue` on `ErrorTypes.VALIDATION_ERROR` is the first signal anything is wrong. Fix by adding the constant on the class (if the intent was to have it) or removing the read (if it was a typo / leftover from a removed feature).

## Reversed dict-direction lookups

A dict built with the keys and values swapped — every lookup silently returns the default. Pyright's `reportArgumentType` on the lookup key surfaces the shape mismatch; nothing else had for years.

```python
# Wrong — keys are the string names, but all consumers pass int tag IDs
self._tag_names = {v: k for k, v in ExifTags.TAGS.items()}   # type: Dict[str, int]

# Later: always falls through to default
tag_names.get(tag_num, f"Tag_{tag_num}")   # tag_num is int, keys are strings

# Right
self._tag_names = dict(ExifTags.TAGS)   # type: Dict[int, str]
```

Signals: pyright flags the lookup (not the construction); the consumer always passes the default-producing fallback; git blame shows the dict was written once and never touched. Fix changes *observable behavior* (defaults are no longer silently returned) — worth flagging in the PR description.

## Parameter the callee doesn't accept

Distinct from "attribute read that never existed" — this is the kwargs version:

```python
# Caller
await image_service.update_image(image_id=img_id, original_size_mb=size)

# Callee (different module)
async def update_image(self, image_id: str, **fields) -> None:
    # accepts arbitrary fields? Actually no — body uses a whitelist.
    ...
```

Pyright's `reportCallIssue` on the caller's kwarg is the signal. At runtime, a `**kwargs`-accepting callee will silently drop the unknown kwarg; a strict-signature callee will `TypeError`. Either way, the data the caller thought it was passing doesn't land. Check the callee's body — does it actually consume that kwarg? If not, either add it to the callee's parameters (and have the body use it) or remove it from the call.
