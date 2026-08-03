---
description: Rewrite prose to the plain-language standard until the scan is clean, and prove no code changed. Covers code comments, docstrings, markdown, plain text, a PR's changed lines, the branch diff, or pasted text.
argument-hint: "[path | PR number or URL | free text] [--full]"
---

# plain-language: apply

Rewrite prose until the scanner reports zero long-sentence and em-dash
violations. Code never changes; the `verify` subcommand proves it.

Bundled assets at `${CLAUDE_PLUGIN_ROOT}` (if the variable is not
substituted in this context, find the files with
`Glob "**/plain-language/standard.md"` and read the siblings alongside it):

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
   - In code files, edit comment and docstring interiors only. In markdown,
     never touch fences, inline code, frontmatter, URLs, or table shape.
5. Re-run the scanner. Repeat step 4 until `long-sentence` and `em-dash`
   totals reach zero in scope, up to 5 passes. If violations remain after 5
   passes, stop and report them honestly.
6. Verify every touched file:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plain_language_cli.py" verify --ref HEAD FILE...
   ```

   Use `--baseline <copy>` for files snapshotted in step 2. A non-zero exit
   means the edit touched protected content: restore that file from its
   baseline and redo it.

7. If the repo has a test suite, run it. Docstring edits can break doctests
   and doc tooling.
8. Report: files touched; violations fixed, by kind; banned tokens judged
   literal and left; files and blocks skipped, by reason; test-suite result.
   Committing stays with the user.

## Don't

- **Don't change code, string literals, identifiers, or log messages.** Only
  comment and docstring prose changes in code files.
- **Don't delete a comment because it looks redundant.** Whether a comment
  should exist is review's call, not this command's.
- **Don't meet the cap by dropping facts.** Split the sentence instead.
- **Don't skip the verify step, even for a one-line edit.**
- **Don't commit.** Leave the worktree diff for the user.
