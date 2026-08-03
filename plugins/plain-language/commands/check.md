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

4. Judge every `banned-token` hit: read its sentence and decide literal or
   figurative, per `standard.md`. Literal uses pass. Figurative uses are
   violations.
5. Report in this order. First the totals by kind. Then, per file, each
   violation with its line number and sentence. Then the banned tokens you
   judged literal and left. Then the files skipped, with reasons, and the
   blocks skipped, grouped by reason. Last, any parse errors.

When violations exist, end with this line: run `/plain-language:apply` with
the same argument to fix them.

## Don't

- **Don't edit any file.** This command reports only; `/plain-language:apply`
  is the one that rewrites.
- **Don't report a `banned-token` hit as a violation before judging it.**
  Literal uses of these words are correct and stay.
- **Don't hide skipped files or blocks.** A reader needs to know what the
  scan did not cover.
