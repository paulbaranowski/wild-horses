#!/usr/bin/env python3
"""Compose initial prompt from baseline + selected feature snippets.

This script assembles a groundcrew initial prompt by starting with a baseline
context block (verbatim copy of groundcrew's DEFAULT_PROMPT_INITIAL from
src/lib/config.ts:273-295) and appending selected feature snippets in stable
order (the order defined in PROMPT_FEATURES, not the user's CSV order).

NOTE: The spec document claims the baseline includes a "[{{ticket}}] <title>"
PR-title rule, but groundcrew's actual DEFAULT_PROMPT_INITIAL does NOT contain
that rule. We match the source byte-for-byte so that users who pick "no features"
(omitting prompts.initial) see the same prompt as the runtime fallback.
"""

from __future__ import annotations

import argparse
import sys

BASE_CONTEXT_BLOCK = "\n".join([
    "You are working on Linear ticket {{ticket}} ({{title}}) in the {{worktree}} worktree subdirectory.",
    "",
    "Ticket description:",
    "",
    "{{description}}",
    "",
    "## Operating mode",
    "",
    "There is no human watching this session. Do not stop to ask clarifying questions. When the ticket is ambiguous or incomplete, choose the simplest reasonable interpretation consistent with the ticket and the codebase, then document that choice in the PR description.",
    "",
    "## Workflow",
    "",
    "1. Inspect the repository instructions and existing patterns before editing.",
    "2. Implement the smallest sensible change that completes the ticket.",
    "3. Run the repository's documented verification command. If no documented verification exists, run the smallest relevant test suite you can find. Fix failures you introduced before continuing.",
    "4. Review your own diff before stopping. Look for bugs, regressions, missing tests, security issues, and convention violations, then fix any issues you find.",
    "5. If this repository uses GitHub and the `gh` CLI is available and authenticated, open a pull request. If you cannot open one, leave the branch ready and record the blocker.",
    "6. Include `Closes {{ticket}}` in the PR description.",
    "{{workspaceContinuationInstruction}}",
    "",
    "Stop after the branch is ready or the PR is open.",
])

PROMPT_FEATURES = {
    "superpowers": (
        "## Using superpowers skills\n"
        "\n"
        "Invoke `superpowers:` skills (brainstorming, writing-plans, executing-plans, "
        "test-driven-development, systematic-debugging, requesting-code-review) for any "
        "non-trivial work. Skills tell you HOW to approach the task; don't try to derive "
        "the procedure from first principles when a skill exists.\n"
    ),
    "babysitPr": (
        "## After opening the PR\n"
        "\n"
        "Invoke `/core:babysit-pr` after pushing the branch. It watches CI, replies to "
        "review threads, and surfaces CodeRabbit feedback so the PR keeps moving without "
        "you needing to be present.\n"
    ),
    "codeStylePointer": (
        "## Before writing code\n"
        "\n"
        "Read `CLAUDE.md` and `AGENTS.md` in the repository root (if present). These "
        "encode the project's conventions, lint rules, and review expectations. Follow "
        "what they say even when it differs from your defaults.\n"
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="compose_initial_prompt.py",
        description="Compose initial prompt from baseline + selected feature snippets.",
    )
    parser.add_argument(
        "--features",
        default="",
        help="Comma-separated feature keys to include (e.g., superpowers,babysitPr). Empty = baseline only.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Parse features CSV.
    features_str = args.features.strip()
    if not features_str:
        # Empty or absent --features: output baseline only.
        sys.stdout.write(BASE_CONTEXT_BLOCK)
        return

    # Split and validate.
    requested = [f.strip() for f in features_str.split(",") if f.strip()]
    unknown = [f for f in requested if f not in PROMPT_FEATURES]
    if unknown:
        valid_keys = ", ".join(PROMPT_FEATURES.keys())
        sys.stderr.write(
            f"ERROR: unknown feature(s): {', '.join(unknown)}\n"
            f"Valid keys: {valid_keys}\n"
        )
        sys.exit(2)

    # Emit baseline + snippets in stable order (PROMPT_FEATURES dict order).
    output = BASE_CONTEXT_BLOCK
    for key in PROMPT_FEATURES:
        if key in requested:
            output += "\n\n" + PROMPT_FEATURES[key]

    sys.stdout.write(output)


if __name__ == "__main__":
    main()
