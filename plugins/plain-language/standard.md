# The plain-language standard

Rules adapted from ASD-STE100 Simplified Technical English. They apply to
prose only: comments, docstrings, markdown body text, plain text.

## Hard rules (the scanner enforces the first two)

- **Keep each sentence to 20 words or fewer.** Meet the limit by splitting
  one sentence into two. Never meet it by deleting a fact the reader would
  have to rebuild.
- **Never emit the em-dash character (U+2014).** Use a comma, a colon,
  parentheses, or two sentences. Convert every em-dash in each sentence you
  rewrite, even ones you did not add. Writing the same dash a different way
  does not satisfy the rule; see "Dashes in disguise" below.
- **Use active voice.** Write "the test reads the catalog", not "the catalog
  is read by the test". Passive stays when the actor is unknown or
  irrelevant.
- **Use one word for one idea.** Pick a word for an idea, then use only that
  word for it.
- **Give each paragraph one topic.** A new topic starts a new paragraph.
- **Lead with the consequence, then the mechanism.** If the point lands in
  the closing clause, invert the sentence.

## What stays

Technical names stay: real identifiers, real domain terms, API names, and
file paths. Define one on first use when it is not common ground.

## Banned when figurative

Never use a figure of speech where a real name exists. The scanner flags
these tokens as candidates. You judge each hit in context:

seam, spine, surface, half, blast radius, load-bearing.

The ban is on the figurative use, not the token. "Attack surface" and
"half the rows" are literal, and they stay. When a hit is figurative, look
up the real name and write it.

## Cut the filler

Never spend words that carry no fact. The scanner flags these phrases as
candidates. Delete the phrase, or use the short form:

    in order to            -> to
    due to the fact that   -> because
    at this point in time  -> now
    in the event that      -> if
    has the ability to     -> can
    it is important to note that -> (delete)
    could potentially      -> could

A hit is not always wrong. "A number of" is filler when the count stays
unknown. It is correct when the sentence goes on to name the number.

## Use is, are, and has

Never dress up a plain statement with an elaborate verb. Writers call this
copula avoidance. The writer drops an ordinary "is" or "has" and reaches
for "serves as", "stands as", "boasts", or "features a". The scanner flags
those four as candidates.

    The cache serves as the fallback.  -> The cache is the fallback.
    The gallery boasts three rooms.    -> The gallery has three rooms.
    The release marks a shift.         -> The release changes X to Y.

Literal uses stay. A proxy that "functions as" a load balancer really does
perform that function, and the verb carries the meaning.

## Never sound decisive and say nothing

Never write a phrase that promises a deeper truth and then restates an
ordinary point. Name the thing, or cut the sentence. Two shapes hit most
often. The first claims to cut through noise:

    The real question is whether teams adapt. -> Can teams adapt?
    At its core, what matters is readiness.   -> Readiness decides it.
    And that is not cheap here.               -> (name the cost)

The second announces the writing instead of doing it:

    Let's dive into how caching works.  -> Next.js caches at three layers.
    Here's what you need to know.       -> (delete, then write it)

## Dashes in disguise

Never replace a banned em-dash with a different character that reads the
same. The scanner flags three shapes as candidates:

- An en-dash (U+2013) with a space on each side.
- An en-dash glued between two letters, as in `client–server`.
- A double hyphen with a space on each side.

Rewrite each one the way you would rewrite an em-dash.

A number range keeps its en-dash. `2020–2024` and `pages 10–20` are correct
typography, and the scanner never flags them.

## Describe the thing, not the change

Never write a comment or a doc as if the reader knows what the last commit
did. A reader six months later has no diff in front of them.

    This was added to replace the old loop, which was O(n^2).
    -> This uses a hash map, so lookup costs O(1).

The scanner flags change words as candidates: `previously`, `was added`,
`the previous approach`, `used to be`, and others. A changelog, a release
note, and a migration guide are about a change, so their hits are correct
and stay.

## Length

Control length by cutting whole items, never by compressing sentences.
Compression produces prose the reader has to decode.

## What not to flag

A candidate hit is a place to look. Most of these are correct in a
technical repo, and rewriting them makes the prose worse:

- **A technical name that happens to be a flagged token.** `surface` in a
  graphics API, `spine` in a document model, `seam` in image processing.
- **A count the sentence goes on to name.** "A number of tests, 14 in all,
  still fail."
- **A verb that carries real meaning.** A proxy that "functions as" a load
  balancer performs that function. "Stands" said of a building is literal.
- **A word inside a quotation, a title, or an example.** This file quotes
  every phrase it bans, and every quote is correct.
- **Change words in a document about change.** A changelog, a release note,
  a migration guide, and a commit message all narrate a diff by design.
- **An en-dash in a number range.** `2020–2024` is not a dash substitute.
- **A phrase the alternative makes worse.** "In order to" reads better than
  "to" when three infinitives follow it. Keep it and say so.

Two rules are never candidates and never negotiable: the 20-word cap and
the em-dash. The scanner rules on those alone.
