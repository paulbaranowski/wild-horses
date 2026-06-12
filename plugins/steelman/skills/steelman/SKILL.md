---
name: steelman
description: Argue the strongest good-faith case AGAINST the proposed changes. Builds a steelman of the opposition — hidden costs, wrong assumptions, simpler alternatives, second-order effects, and the do-nothing option — for a plan, design, or diff under discussion. Use when the user says "steelman the case against this", "argue against these changes", "red-team this plan", or "what's the strongest objection?".
user-invocable: true
disable-model-invocation: true
argument-hint: "[path to a design/plan file — optional; defaults to the proposal in the conversation]"
---

# Steelman the Case Against

Your job is to argue **against** the proposed changes — and to make that argument as strong as an intelligent, well-informed opponent would. This is a _steelman_, not a strawman: you are constructing the objection the proponent should most fear, not scoring cheap points.

You are a temporary adversary on purpose. Do not hedge into balance, do not end with "but it's probably fine." Someone else owns the case _for_ the change; for this turn, you own the case _against_ it.

## Step 1 — Identify what you're arguing against

- **If `$ARGUMENTS` names a file or path** — read it. That document is the proposal.
- **If `$ARGUMENTS` is empty (DEFAULT)** — the proposal is whatever change is currently on the table in this conversation: the plan, design, spec, or diff most recently discussed. If that's genuinely ambiguous, ask one yes/no question to pin it down before continuing.

Restate the proposal in **one or two sentences** so it's clear what you're attacking. If you can't restate it faithfully, you can't steelman against it — get the restatement right first.

## Step 2 — Build the strongest case against

Work through these angles. Use the ones that bite; skip the ones that don't. Quality over coverage — three sharp objections beat ten weak ones.

- **Hidden / underestimated cost** — what this actually costs to build, maintain, document, and operate that the proposal glosses over.
- **Load-bearing assumptions** — the premises the change depends on. Name each one and ask what happens if it's wrong.
- **Simpler alternative** — a cheaper way to get most of the benefit, including changing nothing. If a smaller move captures 80% of the value, the full change has to justify the remaining 20%.
- **Second-order effects** — what this makes harder _later_: new coupling, precedent, migration debt, things future changes now have to route around.
- **Reversibility** — how hard this is to undo once shipped. One-way doors deserve more suspicion than two-way doors.
- **Who pays** — costs that land on someone other than the proposer (other maintainers, users, on-call, future-you).
- **The do-nothing option** — what's actually wrong with the status quo, honestly weighed. Sometimes the current state is fine and the itch isn't worth scratching.

## Step 3 — Deliver it

Output directly in the chat. Lead with the single strongest objection, then the rest in descending order of force. For each: state it plainly, then back it with a concrete consequence — not a vague worry.

End with a one-line **bottom line**: the condition under which you'd drop your opposition (e.g. "If X is already true, most of this falls away"). That keeps the critique honest and actionable instead of obstructionist.

## Rules

**Do:**

- Steelman, not strawman — attack the _best_ version of the proposal, after granting it every reasonable assumption.
- Be specific — name the file, the assumption, the cost. A concrete objection can be answered; a vague one just nags.
- Concede real strengths in one line, then explain why they still don't carry the change. A critique that admits nothing is easy to dismiss.

**Don't:**

- Don't manufacture objections to hit a quota — if the change is sound, say which parts are sound and aim your fire at the weakest remaining joint.
- Don't nitpick style, naming, or formatting when the question on the table is whether to make the change at all.
- Don't soften into a both-sides summary — the case _for_ is not your job this turn; argue the case _against_ and let the user weigh it.
- Don't invent facts about the codebase to win the argument — if an objection depends on something you haven't verified, say it's contingent on that, or go check it.
