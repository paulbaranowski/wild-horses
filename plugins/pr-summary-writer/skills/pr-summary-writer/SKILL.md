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
  description. Stop. The rest of this skill does not apply.
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

4. **Data / contract model** - only when a schema or contract changed: the one
   or two field-level semantics a reviewer must hold in their head. Not the
   full schema.

5. **Testing** - one paragraph: the approach (what's driven end-to-end vs.
   stubbed, and why) and the top-line result.

6. **Sequence / follow-ups** - when part of a series: one line on where this
   sits and what's deferred.

7. Footer per the target repo's convention (often none). Never add
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
6. **Draft in prose, architecture section first.** The one-idea sentence,
   then before/after, then decisions, then what-didn't-change.
7. **Ruthlessly demote detail.** If removing a line loses no _understanding_,
   remove it; the commits and code already carry it.
8. **One-pass read.** If a reviewer can't get the mental model in a single
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

1. Check for an open PR on the current branch first:
   `gh pr view --json number,title,url,body` (non-zero exit means none).
2. Generate the title alongside the body: concise, intent-and-impact,
   conventional style when the repo uses it (`refactor(events): ...`).
3. If a PR exists, offer to update it directly; on confirmation:

   ```bash
   gh pr edit <number> --title "<title>" --body "$(cat <<'EOF'
   <body>
   EOF
   )"
   ```

   The single-quoted `EOF` means zero shell expansion: backticks, `$`, and
   quotes must appear raw. Escaping anything inside the heredoc corrupts the
   markdown on GitHub.

4. If no PR exists, hand the title and body to whatever opens the PR (or
   print them for copy/paste).
