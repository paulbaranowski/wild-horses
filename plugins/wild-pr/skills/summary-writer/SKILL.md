---
name: summary-writer
description: Write pull-request descriptions and titles that lead with the one structural idea, not a file-by-file changelog. Use when about to write or revise a PR description or title (before `gh pr create`/`gh pr edit`, or when a repo squashes with the PR body as the commit message), or when an existing description reads like a changelog and needs rewriting.
user-invocable: true
disable-model-invocation: false
argument-hint: "[PR number or branch, optional]"
---

# summary-writer

Write PR descriptions that lead with what changed structurally, not a catalog of
edits. The reviewer should understand what changed and why before opening the
diff. Per-file detail lives in the commits and in the code. The description
gives the reader the mental model that the diff assumes.

**Core principle: one idea per PR, stated once, up front.** Every PR worth a
description has one main structural idea. Find it, then state it in one
sentence. Let every other sentence follow from it. If you cannot state the one
idea, the PR is either trivial or too big. A trivial PR gets a short
description. A PR that is too big should be split.

**State the idea directly. Never announce it.** The first sentence of the
architecture section makes a claim about the system. It does not announce that a
claim is coming. Write "Event extraction no longer saves events". Do not write
"The core move is...", "The main change here is...", or "At a high level, this
PR...". Start talking about the change. Delete the sentence that says you are
about to.

**Describe the net diff against the base, not the branch's history.** The
reviewer sees `<base>...HEAD` on GitHub. That one collapsed change is the only
thing the description exists to explain. The branch may contain approaches you
tried and reverted, commits that replaced earlier commits, and pivots you made
partway through. None of that appears in the diff, so none of it appears in the
description. The state before this PR is main. The state after is HEAD. Nothing
in between belongs.

## Calling this skill

Another skill invoking `summary-writer` needs only this section. Never restate
these rules in the caller. Point at this section instead.

**Supply:** the branch or PR to describe, and the source issue or plan URL.
Also every ambiguous call with the alternatives you considered, and any review
dismissals. The last two belong in Decision review. That is this skill's name
for the section a caller may know as "Decisions".

**Receive:** a title and a body. This skill re-derives the title on every run,
so an existing title is input, never a default. Use both.

**Delivery is conditional.** Delivery step 1 checks for an open PR. When one
exists, this skill updates it by REST PATCH and the caller is done. When none
exists, this skill returns the title and body and stops.

**This skill never opens a pull request.** The caller runs `gh pr create` when
no PR exists yet.

**This skill never edits source files and never commits.** It reports any
rationale it cut, so the caller can decide where that belongs.

## Plain language

Write the description in plain language, using four rules adapted from
ASD-STE100 Simplified Technical English. Aerospace and defense groups created
that standard to keep technical text easy to read. The standard caps procedural
sentences at 20 words and descriptive sentences at 25. This skill uses 20
everywhere.

- **Keep each sentence to 20 words or fewer.** Meet the limit by splitting one
  sentence into two. Never meet it by deleting a word the reader then has to
  rebuild. "Only the pair is not" is missing _atomic_, and that is not concision.
- **Use active voice.** Write "the test reads the catalog", not "the catalog is
  read by the test". Passive is fine when the actor is unknown or irrelevant.
- **Use one word for one idea.** Choose a word, then use it for that idea only.
  Do not use two words for the same thing.
- **Give each paragraph one topic.** A new topic starts a new paragraph.

**Technical names are allowed.** Real identifiers and real domain terms stay:
`EventSink`, `alembic_version`, `pg_roles`, migration, schema, index, RLS. The
standard permits Technical Names like these rather than forcing a paraphrase.
Define one on first use when it exists only inside this branch.

**Never use a figure of speech where a real name exists.** Look the name up and
write it. These are banned when they stand in for a name: seam, spine, surface,
half, blast radius. So is load-bearing, and one part hanging off another.

The ban is on the use, not the token. "Attack surface" and "half the rows" are
literal, and they stay.

Figure of speech: "The guard now covers the whole surface."
Real name: "The test now checks every table in `public`."

**Never use a phrase that sounds decisive and says nothing.** "And that is not
cheap here" names no cost. Name the cost, or cut the sentence.

## Triage first

- **Trivial PR** (dependency bump, one-line fix, copy tweak): one-line
  description. Plain language still applies. If it changes an interface an
  external consumer uses, add the Interface changes section after the one-liner.
  That section's capture rules still bind. Never write a transcript you did not
  run. If it changes no such interface, stop. Nothing else in this skill applies
  to a trivial PR.
- **Design PR**: use the full method below. This covers a new boundary, a
  refactor, a new data flow, a decoupling, or any change that shifts structure.

## The diagram rubric

This applies only to a design PR. A trivial PR never reaches this decision. The
rubric decides whether the architecture section gets a mermaid diagram. The
default is prose alone, or a tiny inline ASCII arrow for a linear case.

**Draw a mermaid diagram when 2 or more of these are true:**

- The change rewires a flow or dependency graph. Components are added, removed,
  or reconnected: a new boundary, an injected dependency, a new event bus. A
  renamed function or an added parameter does not count.
- The change needs 4 or more named entities held in mind at once. Pipeline
  stages, service boundaries, and state-machine states all count.
- Call order or dispatch order changes in a way that one sentence states badly.
  Examples: sequential to fan-out, sync to async, one path to conditional
  routing.
- Both the old structure and the new structure are complex and different. A
  picture of each is faster to read than a paragraph.

**Skip the diagram when any one of these is true, even if the triggers fire:**

- The change reads cleanly as one sentence with 2 entities. "X now reads from Y
  instead of Z" already carries it.
- The relationship is linear and 2 or 3 steps long. The tiny inline ASCII arrow
  covers that. A full diagram is too much.
- The change is data or schema only, with no shift in structure or flow. That
  belongs to the Data and contract model section.

**One side with no structure selects the shape. It does not skip the diagram.**
A brand-new subsystem has no meaningful "before" to contrast against. Draw a
single "After:" diagram instead of a before-and-after pair.

Default to a `graph TD` or `graph LR` mermaid flowchart. Switch to
`sequenceDiagram` when the call-order signal is the one that fired. Scope each
diagram to the entities that changed or that the reader needs. Do not draw the
whole system. See Diagram delivery below for file conventions.

## The description is a translation, not a transcript

Task lists, acceptance criteria, review logs, and per-file change logs are
**process artifacts**. You read them to find the one idea and the decisions that
shaped it. The description then states those findings in prose. An artifact may
inform every sentence. The artifact itself never appears.

## Write for the reader's context, not your own

You built the change. The reader did not. A description written straight out of
deep immersion compresses against what you know, not what the reader knows.
Every rule below repairs a symptom of that one defect.

- **Write for a reader who has seen neither the plan nor the diff.** They know
  the codebase. They do not know this branch. Introduce the state before the
  change. Define any term that exists only inside this work. Examples: a flag
  name, a route, or a phrase like "cut over cold". Never rebut an objection no
  reader raised. Arguing with the plan document you just read is the clearest
  sign that you are writing from your own context.

- **Lead every paragraph with the consequence. Put the mechanism after.** If the
  cost or the benefit sits in the closing clause, invert the sentence. The
  reason the paragraph exists goes first.

  Buried: "The counter increments before the scan resolves, so a failed scan
  produces an overcount."

  Led: "A failed scan still costs the user one of their five scans. The counter
  increments before the scan resolves, so a failure is charged, not refunded."

- **Quantify anything the reader would otherwise guess at.** A claim may rest on
  a limit, a count, a threshold, or a price. Grep for the constant and put the
  number in the sentence. "An overcount" is abstract and forgettable. "20% of
  everything a free user will ever get" is not. If you cannot find the number,
  say the claim is unquantified.

- **A flag must carry its content.** Calling something important obliges you to
  say what it is. "This needs sign-off, not review" is an assertion on its own.
  Say what the reader would be accepting, and what it costs if the choice is
  wrong.

## Section template (adapt names, omit empty sections, keep this order)

Sections 1 through 4 are required for a design PR. Sections 5 through 9 appear
only when they have content. Every section has a limit; see How much to write.

1. **What this is** - what the PR enables. Add the smallest framing needed to
   understand the rest, such as the prior state or "slice 2 of N". The PR may
   turn on a domain noun that a reader outside this subsystem would not
   recognize. It could be a table, an aggregate, a subsystem, or an internal
   concept. Say what it is in one plain sentence before anything else. A
   teammate has never touched this area, and reads only your first paragraph.
   Can they say what the thing is and why it exists? A reader who cannot name
   the subject cannot judge the architecture.

2. **What this changes for the user** - name the audience, then state both sides
   of the tradeoff. The audience is whoever depends on the changed code without
   reading it. It is the product's end user when the change reaches
   them. It is the developer working in this code when it does not. A refactor's
   user is the developer. Every design PR has an audience, so this section is
   never omitted. The audience shifts. It never disappears.
   - Say what got better for that audience, and what got worse, slower, or more
     expensive. A section carrying only upside is a pitch, not a tradeoff.
   - Say it in feature terms, not mechanism. Write "a failed scan still costs a
     free user one of their five". Do not write "the counter increments before
     the scan resolves". The mechanism belongs to the architecture section.
   - Put the numbers in wherever a number exists.

3. **Requirements** - what the change had to satisfy. List the functional needs,
   the constraints, the invariants that shaped the design, and the explicit
   non-goals. Short bullets work well here. This is the problem statement that
   the architecture answers. It lets the reviewer check the design against what
   it was supposed to do.

4. **The architecture** - the most important section. Every other section
   depends on it.
   - The one idea, stated as a fact about the system, in the first sentence.
     ("Event extraction now hands its events to an injected `EventSink` instead
     of saving them.")
   - Before and after: what the old structure assumed or hard-wired, and why
     that blocked the goal. The _why_ lives here. A refactor only makes sense
     against the constraint it removes.
   - A diagram, when the diagram rubric fires. Embed a mermaid before-and-after
     pair, or a single after-only diagram, as fenced `mermaid` code blocks.
     GitHub renders these natively. A tiny fenced ASCII arrow is still fine for
     the linear case that the rubric skips. Never use ASCII for a case the
     rubric fires on.
   - The decisions that shaped the design, each with its rationale. Skip any
     decision with an obvious default. To route one, ask: could a competent
     reviewer plausibly say _no_ to it, and would that no change the diff? If
     yes, it belongs in Decision review. If the answer is only "here is why this
     shape", it stays here.
   - What deliberately did not change, and how that safety is guaranteed. For
     example: "covered by the existing X suite staying green". Untouched code
     that the change puts at risk is often the most reassuring thing a reviewer
     can read.

5. **Decision review** - the things a reviewer could reject, as distinct from
   the things they only need to understand. Omit it when there is genuinely
   nothing. Two kinds belong here:
   - **Open questions** the change did not settle. Give the question, the
     options, which one the diff implements, and what evidence would change the
     answer.
   - **Settled choices the reviewer accepts by merging.** These are
     consequential, hard to reverse, and not obviously right. State what merging
     accepts, and what it costs if the choice is wrong.

   Route every decision with the test stated in the architecture section above.
   Do not restate that test here. A reviewer asked to rule on everything rules
   on nothing.

6. **Interface changes** - only when the PR changes an interface that an
   external consumer uses without reading the source. That means CLI commands
   and flags, HTTP and RPC endpoints, config file formats, and UI screens.
   Internal library APIs do not count. A renamed function or a changed signature
   shows better in the diff than in any example. Give one example per changed
   interface. Use a before-and-after pair for a modified interface, and an
   after-only example for a brand-new one:

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

   - Capture the "after" by actually running the command. You may not be able to
     run it. Perhaps it is not cheap, not side-effect-free, or it needs
     credentials you lack. Then omit the transcript and describe the change in
     prose. Never write output you did not observe. The reviewer cannot tell
     invented output from real output. Invented output is wrong exactly when
     the interface is unfamiliar enough to need an example. Write the
     "before" from the old code's known behavior. Never check out the base
     branch just to capture it.
     A brand-new interface gets an after-only example and no fabricated
     "before".
   - Trim the output to only the lines that show the change.
   - Visual interfaces (web or mobile UI): one screenshot per changed screen is
     the baseline. Record a GIF only when the change is an interaction or a flow
     that a still image cannot convey. Capture is best-effort with whatever
     tooling the session has. When none is available, skip the media and say so
     in your final message rather than blocking.
   - Media handoff: a PR body can only render media that GitHub hosts, and the
     drag-and-drop upload has no API. Save assets outside the worktree at
     `~/tmp/pr-assets/<repo>/<pr-number>/`, using the branch name until a PR
     number exists. Put a visible italic placeholder in the body that names the
     file (`_[screenshot: settings-page.png - drag file here]_`). List the
     absolute paths in your final message so the user can drag them in.

7. **Data and contract model** - only when a schema or a contract changed. Give
   the one or two field-level meanings that a reviewer must hold in mind. Not
   the full schema.

8. **Testing** - two parts that answer different questions. The first is a
   report the reviewer reads. The second is a procedure they follow. Either part
   can be omitted on its own. Omit the whole section only when neither applies.

   - **Automated coverage** - what the suite now guarantees. Say what was added
     or changed, what is driven end-to-end and what is stubbed and why, and the
     top-line result. Report only a result you observed. Run the suite, or say
     you did not run it. A pass count nobody ran is the same defect as an
     invented transcript. The reviewer cannot tell either one from the real
     thing. Do not restate the architecture section's untouched-code guarantee.
     That bullet names which code is protected. This one names what the tests
     do.
   - **How to test manually** - the shortest path a reviewer can walk to see the
     change. Give preconditions first: branch, migration, feature flag, seed
     data, credentials. Then give the steps as real commands or a named UI path.
     Then give the pass condition: what they should see if it worked. Cover one
     happy path, plus at most one edge case when that edge is the actual risk.

   Rules for the manual part:

   - Write only steps you actually walked. A recipe rebuilt from the source
     reads exactly like a real one. It fails precisely when the interface is
     unfamiliar enough to need instructions.
   - A manual path may exist that you did not walk. Say so in one line. "Not
     exercised by hand; the path is Settings > Billing > Retry" is useful to a
     reviewer. An invented recipe is not, and silence is worse than either.
   - Link to an existing doc rather than inlining, when setup runs past about 5
     steps or is already written down.
   - Omit this part when nothing is human-drivable, such as an internal refactor
     or a type-only change. "Run the test suite" belongs to the automated part.

9. **Sequence and follow-ups** - when the PR is part of a series. Say where this
   one sits and what is deferred.

10. Add the footer that the target repo's convention calls for, which is often
    none. Never add a "Generated with Claude Code" footer. Never add a
    Co-Authored-By trailer.

## How much to write

Most sections have a claim ceiling, listed below. A claim is one statement the
reader has to accept on its own. That covers a fact about the change and a
judgement about it. Write one claim per sentence, so the sentence count is
usually the claim count.

A claim that runs to two sentences still counts once. So splitting a long
sentence never changes the count, and the 20-word rule can never force you to
delete information.

| Section                           | Limit                          |
| --------------------------------- | ------------------------------ |
| What this is                      | 5 claims                       |
| What this changes for the user    | 4 claims                       |
| Requirements                      | 3 to 6 bullets, 1 claim each   |
| Architecture: the one idea        | 1 claim                        |
| Architecture: before and after    | 4 claims                       |
| Architecture: the decisions       | 2 to 4 here, each 1 + 2 claims |
| Architecture: what did not change | 2 claims                       |
| Decision review                   | 1 to 3 items, 4 claims each    |
| Data and contract model           | 3 claims                       |
| Testing: automated coverage       | 4 claims                       |
| Sequence and follow-ups           | 2 claims                       |

Each decision gets 1 claim to state it, plus up to 2 claims of rationale. The
decision count covers only what stays in the architecture section. A decision
routed to Decision review counts against that section instead.

A Decision review item gets 4 claims. An open question needs all four: the
question, the options, and which one the diff implements. The fourth is the
evidence that would settle it.

Interface changes and the manual test part have no claim ceiling. Their limits
are structural: one example per changed interface, and one happy path plus at
most one edge case.

**Meet a limit by cutting the weakest whole claim. Never meet it by compressing
a sentence.** Compression is what produces prose the reader has to decode.

**Report what you cut.** When you drop rationale to meet a limit, name it in
your final message to the user. They can then decide whether it belongs in a
code comment. Never write that comment yourself. This skill does not edit source
files.

## The title

The title is part of the deliverable on every run, not only at PR creation.
Revising a body always means deriving the title again from the same one idea. An
existing title is input, never a default. Keep it only when the newly derived
title would say the same thing.

- Compress the one idea to one line: intent and impact, not mechanics. Write
  "decouple event extraction from persistence", not "refactor
  event_algorithm.py".
- Use conventional-commit style when the repo's history uses it. Check
  `git log --oneline`. Otherwise use plain sentence case.
- Aim for under about 70 characters so GitHub does not truncate it in lists.
- Plain language applies to the title too.

## Method

1. **Find the one idea.** Read the net diff against the base with
   `git diff "$(git merge-base HEAD origin/main)"..HEAD`. Substitute the repo's
   default branch if it is not `main`. Ask: what single structural change makes
   all these edits necessary? Read the diff, not `git log` on the branch. The
   commit history exposes churn inside the branch that the reviewer never sees.
2. **Recover the constraint.** What did the old code assume or hard-wire that
   the goal could not live with? That is your before and after.
3. **Apply the diagram rubric.** Use the before and after you just recovered. If
   2 or more triggers fire and no skip signal fires, draft mermaid source. Draw
   a before-and-after pair, or a single after-only diagram when only one side
   has structure. Otherwise keep the before and after in prose, or use a tiny
   ASCII arrow for the linear case.
4. **Recover the requirements.** What did the change have to satisfy: needs,
   constraints, invariants, non-goals? Keep the ones a reviewer needs to judge
   whether the design answers them.
5. **Name the audience and both sides of the tradeoff.** Who depends on the
   changed code without reading it: the product's end user, or the developer
   working in this code? Then say what got better for them, and what
   got worse, slower, or more expensive.
6. **Fetch the numbers.** Before drafting, grep for the limits, counts,
   thresholds, and prices behind every claim you are about to make. A constant
   one grep away that never reaches the description is the most
   decision-relevant thing the PR failed to ship.
7. **Keep only the decisions that shaped the design.** Take the count and the
   per-decision claim budget from How much to write. Drop anything with an obvious
   default or any local implementation detail.
8. **Collect what a reviewer could say no to.** Gather the open questions the
   change did not settle, plus any settled choice that is consequential. Say
   what accepting it costs if it turns out wrong.
9. **Identify the untouched code that the change puts at risk**, and say how it
   is protected.
10. **Detect changed external interfaces.** Check CLI, API, config, and UI. Give
    one example each, per the Interface changes rules. Capture media only when
    the interface is visual.
11. **Walk the manual path.** If a person can drive the change by hand, drive
    it. Record preconditions, steps, and the pass condition as
    you go. If you cannot, record that it is unverified. Never rebuild the steps
    from the code.
12. **Draft in prose, architecture section first,** using the Plain language
    rules. Write the one-idea sentence, then before and after, then the
    decisions, then what did not change.
13. **Derive the title** from the one idea, per The title section. Do this every
    run. Never carry an existing title forward unexamined.
14. **Demote detail without mercy.** If removing a line loses no _understanding_,
    remove it. The commits and the code already carry it.
15. **Check every section against How much to write.** Count the claims. Cut the
    weakest whole claim to fit. Never compress a sentence to fit.
16. **Check every sentence against Plain language.** Look for sentences over the
    word limit, passive voice, and a figure of speech standing in for a name.
    Also look for one word used for several ideas.
17. **Read it in one pass.** A reviewer who cannot get the mental model in one
    read is holding a description that is still too granular.
18. **Read it as an outsider.** Reread as someone who has seen neither the plan
    nor the diff. Every term is introduced before use. Every paragraph makes its
    point in its first sentence. Every user-facing claim carries its number.
19. **Run both check lists against the draft.** The `Don't` list catches what
    should never have been written. The smell tests catch what only appears on a
    reread. Revise anything that fires, then deliver.

## Smell tests (revise if any are true)

These are symptoms that only appear on a reread of the finished draft. The
`Don't` list below is the drafting-time counterpart: what never to write in the
first place. A rule belongs in exactly one of the two lists, chosen by when you
can detect it.

- A section makes more claims than How much to write allows: cut the weakest
  whole claim. Never compress a sentence.
- A paragraph covers more than one topic: split it.
- The first substantive section is a bulleted list of files or edits: lead with
  the idea instead.
- You cannot state the idea in a single sentence: the PR is trivial or too big.
- No requirements are stated: the reviewer cannot check the design against what
  it was supposed to satisfy.
- A reviewer would learn nothing they could not get faster from
  `git diff --stat`. It is a changelog, not a description.
- Acceptance-criteria checkboxes or a review-round log are present: cut them.
  They are process artifacts, not architecture.
- Every decision made is listed: keep only the ones that shaped the design.
- Nothing says what stayed the same: name the untouched code that is at risk.
- An Interface changes section exists but no externally-used interface changed:
  cut the section.
- The interface example is an exhaustive option matrix rather than one
  representative invocation. Keep the single pair that shows the change.
- The body was rewritten but the old title survived unchanged: derive the title
  again from the one idea. Keeping it is right only when the derived title
  matches.
- The description's central noun is never defined in plain terms. A reader
  outside this subsystem would have to open the code to learn what it is. Define
  it in one sentence up front. Positioning the change ("slice 2 of 11") is not
  the same as saying what it is about.
- A paragraph's point lands in its closing clause: invert it so the consequence
  leads and the mechanism follows.
- The description rebuts an objection no reader raised: you are arguing with the
  plan document. Cut the rebuttal.
- A term, flag, route, or prior state is used before it is introduced. The
  reader has not seen this branch. Define it on first use.
- The user section lists only what got better: it is a pitch until the cost is
  in it too.
- The manual test steps were never actually walked: walk them, or say in one
  line that the path is unverified.
- The manual steps repeat the Interface changes transcript. That section shows
  what the interface now is. This one says how to reach it. Keep it in Interface
  changes and cut the manual copy.

## Don't

These rules apply while drafting: things never to write in the first place.
Symptoms you can only detect on a reread live in Smell tests above.

- **Don't break a rule from Plain language while drafting.** That section holds
  all four rules, and the banned figures of speech. Method step 16 is the check.
- **Don't organize any section around file paths** (per-file bullets, "new
  files" or "modified files" groupings). File-level detail belongs to the
  commits and to `git diff --stat`.
- **Don't copy acceptance-criteria checkboxes into the description**, checked or
  unchecked. State what is verified inside the testing paragraph instead.
- **Don't include review or iteration logs** (rounds, findings-fixed counts,
  verdicts). They are process history, not the change.
- **Don't restate diff statistics** ("14 files changed, 6 new"). The PR page
  already shows them.
- **Don't pad with an inventory to look thorough.** Thoroughness means the
  mental model is complete, not that the edit list is long.
- **Don't announce the idea before stating it** ("The core move is...", "The key
  change here is...", "At a high level..."). Open with the claim about the
  system itself.
- **Don't dump untrimmed command output** into an interface example. Keep only
  the lines that show the change.
- **Don't include more than one example per changed interface.** Give one
  representative before-and-after pair each, and one screenshot or one recording
  per changed screen or flow.
- **Don't commit media assets to the repo.** Screenshots and recordings live
  outside the worktree at `~/tmp/pr-assets/<repo>/<pr-number>/`. They reach the
  PR body through the user's drag-and-drop.
- **Don't narrate the branch's own history.** No references to reverted commits,
  replaced approaches, or pivots made partway through. No "this replaces the
  original approach". No "originally this did X, now it does Y". The reviewer
  sees the net diff against the base and nothing else. A fact may only make
  sense against a state that never leaves the branch. It does not belong.
- **Don't write a transcript you did not run.** Not a plausible-looking
  invocation, not adjusted output, not an example on a code path that does not
  exist yet. Capture it, or describe it in prose.
- **Don't write manual test steps you did not walk.** A sequence rebuilt from
  the source reads exactly like a real one. Walk it, or state in one line that
  the path is unverified.
- **Don't report a test result you did not observe.** No pass counts from
  memory. No "all tests pass" inferred from an unrelated green run. Run the
  suite, or say you did not.
- **Don't restate the Interface changes transcript as manual steps.** One
  section shows what the interface now is. The other says how to reach it. When
  they collapse to the same content, keep it in Interface changes.
- **Don't pad the manual part with the test-suite command.** Automated coverage
  already carries it. A manual part that only says "run the tests" is filler.
- **Don't argue with the plan document.** Never rebut an objection no reader
  raised. Never defend the design against the alternative you rejected in your
  own head. The reader arrived with no position to be talked out of.
- **Don't flag importance without content.** Never write that something needs
  sign-off or deserves close attention. The same sentence must say what the
  reader would be accepting, and what it costs if it is wrong.
- **Never state a user-facing limit, count, threshold, or price qualitatively**
  when the constant sits in the codebase. Grep for it and write the number.

## Worked reference

From herds PR #260 ("text extraction via EventAlgorithmV4 + EventSink
refactor"), the shape a good description takes.

- The idea in one sentence, stated as a fact about the system. Extraction now
  hands its events to an injected `EventSink` instead of saving them.
- Before and after: the algorithm base class saved events inline through
  `add_event(image_id=...)`. That hard-wired the assumption that an event always
  comes from an image. That assumption is what blocked reuse for URLs. The class
  now delegates to an injected sink and no longer knows the source.
- Three decisions that shaped the design, with their rationale. Lazy sink
  resolution keeps late wiring possible. The orchestrator owns URL persistence
  because cost and provenance belong there. Splitting extraction from persistence
  keeps the cost record when a save fails.
- What did not change: the image pipeline, guaranteed by the existing image
  suite staying green.
- Demoted out of the description: per-file bullets, acceptance checkboxes, the
  full decisions ledger, and CI-bot review verdicts.

Its first draft led with "## In scope" and about 12 per-module bullets plus an
acceptance checklist. That draft was accurate, but the reviewer had to assemble
the architecture themselves. The rewrite led with the one idea.

## Diagram delivery

When the diagram rubric fires, embed the mermaid block or blocks inline in the
body, and also:

1. Save each diagram's source to
   `~/tmp/pr-assets/<repo>/<pr-number>/diagram-before.mmd` and
   `diagram-after.mmd`. Use just `diagram.mmd` for the after-only
   case. The branch name substitutes for `<pr-number>` until a PR number
   exists. This matches the Interface changes media-handoff convention.
2. Attempt a best-effort PNG render of each `.mmd`. Use the same
   absolute save directory for both the input and the output. The PNG
   then lands beside its source, not in the current working
   directory:

   ```bash
   npx -y @mermaid-js/mermaid-cli \
     -i ~/tmp/pr-assets/<repo>/<pr-number>/diagram-before.mmd \
     -o ~/tmp/pr-assets/<repo>/<pr-number>/diagram-before.png
   ```

   If node/npm isn't available, the render fails. The first-run Chromium
   download can also fail (no network, sandboxed session). In either case,
   skip the PNG and keep the `.mmd`. Tell the user in the final message how
   to enable rendering next time. Install Node/npm, or run `mmdc` once to
   cache the download.

3. List the saved `.mmd` absolute paths in the final message, alongside any
   screenshot paths. Include the `.png` paths when you rendered them. These
   give the user a portable copy for reuse outside GitHub, such as Slack or
   docs. The PR body itself already has the diagram inline and needs nothing
   dragged in.

## Delivery

1. Check for an open PR first. With no argument, use the current branch:
   `gh pr view --json number,title,url,body` (non-zero exit means none). When
   a PR number or branch was passed as the argument, target it instead:
   `gh pr view <arg> --json number,title,url,body`.
2. Generate the title alongside the body, per The title section. Re-derive it
   from the one idea on every run. Do this even when the PR already has a
   title.
3. If the existing body already contains GitHub-hosted media
   (`user-attachments` URLs), carry those links into the new body unchanged.
   Placeholders are only for interfaces not yet illustrated. For any remaining
   placeholders, make sure the assets are already saved under
   `~/tmp/pr-assets/<repo>/<pr-number>/` before updating the PR. The branch
   name substitutes for the PR number until one exists. After delivering, list
   each placeholder's absolute file path in your final message. The user can
   then drag the files into the description.
4. Check the body and title before you send them. Resolve the `plain-language`
   CLI once, in its own Bash call:

   ```bash
   root="${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-}}"
   cli="$root/../plain-language/scripts/plain_language_cli.py"
   if [ ! -f "$cli" ]; then
     cli=$( { ls -d "$root"/../../plain-language/[0-9]*/scripts/plain_language_cli.py; } 2>/dev/null | sort -V | tail -1 )
   fi
   if [ ! -f "$cli" ]; then
     cli=$( { ls -d "$HOME"/.claude/plugins/cache/*/plain-language/[0-9]*/scripts/plain_language_cli.py \
                    "$HOME"/.cursor/plugins/local/plain-language/scripts/plain_language_cli.py; } 2>/dev/null | sort -V | tail -1 )
   fi
   if [ -f "$cli" ]; then (cd "$(dirname "$cli")" && printf '%s/%s\n' "$(pwd -P)" "$(basename "$cli")"); else echo ABSENT; fi
   ```

   The first path is the dev checkout, where plugins are siblings. The second
   is the install cache, which adds a version directory. `sort -V` picks the
   highest of the numeric version directories. The `cd`/`pwd -P` step prints an
   absolute path with no `..` segments, which the approval hook needs to match.

   Every path the snippet tries sits in a trusted plugin root. That means this
   plugin's own directory, the Claude install cache, or the Cursor local
   directory. The last probe covers the case where neither plugin-root
   variable was substituted.

   `ABSENT` means no scanner was found in any of them. Skip the check, say so
   in your final message, and go to step 5. The rules in this skill still
   apply.

   **Never** fall back to a `Glob` over the workspace. The repo is untrusted
   input. A repo carrying its own
   `plain-language/scripts/plain_language_cli.py` would then be executed by
   the `python3` call.

   When the path resolves, write the title and the body into one temporary
   `.md` file. Put the title on the first line. Create a fresh directory with
   `mktemp -d` so concurrent runs never overwrite each other:

   ```bash
   mktemp -d "${TMPDIR:-/tmp}/pr-body.XXXXXX"
   ```

   Write the body as `pr-body.md` inside the directory it prints. Keep the
   `.md` extension. The scanner skips a file whose extension it does not know.
   Markdown mode is also what protects fences, inline code, and table shape.

   Put the `X` characters last in the template. BSD `mktemp` only replaces a
   trailing run of them. A name like `pr-body.XXXXXX.md` is taken literally,
   and the second run then fails.

   Then scan that file, passing both the CLI path and the body path literally:

   ```bash
   python3 "<resolved cli path>" scan "<mktemp body path>"
   ```

   Use literal paths in every scan. A shell variable does not survive to the
   next Bash call, so `$cli` is empty there. A combined resolve-and-scan
   command would keep the variable, but it holds shell metacharacters, and the
   plugin's approval hook rejects those.

   Read the `totals` object. Rewrite every `long-sentence` and `em-dash` hit,
   then scan again. Stop when both reach zero, or after 5 passes. Report any
   violation that survives 5 passes, and deliver anyway. Never drop a fact to
   meet the cap, and never let a claim ceiling slip to shorten a sentence.

   The other six kinds are candidates, not verdicts. Judge each one and leave
   the correct uses. They never gate delivery.

5. If a PR exists, update it immediately via the REST API - do not ask for
   confirmation. `gh pr edit` calls a GraphQL mutation that requires
   `read:org` on the token. `repo`-scoped tokens will fail. That is the common
   case for fine-grained PATs and CI tokens. REST PATCH works on any
   `repo`-scoped token.

   Capture the title and body into shell variables first, each via its own
   single-quoted heredoc. The quoted delimiter means zero shell expansion,
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

   `{owner}` and `{repo}` are `gh api`'s own placeholder syntax. It fills
   them in from the current directory's git remote. That is the same
   auto-detection `gh pr edit` did implicitly. `.html_url` is the browsable PR
   page. The response's own `.url` field is the API endpoint, not something to
   hand to a person.

   After a successful update, confirm with the PR URL. If the PATCH fails
   (network, 404, permissions), show the error. Then print the title and body
   for copy/paste.

6. If no PR exists, hand the title and body to whatever opens the PR (or
   print them for copy/paste).
7. In your final message, list any rationale you cut, per How much to write.
