# Plan: Add anti-`getattr()` rules to reasoning-gaps skill

## Context

In herds-social/herds#173, the reasoning-gaps tool's recommendations led to `getattr(obj, dynamic_key)` replacing `dict.get(key)` — a lateral move that doesn't improve AI readability. Example:

```python
# Before (dict-based):
field = _PROVIDER_FIELD_MAP[provider]
calendar_id = user_data.get(field)

# Bad fix (getattr — same opacity):
calendar_id = getattr(user_data, field, None)

# Correct fix (typed method — AI can trace):
calendar_id = user_data.get_calendar_id(provider)
```

The reasoning-gaps command needs rules at three layers to prevent this.

## File to modify

`plugins/harness/commands/reasoning-gaps.md` (single file, ~470 lines)

## Changes

### 1. Agent 1 — "Dict-based data passing" bullet (line 81)

Append a warning to the existing bullet. After "A Pydantic model or dataclass makes the shape explicit." add:

```text
CAUTION: `getattr(obj, dynamic_key)` is NOT a valid fix — it moves dynamic lookup from dict to attribute access. The AI still cannot trace which attribute is accessed or validate it exists at the type level. Fix with a typed method (e.g., `obj.get_calendar_id(provider)`) or a typed mapping (e.g., `dict[Provider, CalendarId]`) that encapsulates the lookup.
```

### 2. Agent 2 — "Dynamic dispatch" bullet (line 139)

Expand title and add attribute-access coverage. Replace the bullet with:

```text
- **Dynamic dispatch and dynamic attribute access** — `getattr(obj, method_name)()`, `getattr(obj, key)` (attribute lookup, not just calls), `registry[name]()`, strategy patterns with string-based lookup, `importlib.import_module()`. An AI agent cannot determine which code will execute or which attribute is accessed without tracing the runtime value. `getattr(obj, dynamic_key)` is especially harmful when it appears as a "fix" for dict-based access — it is equally opaque. Recommend a typed method that encapsulates the lookup.
```

### 3. Guidelines section — new bullet (after line 469)

Add as the final guideline:

```text
- **Never recommend `getattr()` as a fix.** Replacing `data[key]` with `getattr(obj, key)` moves dynamic lookup from dict to attribute access — the AI still cannot trace which attribute is accessed, cannot validate it exists at the type level, and cannot follow the data flow. Always recommend typed methods or typed mappings instead.
```

## Why three layers

1. **Agent 1** prevents recommending `getattr()` when fixing dict-based access (where the problem originates)
2. **Agent 2** detects existing `getattr()` in code under analysis (catches it if already present)
3. **Guidelines** acts as a universal backstop during Phase 3 merge and intervention design

## Verification

1. `claude plugin validate .` passes
2. Run `/harness:reasoning-gaps` on code with `getattr()` patterns — verify Agent 2 flags them
3. Run `/harness:reasoning-gaps` on code with dict-based access — verify Agent 1's fix recommendations do NOT suggest `getattr()`
4. Check that interventions in the merged report use typed-method or typed-mapping language, not `getattr()`
