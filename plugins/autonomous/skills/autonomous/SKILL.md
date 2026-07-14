---
name: autonomous
description: >-
  Autonomously take an issue/ticket or a plan file from a link (or path) to an opened pull request, with no human in the loop. Hand it a Linear/GitHub/other issue URL, a path to a plan/spec file (e.g. ~/plans/repo/foo.md), or a plan already read into the conversation, and it decides everything itself: implements, tests, simplifies the diff, runs a bounded reasoning-gaps review (critical findings only, via harness agent prompts), reviews via pr:review (falling back to core:cb-review, then an independent sub-agent) to convergence, and opens a PR following the target repo's own conventions. Use when the user says work this issue autonomously, take this ticket end-to-end, do this AFK, pastes an issue link, or points it at a plan file and asks you to just build it. Ships an autonomy contract (never stop to ask) plus a 10-rule code-style bar.
user-invocable: true
disable-model-invocation: false
argument-hint: "<issue/ticket URL or path to a plan file>"
---

# autonomous

Take a task — an issue/ticket **link** or a **plan/spec file** — all the way to an
opened pull request, with no human in the loop. Resolve the task from the link or
file, then run an implement → test → review → PR → tend loop entirely on your own
judgment.

## Input resolution (the task source)

Resolve the task target in this priority order:

1. **URL in the arguments** — detect the host and fetch the issue title + body:
   - GitHub issue/PR → `gh issue view <url>` / `gh pr view <url>`
   - Linear → the `linear` CLI if present, otherwise WebFetch
   - anything else → WebFetch
2. **File path in the arguments** — a path to a plan, spec, or issue file (e.g.
   `~/plans/<repo>/foo.md` or any local `.md`). Read it with the `Read` tool; its
   full content is the Task. There is no separate "title" to fetch — the file _is_
   the spec.
3. **A task already in the conversation** — an issue ref/link, or a plan/spec
   already read into context (for example, handed off by `plan-keeper:plan-do`,
   which reads the plan file and then invokes this skill). Use that content as the
   Task; do not re-fetch or re-read it.
4. **Nothing resolvable** — this is the one allowed stop. It is a _precondition_
   failure, not a mid-task clarification: state plainly that the skill needs an
   issue link or a plan file, and stop. Once a task is resolved and work begins,
   the "never ask" contract below governs everything.

The fetched issue (title + body) or the plan/spec file's content becomes the
**Task** you work from (see the **Task** section below).

## Autonomy

Make every design and implementation decision yourself. Do not stop to ask
clarifying questions — assume no human is watching this session.

**No exceptions:**

- **Don't ask** even for "fundamental" or "architectural" decisions
- **Don't ask** even for "I just need one quick clarification"
- **Don't ask** even when the issue description is empty or incomplete — proceed from the title
- **Don't ask** "in the spirit of being efficient"

**Violating the letter of this rule is violating the spirit of this rule.**

When the issue is ambiguous:

1. Pick the simplest interpretation consistent with the issue and the codebase's existing patterns.
2. Proceed and finish the work.
3. Record the choice (and the alternatives you considered) in the PR description under a "Decisions" section, so the reviewer can push back if you guessed wrong.

The single exception is a **precondition, not a clarification**: if you were
invoked with no resolvable task target — no issue link or plan-file path in the
arguments, no plan/issue in the conversation, and no clear in-progress work on the
current branch — stop and say so. That is the only question you may ask, and only
before work begins.

## Code style

1. **Type every boundary.** Public functions, exported APIs, and module interfaces get explicit parameter and return types. No `any` / `Any`. No raw `list` / `dict` / `Array` without element types. Closed sets of values are enums or literal-union types, never raw strings.
2. **Model structured data as a type.** If data has a known shape, define it (TypedDict, dataclass, Pydantic model, interface, type alias). Do not pass shapes around as `dict` / object literals and access them by string key.
3. **Validate at the edge; trust the interior.** Input crossing a system boundary (HTTP, CLI, file, queue) is parsed into a typed model at the boundary. Code past the boundary trusts the type and does not re-validate.
4. **Make control flow explicit.** No decorators or middleware that silently change semantics (retry, cache, auth, transactions) — if it changes behavior, it is visible at the call site or named in the function. No dynamic dispatch via string keys / `getattr` without a typed registry. No import-time side effects: modules declare; they do not register, mutate globals, or do I/O at load.
5. **No hidden mutation.** Methods that read or compute (`get_*`, `to_*`, `validate_*`, `is_*`) do not mutate state. Accessors do not return mutable internals — return a copy or a read-only view. Mutation has a verb in its name.
6. **Inject collaborators.** Anything that touches the outside world — database, HTTP client, clock, RNG, environment, filesystem — is passed in as a parameter, not constructed inline. `Date.now()`, `Math.random()`, `os.environ`, and `new SomeService()` appear only at composition roots (entrypoints, factories).
7. **Small, single-purpose, shallow.** Target ≤ 50 lines per function and ≤ 4 levels of nesting. If a function does N steps in sequence, name and extract each step. Prefer early returns over deep `if` pyramids. One class, one responsibility — if its name needs an `and`, split it.
8. **Fail loud, fail with context.** No bare `except:` / `catch (e) {}` that swallows. Errors carry the field, value, and operation that failed. Re-raise with `cause` / `from` so the stack survives. If recovery is intentional, comment why.
9. **Document the _why_, not the _what_.** Every module gets a 1–3 line header describing its role in the system. Comments only on non-obvious things — magic numbers, business rules, workarounds, performance trade-offs. Never restate what the code says.
10. **Red, green, refactor.** Write a failing unit test that asserts observable behavior. Make it pass with the simplest code. Then refactor with the test as a safety net. Tests assert behavior, not implementation — never assert on mocks, private fields, or call counts unless the call itself is the contract.

## Workflow

Invoke the `superpowers:using-superpowers` skill before you do anything else — it
is the entry point to a suite (brainstorming, writing-plans,
test-driven-development, systematic-debugging, subagent-driven-development,
verification-before-completion, requesting-code-review,
finishing-a-development-branch) that you should apply throughout this task. The
numbered steps below describe the destination; superpowers describes the
discipline that gets you there.

1. Implement the change.
2. Run the project's tests. If any fail, fix them before continuing.
   Pre-existing failures, "unrelated" failures, and flaky failures all count —
   diagnose the root cause and either fix or document in the Decisions section.
   Never skip a test, never disable it (e.g. `it.skip`), and never rely on CI to
   catch what should pass locally.
3. Simplify the diff. Run the three-lens review bundled at `references/simplify.md`
   (code reuse, code quality, efficiency). If the host supports sub-agents, launch
   the three concurrently, handing each the full diff; otherwise perform them
   inline. Aggregate their findings and fix each one directly - this is not a gate
   and has no stop point. If a fix changes behavior, re-run step 2's tests before
   continuing.
4. Reasoning-gaps review. Run the bounded pass at `references/reasoning-gaps-review.md`
   on changed source files only. It reuses the harness plugin's three specialist
   agent prompts (types, implicit flow, structure) but triages to **critical
   findings only**, plus important findings when (cross-dimension or on a
   public API) **and** the fix is a small local type/doc change. Fix every must-fix
   item; defer the rest to the PR Decisions section. Skip entirely if the harness
   plugin is unavailable. Re-run step 2's tests when a fix changes behavior.
5. Commit your work, then get an independent code review of the committed diff
   before opening the PR. Prefer the in-marketplace `pr:review` skill (wild-horses
   `pr` plugin), fall back to `core:cb-review`, and use an ad-hoc sub-agent only if
   neither review skill is available this session. Commit first because these review
   skills review the committed diff against the base branch, never the working tree -
   an uncommitted change reads to them as an empty diff and the review silently
   no-ops. Put the Task (the issue/plan) in the commit message so the review has spec
   context to check against.
   - **Primary - `pr:review` (wild-horses):** read and execute the `pr` plugin's
     `skills/review/SKILL.md` in `--report --effort high` mode on the committed diff
     (locate it in the plugin cache or marketplace checkout, the same way `/pr`
     composes its dependency skills). It derives its own spec context from the branch
     (the commit messages, and the PR body once one exists); do **not** feed it your
     reasoning or this conversation - the value is in independent judgment. `--report`
     mode is non-interactive and returns its findings with no gates.
   - **Fallback - `core:cb-review`:** if `pr:review` is unavailable this session,
     invoke `core:cb-review --effort high --report` on the committed diff the same way.
   - **Last resort - ad-hoc sub-agent** (only if neither review skill is available
     this session): spawn a sub-agent to review your changes. Hand it the diff plus the
     issue description, but not your reasoning or this conversation - the value is
     in independent judgment. Ask it to flag bugs, regressions, missing test
     coverage, security issues, and convention violations.

   Either way, triage every finding yourself: fix the real, in-scope ones; dismiss
   out-of-scope or false-positive ones, recording each dismissal with a one-line
   reason in the PR's Decisions section. Then re-run steps 2 → 3 → 4 → 5 on the updated
   diff; iterate until tests pass and the review surfaces no remaining substantive
   findings. "Same findings as last iteration" is **not** convergence - it means
   your fixes were incomplete; fix harder. Document any disagreement with a
   specific finding in the PR's Decisions section.

6. Open a pull request. Follow the target repo's own PR conventions — read its
   CLAUDE.md / AGENTS.md / CONTRIBUTING and recent `git log` for the title and
   description format. Link back to the source issue URL in the PR description,
   and include a "Decisions" section recording any ambiguous calls and the
   alternatives considered. **Don't** append a "Generated with Claude Code"
   footer and **don't** add any "Co-Authored-By: Claude" trailer.
7. Tend the PR with `pr:pr-babysit`: invoke it on the PR you just opened to
   snapshot CI, auto-fix high-confidence failures, and reply to review threads.
   Loop this 5 times - after each run, push any fixes back through steps 2–5,
   wait for review and CI to settle, then re-invoke `pr:pr-babysit` (stop early
   once CI is green and the review threads are addressed). You own this outer
   loop: on a `progressing` exit pr-babysit may advise re-running it or wrapping
   it in `/loop` - ignore that and start the next round yourself. If
   `pr:pr-babysit` is not available in this session, tend the PR manually
   instead: address CI failures and review comments over the same 5 rounds,
   then stop.
8. Stop. The human review loop happens out-of-session — **don't** keep polling
   the PR and **don't** refresh CI by hand.

## Task

The task is whatever you resolved in **Input resolution** above — an issue/ticket
or a plan/spec file. Treat that content as the authoritative spec for what to
build:

- **From an issue/ticket:** work from its title and body. The title alone is
  enough to proceed when the body is thin or empty.
- **From a plan/spec file (or an in-context plan):** the file's full content is
  the spec — phases, tasks, acceptance criteria, and any design notes it carries.
  A plan is usually richer than an issue body; follow it, but you still own every
  decision it leaves open.

Either way, do not stop to ask for more detail (see **Autonomy**). Record any
ambiguous calls in the PR's "Decisions" section.
