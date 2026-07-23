---
name: summary-writer
description: Write pull-request descriptions and titles that lead with the one structural idea, not a file-by-file changelog. Use when about to write or revise a PR description or title (before `gh pr create`/`gh pr edit`, or when a repo squashes with the PR body as the commit message), or when an existing description reads like a changelog and needs rewriting.
user-invocable: true
disable-model-invocation: false
argument-hint: "[PR number or branch, optional]"
---

# summary-writer

Write PR descriptions that lead with the _shape_ of the change, not a catalog of
edits. The reviewer should understand _what changed structurally and why_ before
ever opening the diff. Granular per-file detail lives in the commits and the
code; the description exists to give the reader the mental model the diff
assumes.

**Core principle: one idea per PR, stated once, up front.** Every PR worth a
description has a single load-bearing structural idea. Find it, state it in
one sentence, and let everything else hang off it. If you can't state the one
idea, the PR is either trivial (short description, stop) or doing too much
(suggest splitting).

**State the idea directly; never announce it.** The opening sentence of the
architecture section is a claim about the system ("Event extraction is now
decoupled from persistence behind the EventSink seam"), not a preamble about
the description itself ("The core move is...", "The main change here is...",
"At a high level, this PR..."). Start talking about the change; delete the
sentence that says you're about to.

**The description describes the net diff vs. the base, not the branch's
history.** The reviewer sees `<base>...HEAD` on GitHub - one collapsed
change against main - and that is the only surface the description exists
to explain. Approaches that were tried and reverted inside the branch,
commits that superseded earlier commits, mid-PR pivots, and "originally
this did X, now it does Y" narration are all invisible in that diff and
must be invisible in the description too. The state before this PR is
main; the state after is HEAD; nothing in between belongs.

## Triage first

- **Trivial PR** (dependency bump, one-line fix, copy tweak): one-line
  description. If it changes an externally-consumed interface, add the
  Interface changes section after the one-liner; otherwise stop. Nothing
  else in this skill applies to a trivial PR.
- **Design PR** (new seam, refactor, new data flow, a decoupling, anything that
  introduces or shifts structure): full method below.

## The diagram rubric

Applies only within the Design PR bucket above - trivial PRs never reach
this decision. Decides whether the architecture section's diagram is a
mermaid before/after pair (or a single after-only diagram) instead of the
default: prose alone, or the tiny inline ASCII arrow for a linear case.

**Trigger a mermaid diagram when 2+ of these are true:**

- The change rewires a flow/dependency graph - components added, removed,
  or reconnected (a new seam, injected dependency, event bus introduced) -
  not just a renamed function or added parameter.
- Understanding the seam requires holding 4+ named entities and their
  relationships in your head at once (pipeline stages, service
  boundaries, state-machine states).
- Call/dispatch order changes in a way that's awkward to state as one
  sentence (sequential -> fan-out/fan-in, sync -> async, single-path ->
  conditional routing).
- The before shape and after shape are both genuinely non-trivial and
  structurally different - a picture of each clarifies faster than a
  paragraph of prose would.

**Skip it (any one of these blocks it, even if triggers above fire):**

- The change reads cleanly as one sentence with 2 entities ("X now reads
  from Y instead of Z") - prose already carries it.
- It's a linear 2-3 step relationship - the tiny inline ASCII arrow
  already covers this; a full diagram is overkill.
- It's a pure data/schema change with no flow/structural shift - that's
  the Data/contract model section's job, not a diagram.

**Diagram shape, not a skip condition:** if only one side has real
structure (brand-new subsystem, nothing to contrast), that doesn't skip
the diagram - it selects the shape. Draft a single "After:" diagram
instead of a before/after pair, since there's no meaningful "before"
shape to contrast against.

When it triggers, default to a `graph TD` (or `LR`) mermaid flowchart;
switch to `sequenceDiagram` specifically when the call/dispatch-order
signal is what fired. Scope each diagram to the entities that changed or
are load-bearing for the seam - not the whole system's structure. See
Diagram delivery below for embedding and file conventions.

## The description is a translation, not a transcript

Task lists, acceptance criteria, review logs, and per-file change logs are
**process artifacts**. They are input you read to _find_ the one idea and the
load-bearing decisions; the description then states those findings in prose.
An artifact's content may inform every sentence, yet the artifact itself never
appears.

## Section template (adapt names, omit empty sections, keep this order)

1. **What this is** - one short paragraph: what the PR enables plus the
   smallest framing needed to understand the rest (prior state, "slice 2 of N").

2. **Requirements** - what the change had to satisfy: the functional needs,
   constraints, and invariants that shaped the design, plus explicit
   non-goals. Short bullets are fine here; this is the problem statement the
   architecture answers, and it lets the reviewer check the design against
   what it was supposed to do.

3. **The architecture** - the heart; the only mandatory section for a design PR:
   - The one idea, stated directly as a fact about the system, in the opening
     sentence. ("Event extraction is now decoupled from persistence behind
     the EventSink seam.")
   - Before/after: what the old structure assumed or hard-wired, and why that
     blocked the goal. This is where the _why_ lives - a refactor only makes
     sense against the constraint it removes.
   - A diagram, when the diagram rubric triggers: a mermaid before/after
     pair (or a single after-only diagram) embedded as fenced `mermaid`
     code blocks - GitHub renders these natively. A tiny fenced ASCII
     arrow sketch remains fine for the linear case the rubric explicitly
     skips; never use ASCII for a case the rubric triggers on.
   - The load-bearing decisions (2-4, each with one sentence of rationale).
     Skip decisions with an obvious default.
   - What deliberately did NOT change, and how that safety is guaranteed
     (e.g. "covered by the existing X suite staying green"). The
     untouched-but-at-risk surface is often the most reassuring thing a
     reviewer can read.

4. **Interface changes** - only when the PR changes a surface an external
   consumer touches without reading the source: CLI commands/flags, HTTP/RPC
   endpoints, config file formats, UI screens. Internal library APIs
   (renamed functions, changed signatures) do not count - the diff shows
   those better than any example. One example per changed surface: a
   before/after pair for a modified surface, "after"-only for a brand-new
   one:

   Before:

   ```text
   $ plan-keeper list
   error: repo could not be derived
   ```

   After:

   ```text
   $ plan-keeper list --repo herds
   3 plans in ~/plans/herds/
   ```

   - Capture the "after" by actually running the command when that is cheap
     and side-effect-free; write the "before" from the old code's known
     behavior (never check out the base branch just to capture it). A
     brand-new surface gets an "after"-only usage example - no fabricated
     "before".
   - Trim output to only the lines that demonstrate the change.
   - Visual interfaces (web/mobile UI): one screenshot per changed screen is
     the baseline; a recording (GIF) only when the change is an interaction
     or flow a still image can't convey. Capture is best-effort with
     whatever tooling the session has; when none is available, skip the
     media and say so in your final message instead of blocking.
   - Media handoff: a PR body can only render media that GitHub hosts, and
     the drag-and-drop upload has no API. Save assets outside the worktree
     at `~/tmp/pr-assets/<repo>/<pr-number>/` (use the branch name until a
     PR number exists), put a visible italic placeholder naming the file in
     the body (`_[screenshot: settings-page.png - drag file here]_`), and
     list the absolute file paths in your final message so the user can
     drag them in.

5. **Data / contract model** - only when a schema or contract changed: the one
   or two field-level semantics a reviewer must hold in their head. Not the
   full schema.

6. **Testing** - one paragraph: the approach (what's driven end-to-end vs.
   stubbed, and why) and the top-line result.

7. **Sequence / follow-ups** - when part of a series: one line on where this
   sits and what's deferred.

8. Footer per the target repo's convention (often none). Never add
   "Generated with Claude Code" footers or Co-Authored-By trailers.

## The title

The title is part of the deliverable on every run, not just at PR creation:
revising a body always means re-deriving the title from the same one idea.
An existing title is input, never a default - keep it only when the
re-derived title would say the same thing.

- The one idea compressed to one line: intent and impact, not mechanics
  ("decouple event extraction from persistence", not "refactor
  event_algorithm.py").
- Conventional-commit style when the repo's history uses it (check
  `git log --oneline`); plain sentence case otherwise.
- Aim under ~70 characters so GitHub doesn't truncate it in lists.

## Method

1. **Find the one idea.** Read the net diff against the base -
   `git diff "$(git merge-base HEAD origin/main)"..HEAD` (substitute the
   repo's default branch if not `main`) - and ask: what single structural
   change makes all these edits necessary? That sentence is the spine.
   Read the diff, not `git log` on the branch: commit history exposes
   intra-branch churn (reverted commits, superseded approaches, mid-PR
   pivots) that the reviewer will never see and must not appear in the
   description.
2. **Recover the constraint.** What did the old code assume or hard-wire that
   the goal couldn't live with? That's your before/after.
3. **Apply the diagram rubric.** Using the before/after just recovered,
   check the diagram rubric: 2+ trigger signals and no true skip signal
   means draft mermaid source - a before/after pair, or a single
   after-only diagram when only one side has real structure; otherwise
   the before/after stays prose-only, or a tiny ASCII arrow for the
   linear case.
4. **Recover the requirements.** What did the change have to satisfy: needs,
   constraints, invariants, non-goals? Keep the ones a reviewer needs in
   order to judge whether the design answers them.
5. **Keep only the 2-4 decisions that shape the design.** Drop anything with
   an obvious default or that's a local implementation detail.
6. **Identify the at-risk untouched surface** and how it's protected.
7. **Detect changed external surfaces.** CLI, API, config, UI - one
   before/after example each, per the Interface changes section's rules;
   capture media only when the surface is visual.
8. **Draft in prose, architecture section first.** The one-idea sentence,
   then before/after, then decisions, then what-didn't-change.
9. **Compress the one idea into the title** per The title section - re-derive
   it every run; never carry an existing title forward unexamined.
10. **Ruthlessly demote detail.** If removing a line loses no _understanding_,
    remove it; the commits and code already carry it.
11. **One-pass read.** If a reviewer can't get the mental model in a single
    read, it's still too granular.

## Smell tests (revise if any are true)

- The first substantive section is a bulleted list of files or edits: lead
  with the idea instead.
- You can't state the idea in a single sentence: the PR is trivial or too big.
- The architecture section opens with a preamble ("The core move is...",
  "The main change is..."): delete it and open with the claim itself.
- No requirements are stated: the reviewer can't check the design against
  what it was supposed to satisfy.
- A reviewer would learn nothing they couldn't get faster from
  `git diff --stat`: it's a changelog, not a description.
- Acceptance-criteria checkboxes or a review-round log are present: cut them;
  they're process artifacts, not architecture.
- Every decision made is listed: keep only the load-bearing ones.
- No mention of what stayed the same: name the at-risk untouched surface.
- An Interface changes section exists but no externally-consumed surface
  changed: cut the section.
- The interface example is an exhaustive option matrix rather than the one
  representative invocation: keep the single pair that shows the change.
- The body was rewritten but the pre-existing title survived verbatim:
  re-derive the title from the one idea; keeping it is only right when the
  re-derived title matches.
- The description narrates the branch's own history - "this replaces this
  PR's original approach", "an earlier commit is reverted in-branch",
  "originally this did X, now it does Y", "the net diff below is only the
  new feature": cut every such phrase. Describe the state after HEAD as
  measured against main, with no reference to intermediate states the
  reviewer will not see.

## Don't

- **Don't organize any section around file paths** (per-file bullets, "new
  files" / "modified files" groupings). File-level detail belongs to the
  commits and `git diff --stat`.
- **Don't copy acceptance-criteria checkboxes into the description**, checked
  or unchecked. State what's verified inside the testing paragraph instead.
- **Don't include review or iteration logs** (rounds, findings-fixed counts,
  verdicts). They're process history, not the change.
- **Don't restate diff statistics** ("14 files changed, 6 new"). The PR page
  already shows them.
- **Don't pad with an inventory to look thorough.** Thoroughness is the
  mental model being complete, not the edit list being long.
- **Don't announce the idea before stating it** ("The core move is...",
  "The key change here is...", "At a high level..."). Open with the claim
  about the system itself.
- **Don't dump untrimmed command output** into an interface example. Keep
  only the lines that demonstrate the change.
- **Don't include more than one example per changed surface**: one
  representative before/after pair each, one screenshot or one recording
  per changed screen/flow.
- **Don't commit media assets to the repo.** Screenshots and recordings
  live outside the worktree (`~/tmp/pr-assets/<repo>/<pr-number>/`) and
  reach the PR body via the user's drag-and-drop.
- **Don't narrate the branch's own history.** No references to reverted
  commits, superseded approaches, mid-PR pivots, or "this replaces the
  original approach" / "an earlier commit is reverted in-branch" /
  "originally this did X, now it does Y" framing. The reviewer sees the
  net diff against the base and nothing else; describe only that. If a
  fact only makes sense as a contrast with an intermediate state that
  never leaves the branch, the fact does not belong in the description.

## Worked reference

From herds PR #260 ("text extraction via EventAlgorithmV4 + EventSink
refactor"), the shape a good description takes:

- The idea in one sentence, stated as a fact about the system: event
  _extraction_ is decoupled from event _persistence_ behind the `EventSink`
  seam.
- Before/after: the algorithm base class persisted inline via
  `add_event(image_id=...)`, hard-wiring "an event always comes from an
  image" - the exact assumption that blocked reusing extraction for URLs. Now
  it delegates to an injected sink and no longer knows the source.
- Three load-bearing decisions, one line of rationale each (lazy sink
  resolution preserves late-wiring; orchestrator owns URL persistence because
  cost/provenance belong there; extraction split from persistence so cost
  survives a save fault).
- What didn't change: the image pipeline, guaranteed by the existing image
  suite staying green.
- Demoted out of the description: per-file bullets, acceptance checkboxes,
  the full decisions ledger, CI-bot review verdicts.

Its first draft had led with "## In scope" and ~12 per-module bullets plus an
acceptance checklist - accurate, but the reviewer had to assemble the
architecture themselves. The rewrite led with the one idea.

## Diagram delivery

When the diagram rubric triggers, in addition to embedding the mermaid
block(s) inline in the body:

1. Save each diagram's source to
   `~/tmp/pr-assets/<repo>/<pr-number>/diagram-before.mmd` and
   `diagram-after.mmd` (or just `diagram.mmd` for the after-only case) -
   the branch name substitutes for `<pr-number>` until a PR number
   exists, matching the Interface changes media-handoff convention.
2. Attempt a best-effort PNG render of each `.mmd`, using the same
   absolute save directory for both the input and the output so the PNG
   lands next to its source rather than in the current working
   directory:

   ```bash
   npx -y @mermaid-js/mermaid-cli@11.16.0 \
     -i ~/tmp/pr-assets/<repo>/<pr-number>/diagram-before.mmd \
     -o ~/tmp/pr-assets/<repo>/<pr-number>/diagram-before.png
   ```

   Pin the CLI to a known version rather than the floating `latest` tag -
   unpinned `npx -y` executes whatever the package currently resolves to,
   which is a supply-chain risk if that release is ever compromised. Bump
   the pinned version here periodically.

   If node/npm isn't available, or the first-run Chromium download fails
   (no network, sandboxed session), skip the PNG, keep the `.mmd`, and
   tell the user in the final message that installing Node/npm (or
   running `mmdc` once to cache the download) would enable rendering
   next time.

3. List the saved `.mmd` (and `.png`, if rendered) absolute paths in the
   final message, alongside any screenshot paths - a portable copy for
   reuse outside GitHub (Slack, docs), even though the PR body itself
   already has the diagram inline and needs nothing dragged in.

## Delivery

1. Check for an open PR first. With no argument, use the current branch:
   `gh pr view --json number,title,url,body` (non-zero exit means none). When
   a PR number or branch was passed as the argument, target it instead:
   `gh pr view <arg> --json number,title,url,body`.
2. Generate the title alongside the body per The title section - re-derived
   from the one idea on every run, including when the PR already has a
   title.
3. If the existing body already contains GitHub-hosted media
   (`user-attachments` URLs), carry those links into the new body unchanged;
   placeholders are only for surfaces not yet illustrated. For any remaining
   placeholders, make sure the assets are already saved under
   `~/tmp/pr-assets/<repo>/<pr-number>/` (branch name until a PR number
   exists) before updating the PR, and after delivering, list each
   placeholder's absolute file path in your final message so the user can
   drag the files into the description.
4. If a PR exists, update it immediately via the REST API - do not ask for
   confirmation. `gh pr edit` calls a GraphQL mutation that requires
   `read:org` on the token; `repo`-scoped tokens (the common case for
   fine-grained PATs and CI tokens) will fail. REST PATCH works on any
   `repo`-scoped token.

   Capture the title and body into shell variables first, each via its own
   single-quoted heredoc - the quoted delimiter means zero shell expansion,
   so backticks, `$`, and quotes stay raw:

   ```bash
   title=$(cat <<'TITLE_EOF'
   <title>
   TITLE_EOF
   )
   body=$(cat <<'BODY_EOF'
   <body>
   BODY_EOF
   )

   gh api -X PATCH "repos/{owner}/{repo}/pulls/<number>" \
     -f title="$title" \
     -f body="$body" \
     --jq '{url: .html_url, title}'
   ```

   `{owner}` and `{repo}` are `gh api`'s own placeholder syntax - it fills
   them in from the current directory's git remote, the same auto-detection
   `gh pr edit` did implicitly. `.html_url` is the browsable PR page; the
   response's own `.url` field is the API endpoint, not something to hand
   to a person.

   After a successful update, confirm with the PR URL. If the PATCH fails
   (network, 404, permissions), show the error and fall back to printing the
   title and body for copy/paste.

5. If no PR exists, hand the title and body to whatever opens the PR (or
   print them for copy/paste).
