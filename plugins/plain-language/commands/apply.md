---
description: Rewrite prose to the plain-language standard until the scan is clean, and prove no code changed. Covers code comments, docstrings, markdown, plain text, a PR's changed lines, the branch diff, or pasted text.
argument-hint: "[path | PR number or URL | free text] [--full]"
---

# plain-language: apply

Rewrite prose until the scanner reports zero long-sentence and em-dash
violations. Code never changes; the `verify` subcommand proves it.

Bundled assets at `${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT}}`. If the variable is not substituted
in this context, find the files with `Glob "**/plain-language/standard.md"`
and read the siblings alongside it.

- `standard.md` - the writing standard.
- `scope.md` - scope resolution, the extension table, the changed-lines map.
- `scripts/plain_language_cli.py` - scanner and verifier.

**Target:** "$ARGUMENTS"

## Steps

1. Resolve the scope with `scope.md`. Free text: rewrite it in the reply per
   `standard.md` and stop. No files are touched.
2. Snapshot baselines. For each target file that differs from `HEAD` (check
   `git status --porcelain`), copy it into the scratchpad directory before
   editing. Clean files use `--ref HEAD` in step 6; snapshotted files use
   `--baseline <copy>`.
3. Run the scanner (with the changed-lines map for PR scope). Files with zero
   violations are done; count them for the report.
4. For each flagged block, read the surrounding file content, then rewrite
   the prose:
   - Split sentences over 20 words. Never delete a fact the reader would
     have to rebuild.
   - Convert every em-dash (U+2014) in each sentence you rewrite: use a
     comma, a colon, parentheses, or two sentences.
   - Turn passives active where the actor is known.
   - Replace figurative banned tokens with the real name. Leave literal uses,
     and leave hits in blocks the scanner skipped.
   - Cut each `filler-phrase` hit that carries no fact, or use the short form
     from `standard.md`. Keep the phrase when the sentence needs those words.
   - Rewrite each `copula-avoidance` hit around "is", "are", or "has". Keep
     the verb when it carries real meaning.
   - Replace each `empty-phrase` hit with the thing it gestures at. Cut the
     sentence when no real name exists.
   - Rewrite each `dash-substitute` hit the way you rewrite an em-dash. Leave
     an en-dash inside a number range.
   - Rewrite each `diff-anchored` hit to describe the thing as it is now.
     Leave the hit in a changelog, a release note, or a migration guide.
   - In code files, edit comment and docstring interiors only. In markdown,
     never touch fences, inline code, frontmatter, URLs, or table shape.

   Judge each candidate against "What not to flag" in `standard.md` before
   you edit it. A correct use stays.

5. Audit each rewrite before you move on. Ask: does it state a fact, a name,
   a number, a date, or a path that the original did not? Splitting a long
   sentence is where this happens. The second half needs a subject, and an
   invented one reads fluently. `verify` cannot catch it, because a made-up
   claim inside a comment leaves the code byte-identical. Restore the fact
   from the original, or write the sentence without it.
6. Re-run the scanner. Repeat step 4 until `long-sentence` and `em-dash`
   totals reach zero in scope, up to 5 passes. If violations remain after 5
   passes, stop and report them honestly. The six candidate kinds never gate
   the loop: a hit you judged correct stays, and it would never clear.
7. Verify every touched file:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT}}/scripts/plain_language_cli.py" verify --ref HEAD FILE...
   ```

   Use `--baseline <copy>` for files snapshotted in step 2. A non-zero exit
   means the edit touched protected content. Restore that file from its
   baseline and redo the rewrite.

8. If the repo has a test suite, run it. Docstring edits can break doctests
   and doc tooling.
9. Report the files touched and the violations fixed, by kind. Then the
   candidate hits you judged correct and left, grouped by kind. Then the
   files and blocks skipped by reason, and the test-suite result. Committing
   stays with the user.

## Don't

- **Don't change code, string literals, identifiers, or log messages.** Only
  comment and docstring prose changes in code files.
- **Don't delete a comment because it looks redundant.** Whether a comment
  should exist is review's call, not this command's.
- **Don't meet the cap by dropping facts.** Split the sentence instead.
- **Don't add a fact the original did not state.** No name, number, date,
  path, or reason that you cannot point to in the source text. A rewrite
  that reads better and claims more is a defect, and `verify` will pass it.
- **Don't skip the verify step, even for a one-line edit.**
- **Don't commit.** Leave the worktree diff for the user.
