---
name: autonomous
description: >-
  Autonomously take an issue/ticket or a plan file from a link (or path) to an opened pull request, with no human in the loop. Hand it a Linear/GitHub/other issue URL, a path to a plan/spec file (e.g. ~/plans/repo/foo.md), or a plan already read into the conversation, and it decides everything itself: implements, tests, simplifies the diff, runs a bounded reasoning-gaps review (critical findings only, via harness agent prompts), reviews via wild-pr:review (falling back to an independent sub-agent) to convergence, and opens a PR following the target repo's own conventions. Use when the user says work this issue autonomously, take this ticket end-to-end, do this AFK, pastes an issue link, or points it at a plan file and asks you to just build it. Ships an autonomy contract (never stop to ask) plus a 10-rule code-style bar.
user-invocable: true
disable-model-invocation: false
argument-hint: "<issue/ticket URL or path to a plan file>"
---

# autonomous

Take a task all the way to an opened pull request, with no human in the loop.
The task is an issue/ticket **link** or a **plan/spec file**. Resolve the task
from the link or file. Then run an implement → test → review → PR → tend loop
entirely on your own judgment.

## Input resolution (the task source)

Resolve the task target in this priority order:

1. **URL in the arguments.** Detect the host and fetch the issue title and body.
   - GitHub issue/PR → `gh issue view <url>` / `gh pr view <url>`.
   - Linear → the `linear` CLI if present, otherwise WebFetch.
   - anything else → WebFetch.
2. **File path in the arguments.** This is a path to a plan, spec, or issue
   file. Examples: `~/plans/<repo>/foo.md`, or any local `.md`. Read it with the
   `Read` tool. Its full content is the Task. There is no separate "title" to
   fetch, because the file _is_ the spec.
3. **A task already in the conversation.** This is an issue ref or link, or a
   plan/spec already read into context. `plan-keeper:plan-do` hands off this
   way: it reads the plan file, then invokes this skill. Use that content as the
   Task. Do not re-fetch or re-read it.
4. **Nothing resolvable.** This is the one allowed stop. It is a _precondition_
   failure, not a mid-task clarification. State plainly that the skill needs an
   issue link or a plan file, then stop. Once a task is resolved and work begins,
   the "never ask" contract below governs everything.

The fetched issue (title and body) becomes the **Task** you work from. So does
the plan/spec file's content. See the **Task** section below.

## Autonomy

Make every design and implementation decision yourself. Do not stop to ask
clarifying questions. Assume no human is watching this session.

**No exceptions:**

- **Don't ask** even for "fundamental" or "architectural" decisions.
- **Don't ask** even for "I just need one quick clarification".
- **Don't ask** even when the issue description is empty or incomplete. Proceed
  from the title.
- **Don't ask** "in the spirit of being efficient".

**Violating the letter of this rule is violating the spirit of this rule.**

When the issue is ambiguous:

1. Pick the simplest interpretation consistent with the issue and the codebase's existing patterns.
2. Proceed and finish the work.
3. Record the choice, and the alternatives you considered, in the PR description under a "Decisions" section. The reviewer can then push back if you guessed wrong.

The single exception is a **precondition, not a clarification**. It applies when
you were invoked with no resolvable task target. That means the arguments hold
no issue link and no plan-file path. It also means no plan or issue sits in the
conversation. The current branch holds no clear in-progress work. In that case,
stop and say so. That is the only question you may ask, and only before work
begins.

## Code style

1. **Type every boundary.** Public functions, exported APIs, and module interfaces get explicit parameter and return types. No `any` / `Any`. No raw `list` / `dict` / `Array` without element types. Closed sets of values are enums or literal-union types, never raw strings.
2. **Model structured data as a type.** If data has a known shape, define it (TypedDict, dataclass, Pydantic model, interface, type alias). Do not pass shapes around as `dict` / object literals and access them by string key.
3. **Validate at the edge; trust the interior.** Input crossing a system boundary (HTTP, CLI, file, queue) is parsed into a typed model at the boundary. Code past the boundary trusts the type and does not re-validate.
4. **Make control flow explicit.** No decorators or middleware that silently change semantics (retry, cache, auth, transactions). If it changes behavior, it is visible at the call site or named in the function. No dynamic dispatch via string keys / `getattr` without a typed registry. No import-time side effects: modules declare; they do not register, mutate globals, or do I/O at load.
5. **No hidden mutation.** Methods that read or compute (`get_*`, `to_*`, `validate_*`, `is_*`) do not mutate state. Accessors do not return mutable internals. Return a copy or a read-only view. Mutation has a verb in its name.
6. **Inject collaborators.** Anything that touches the outside world is passed in as a parameter, not constructed inline. That covers the database, HTTP client, clock, RNG, environment, and filesystem. `Date.now()`, `Math.random()`, `os.environ`, and `new SomeService()` appear only at composition roots (entrypoints, factories).
7. **Small, single-purpose, shallow.** Target ≤ 50 lines per function and ≤ 4 levels of nesting. If a function does N steps in sequence, name and extract each step. Prefer early returns over deep `if` pyramids. One class, one responsibility. If its name needs an `and`, split it.
8. **Fail loud, fail with context.** No bare `except:` / `catch (e) {}` that swallows. Errors carry the field, value, and operation that failed. Re-raise with `cause` / `from` so the stack survives. If recovery is intentional, comment why.
9. **Document the _why_, not the _what_.** Every module gets a 1–3 line header describing its role in the system. Comment only on non-obvious things: magic numbers, business rules, workarounds, and performance trade-offs. Never restate what the code says.
10. **Red, green, refactor.** Write a failing unit test that asserts observable behavior. Make it pass with the simplest code. Then refactor with the test as a safety net. Tests assert behavior, not implementation. Never assert on mocks, private fields, or call counts, unless the call itself is the contract.

## Workflow

Invoke the `superpowers:using-superpowers` skill before you do anything else. It
is the entry point to a suite you should apply throughout this task. The suite
holds brainstorming, writing-plans, test-driven-development,
systematic-debugging, subagent-driven-development, verification-before-completion,
requesting-code-review, and finishing-a-development-branch. The numbered steps
below describe the destination. Superpowers describes the discipline that gets
you there.

1. Implement the change.
2. Run the project's tests. If any fail, fix them before continuing.
   Pre-existing failures, "unrelated" failures, and flaky failures all count.
   Diagnose the root cause. Then fix it, or document it in the Decisions
   section. Never skip a test, never disable it (e.g. `it.skip`), and never rely
   on CI to catch what should pass locally.
3. Simplify the diff. Run the three-lens review bundled at `references/simplify.md`
   (code reuse, code quality, efficiency). If the host supports sub-agents, launch
   the three concurrently, handing each the full diff; otherwise perform them
   inline. Aggregate their findings and fix each one directly - this is not a gate
   and has no stop point. If a fix changes behavior, re-run step 2's tests before
   continuing.
4. Reasoning-gaps review. Run the bounded pass at `references/reasoning-gaps-review.md`
   on changed source files only. It reuses the harness plugin's three specialist
   agent prompts: types, implicit flow, and structure. It triages to **critical
   findings only**. It also keeps important findings in one case. Such a finding
   must be cross-dimension or on a public API. The fix must **also** be a small
   local type/doc change. Fix every must-fix item; defer the rest to the PR
   Decisions section. Skip entirely if the harness plugin is unavailable. Re-run
   step 2's tests when a fix changes behavior.
5. Commit your work, then get an independent code review of the committed diff
   before opening the PR. Prefer the in-marketplace `wild-pr:review` skill (wild-horses
   `wild-pr` plugin), and use an ad-hoc sub-agent only if it is unavailable this session.
   Commit first, because `wild-pr:review` reviews the committed diff against the base
   branch, never the working tree. An uncommitted change reads to it as an empty diff,
   and the review silently no-ops. Put the Task (the issue/plan) in the commit message
   so the review has spec context to check against.
   - **Primary - `wild-pr:review` (wild-horses):** read and execute the `wild-pr`
     plugin's `skills/review/SKILL.md` on the committed diff, in
     `--report --effort high` mode. Locate that file in the plugin cache or
     marketplace checkout, the same way `/wild-pr` composes its dependency
     skills. It derives its own spec context from the branch: the commit
     messages, and the PR body once one exists. Do **not** feed it your reasoning
     or this conversation. The value is in independent judgment. `--report` mode
     is non-interactive, and it returns its findings with no gates.
   - **Fallback - ad-hoc sub-agent** (only if `wild-pr:review` is unavailable this
     session): spawn a sub-agent to review your changes. Hand it the diff plus the
     issue description. Do not hand it your reasoning or this conversation. The
     value is in independent judgment. Ask it to flag bugs, regressions, missing
     test coverage, security issues, and convention violations.

   Either way, triage every finding yourself. Fix the real, in-scope ones.
   Dismiss the out-of-scope and false-positive ones. Record each dismissal with a
   one-line reason in the PR's Decisions section. Then re-run steps 2 → 3 → 4 → 5
   on the updated diff. Iterate until tests pass. The review must also report no
   remaining substantive findings. "Same findings as last iteration" is **not**
   convergence - it means your fixes were incomplete; fix harder. Document any
   disagreement with a specific finding in the PR's Decisions section.

6. Open a pull request.

   Gather the body's inputs first, before anything drafts prose. Read the
   target repo's CLAUDE.md / AGENTS.md / CONTRIBUTING and recent `git log` for
   the title and description format. A repo convention beats this step when the
   two disagree. Collect the source issue URL, every ambiguous call with the
   alternatives you considered, and your step 5 dismissals.

   Then write the title and body with `wild-pr:summary-writer`, never straight
   from your own memory of the work. You built the change, so your own draft
   compresses against what you know, not what the reviewer knows.
   - **Primary - `wild-pr:summary-writer` (wild-horses):** read and execute the
     `wild-pr` plugin's `skills/summary-writer/SKILL.md`. Locate that file in
     the plugin cache or marketplace checkout, the same way step 5 locates
     `skills/review/SKILL.md`. It owns the section list, the claim ceilings,
     the diagram rubric, and the title. Hand it everything you gathered above.
     Its Decision review section is where the dismissals and disagreements
     belong. Use both the title and the body it produces.
   - **Fallback** (only if `wild-pr:summary-writer` is unavailable this
     session): write the title and body yourself. Lead the body with the one
     structural idea, not a file-by-file changelog. State that idea as a fact
     about the system in the first sentence. Compress the same idea into the
     title. Keep a Requirements section. Record the same step 5 dismissals.
     Leave out file inventories, acceptance-criteria checkboxes, and review
     logs.

   `summary-writer` checks for an open PR before it delivers. When one exists
   it updates that PR itself, and you are done. When none exists it stops at a
   title and body, and you then run `gh pr create` with them. Never run
   `gh pr create` without checking, because a create against an existing PR
   fails after the body was already updated. Step 7 needs the PR either way.

   **Don't** append a "Generated with Claude Code" footer and **don't** add any
   "Co-Authored-By: Claude" trailer.

7. Tend the PR with `wild-pr:babysit`. Invoke it on the PR you just opened. It
   snapshots CI, auto-fixes high-confidence failures, and replies to review
   threads. Loop this 5 times. After each run, push any fixes back through
   steps 2–5. Wait for review and CI to settle, then re-invoke `wild-pr:babysit`.
   Stop early once CI is green and the review threads are addressed. You own this
   outer loop. On a `progressing` exit, babysit may advise re-running it, or
   wrapping it in `/loop`. Ignore that advice and start the next round yourself.
   If `wild-pr:babysit` is not available in this session, tend the PR manually
   instead. Address CI failures and review comments over the same 5 rounds, then
   stop.
8. Stop. The human review loop happens out-of-session. **Don't** keep polling
   the PR, and **don't** refresh CI by hand.

## Task

The task is whatever you resolved in **Input resolution** above: an issue/ticket,
or a plan/spec file. Treat that content as the authoritative spec for what to
build:

- **From an issue/ticket:** work from its title and body. The title alone is
  enough to proceed when the body is thin or empty.
- **From a plan/spec file (or an in-context plan):** the file's full content is
  the spec. That covers phases, tasks, acceptance criteria, and any design notes
  it carries. A plan is usually richer than an issue body. Follow it, but you
  still own every decision it leaves open.

Either way, do not stop to ask for more detail (see **Autonomy**). Record any
ambiguous calls in the PR's "Decisions" section.
