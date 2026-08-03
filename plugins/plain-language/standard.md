# The plain-language standard

Rules adapted from ASD-STE100 Simplified Technical English. They apply to
prose only: comments, docstrings, markdown body text, plain text.

## Hard rules (the scanner enforces the first two)

- **Keep each sentence to 20 words or fewer.** Meet the limit by splitting
  one sentence into two. Never meet it by deleting a fact the reader would
  have to rebuild.
- **Never emit the em-dash character (U+2014).** Use a comma, a colon,
  parentheses, or two sentences. Convert every em-dash in each sentence you
  rewrite, even ones you did not add.
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

## Length

Control length by cutting whole items, never by compressing sentences.
Compression produces prose the reader has to decode.
