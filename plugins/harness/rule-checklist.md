# Rule Checklist — Write-Time Self-Check

This is the condensed write-time companion to `/harness:reasoning-gaps` and `/harness:feedback-blockers`. The two analyzer commands surface gaps in code that already exists; this document is the **write-time mirror** — walk it on new and modified code before declaring a task done.

It is consumed by the executor (whether that's `superpowers:executing-plans`, a fresh subagent under `superpowers:subagent-driven-development`, or a human) at the end of each task.

---

## Reasoning-Gaps checklist

_Can a future agent understand this code well enough to change it correctly?_

- [ ] **Typed signatures** — every public function/method has parameter and return type annotations. No `Any`. No bare `list`/`dict` — use `list[Foo]`, `dict[K, V]`.
- [ ] **No dict-based contracts** — data passed between functions uses a `dataclass`, Pydantic model, or `TypedDict`, not `dict[str, Any]`. Stringly-typed status/event values become `Enum`s. (`getattr(obj, dynamic_key)` is **not** a fix — it's the same opacity moved one layer.)
- [ ] **No hidden flow** — no decorator that changes return type or adds I/O without a one-line comment; no import-time side effects; no dynamic dispatch without an explicit registry type.
- [ ] **Module/class docstring on new files and classes** — one or two sentences saying what it is and why it exists. Be concrete: not "the user service", but "validates and persists user records; the only writer to the users table".
- [ ] **No "why" gap** — magic numbers, regex patterns, workarounds, business rules each get a one-line `# why:` comment. The _why_, not the _what_.

---

## Feedback-Blockers checklist

_Can this code be tested and changed safely?_

- [ ] **Dependencies injected, not constructed** — the new code accepts collaborators via parameters/constructor; it doesn't `Database()` or `requests.get(...)` deep inside its logic.
- [ ] **No untestable side effects** — the core logic is callable without I/O. I/O sits at the edge.
- [ ] **No non-determinism without a seam** — `datetime.now()`, `random`, `uuid.uuid4()`, env reads come from injected providers (or are passed in), not called inline.
- [ ] **Errors are loud and located** — exceptions name the failing input, the failing component, and a hint of cause. No bare `except:` or `except Exception:` swallowing context. No "something went wrong" messages.
- [ ] **Encapsulation honored** — fields and methods that should be private _are_ private (leading underscore in Python; `private` / `#` in TS/JS). Mutable internals aren't returned by reference.
- [ ] **Single responsibility** — the new class/function does one thing. If the name needs "and" to describe it, it's two things — split.

---

## How to use this checklist

- **Walk both lists at the end of each implementation task** (or per-file if the task is large). If a box is unchecked, fix it before claiming done.
- **If a rule genuinely doesn't apply** (e.g., a pure transform with no error case for "Errors are loud and located"), say so explicitly in the task report — don't silently skip.
- **The checklist is the floor, not the ceiling.** Pass it, then keep going if more rigor is warranted by the change.
- **For deeper analysis** of an existing codebase, run the full analyzers: `/harness:reasoning-gaps` and `/harness:feedback-blockers`. They spawn parallel specialist subagents and produce ranked remediation plans. This checklist is the lightweight write-time shadow.
