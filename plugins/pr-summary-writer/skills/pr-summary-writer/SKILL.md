---
name: pr-summary-writer
description: Use when about to write or revise a pull-request description or title - before running `gh pr create` or `gh pr edit`, when asked to write or update a PR body, or when a repo squashes with the PR body as the commit message. Also use when an existing description reads like a changelog (file-by-file bullets, acceptance-criteria checkboxes, review-round logs) and needs rewriting.
user-invocable: true
disable-model-invocation: false
argument-hint: "[PR number or branch, optional]"
---

# pr-summary-writer

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

## Triage first

- **Trivial PR** (dependency bump, one-line fix, copy tweak): one-line
  description. If it changes an externally-consumed interface, add the
  Interface changes section (template section 4) after the one-liner;
  otherwise stop. Nothing else in this skill applies to a trivial PR.
- **Design PR** (new seam, refactor, new data flow, a decoupling, anything that
  introduces or shifts structure): full method below.

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
   - A small fenced ASCII diagram, only if it clarifies the seam/flow/dispatch
     better than prose.
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
   those better than any example. One before/after example per changed
   surface:

   ```text
   # before
   $ plan-keeper list
   error: repo could not be derived

   # after
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
     media and say so in the handoff message instead of blocking.
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

## Method

1. **Find the one idea.** Skim the diff and ask: what single structural change
   makes all these edits necessary? That sentence is the spine.
2. **Recover the constraint.** What did the old code assume or hard-wire that
   the goal couldn't live with? That's your before/after.
3. **Recover the requirements.** What did the change have to satisfy: needs,
   constraints, invariants, non-goals? Keep the ones a reviewer needs in
   order to judge whether the design answers them.
4. **Keep only the 2-4 decisions that shape the design.** Drop anything with
   an obvious default or that's a local implementation detail.
5. **Identify the at-risk untouched surface** and how it's protected.
6. **Detect changed external surfaces.** CLI, API, config, UI - one
   before/after example each, per the Interface changes section's rules;
   capture media only when the surface is visual.
7. **Draft in prose, architecture section first.** The one-idea sentence,
   then before/after, then decisions, then what-didn't-change.
8. **Ruthlessly demote detail.** If removing a line loses no _understanding_,
   remove it; the commits and code already carry it.
9. **One-pass read.** If a reviewer can't get the mental model in a single
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

## Delivery

1. Check for an open PR first. With no argument, use the current branch:
   `gh pr view --json number,title,url,body` (non-zero exit means none). When
   a PR number or branch was passed as the argument, target it instead:
   `gh pr view <arg> --json number,title,url,body`.
2. Generate the title alongside the body: concise, intent-and-impact,
   conventional style when the repo uses it (`refactor(events): ...`).
3. If the body contains media placeholders, make sure the assets are already
   saved under `~/tmp/pr-assets/<repo>/<pr-number>/` before updating the PR,
   and after delivering, list each placeholder's absolute file path in your
   final message so the user can drag the files into the description.
4. If a PR exists, update it immediately — do not ask for confirmation:

   ```bash
   gh pr edit <number> --title "<title>" --body "$(cat <<'EOF'
   <body>
   EOF
   )"
   ```

   The single-quoted `EOF` means zero shell expansion: backticks, `$`, and
   quotes must appear raw. Escaping anything inside the heredoc corrupts the
   markdown on GitHub.

   After a successful update, confirm with the PR URL. If the update fails,
   show the error and fall back to printing the title and body for
   copy/paste.

5. If no PR exists, hand the title and body to whatever opens the PR (or
   print them for copy/paste).
