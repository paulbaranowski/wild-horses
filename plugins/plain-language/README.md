# plain-language

Apply the plain-language writing standard to prose wherever it lives: code
comments, docstrings, markdown, plain text, or pasted text. The standard is
adapted from ASD-STE100 Simplified Technical English; see
[standard.md](standard.md).

## Install

    /plugin install plain-language@wild-horses

## Commands

    /plain-language:check [scope]    report violations, edit nothing
    /plain-language:apply [scope]    rewrite until the scan is clean

Scope forms, in resolution order: a file or directory path, a PR number or
URL, then `--full` (the whole repo). After those come free text (checked or
rewritten inline) and no argument (the current branch's changed files). See
[scope.md](scope.md).

## Guarantees

- In code files, only comment and docstring prose changes. The bundled CLI's
  `verify` subcommand proves the comment-stripped file is byte-identical to
  the baseline's.
- In markdown, fences, inline code spans, frontmatter, link URLs, and table
  shapes survive every run, proven the same way.
- Deterministic checks live in the stdlib-only
  `scripts/plain_language_cli.py`. Two kinds are verdicts: the 20-word
  sentence cap and the em-dash. Six kinds are candidates, and the model
  judges each hit in context. Those six are figurative tokens, filler
  phrases, copula substitutes, empty phrases, dash substitutes, and change
  narration.
- The plugin never commits. It edits the worktree and reports.

## Drift gate

The plugin's own prose must pass its own scanner. That also works as a drift
check for any prompt file that carries the standard:

    /plain-language:check plugins/wild-pr/skills/summary-writer/SKILL.md
