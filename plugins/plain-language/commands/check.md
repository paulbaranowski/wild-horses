---
description: Scan prose for plain-language violations and report them without editing. Covers code comments, docstrings, markdown, plain text, a PR's changed lines, the branch diff, or pasted text.
argument-hint: "[path | PR number or URL | free text] [--full]"
---

# plain-language: check

Report where prose breaks the plain-language standard. This command never
edits a file.

Bundled assets at `${CLAUDE_PLUGIN_ROOT}`. If the variable is not substituted
in this context, find the files with `Glob "**/plain-language/standard.md"`
and read the siblings alongside it.

- `standard.md` - the writing standard.
- `scope.md` - scope resolution, the extension table, the changed-lines map.
- `scripts/plain_language_cli.py` - the deterministic scanner.

**Target:** "$ARGUMENTS"

## Steps

1. Resolve the scope with `scope.md`. The result is a file list, a file list
   plus a changed-lines map, or free text.
2. Free text: check it against `standard.md` yourself. List each violation
   with its sentence in the reply. Stop here.
3. Run the scanner:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plain_language_cli.py" scan FILE...
   ```

   For PR scope, add `--changed-lines -` and pipe the map per `scope.md`.

4. Judge every candidate hit against `standard.md`, including its "What not
   to flag" section. Six kinds are candidates, not verdicts:
   - `banned-token`: read the sentence and decide literal or figurative.
     Literal uses pass. Figurative uses are violations.
   - `filler-phrase`: a violation when the phrase carries no fact. It passes
     when the sentence needs those words.
   - `copula-avoidance`: a violation when "is", "are", or "has" says the same
     thing. It passes when the verb carries real meaning.
   - `empty-phrase`: a violation when the phrase promises depth and names
     nothing. It passes when the sentence goes on to name the thing.
   - `dash-substitute`: a violation when the character does an em-dash's job.
     A number range passes.
   - `diff-anchored`: a violation when the prose narrates a change the reader
     cannot see. It passes in a changelog, a release note, or a migration
     guide, because those documents are about a change.
5. Report in this order. First the totals by kind. Then, per file, each
   violation with its line number and sentence. Then the candidate hits you
   judged correct and left, grouped by kind. Then the files skipped, with
   reasons, and the blocks skipped, grouped by reason. Last, any parse errors.

When violations exist, end with this line: run `/plain-language:apply` with
the same argument to fix them.

## Don't

- **Don't edit any file.** This command reports only; `/plain-language:apply`
  is the one that rewrites.
- **Don't report a candidate hit as a violation before judging it.** All six
  candidate kinds have correct uses, and those stay. Read "What not to flag"
  in `standard.md` before you rule.
- **Don't hide skipped files or blocks.** A reader needs to know what the
  scan did not cover.
