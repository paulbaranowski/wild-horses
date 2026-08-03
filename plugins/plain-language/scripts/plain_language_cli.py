#!/usr/bin/env python3
"""plain-language CLI.

Deterministic checks for the plain-language writing standard. Two subcommands:

- ``scan`` extracts prose blocks (comments, docstrings, markdown paragraphs)
  and reports violations: long-sentence, em-dash, banned-token.
- ``verify`` proves an edit touched prose only. Comment mode: the
  comment-stripped file is byte-identical to the baseline's stripped form.
  Prose mode: fences, inline code spans, frontmatter, link URLs, and table
  shapes are unchanged.

Judgment stays with the model. Banned tokens are candidates, not verdicts.
Both subcommands print JSON on stdout. ``scan`` exits 0 whenever it ran.
``verify`` exits 1 when protected content changed, 2 on usage errors.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

WORD_CAP = 20
EM_DASH = "—"

# extension -> (language, mode). mode "comment" edits comment prose only;
# mode "prose" treats the whole file as prose.
EXTENSIONS: dict[str, tuple[str, str]] = {
    ".py": ("python", "comment"),
    ".js": ("javascript", "comment"),
    ".jsx": ("javascript", "comment"),
    ".ts": ("javascript", "comment"),
    ".tsx": ("javascript", "comment"),
    ".mjs": ("javascript", "comment"),
    ".cjs": ("javascript", "comment"),
    ".sh": ("shell", "comment"),
    ".bash": ("shell", "comment"),
    ".zsh": ("shell", "comment"),
    ".md": ("markdown", "prose"),
    ".markdown": ("markdown", "prose"),
    ".txt": ("text", "prose"),
}


def read_source(path: str) -> str | None:
    """Bytes in, exact text out. None when the file is not UTF-8 text."""
    try:
        return Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def empty_report() -> dict:
    return {"files": [], "skipped_files": [], "errors": [],
            "totals": {"long-sentence": 0, "em-dash": 0, "banned-token": 0,
                       "files_scanned": 0, "files_clean": 0}}


def cmd_scan(args: argparse.Namespace) -> int:
    report = empty_report()
    for path in args.files:
        ext = Path(path).suffix.lower()
        if ext not in EXTENSIONS:
            report["skipped_files"].append({"path": path, "reason": "unknown-extension"})
            continue
        if read_source(path) is None:
            report["skipped_files"].append({"path": path, "reason": "not-utf8"})
            continue
        report["totals"]["files_scanned"] += 1
    print(json.dumps(report, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    print(json.dumps({"error": "not-implemented"}))
    return 2


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="plain_language_cli.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_scan = sub.add_parser("scan", help="report violations, edit nothing")
    p_scan.add_argument("files", nargs="+")
    p_scan.add_argument("--changed-lines", choices=["-"], default=None,
                        help="read a {path: [[start, end], ...]} JSON map from stdin")
    p_scan.add_argument("--all-blocks", action="store_true",
                        help="include clean and skipped blocks in the output")
    p_scan.set_defaults(func=cmd_scan)
    p_verify = sub.add_parser("verify", help="prove protected regions are unchanged")
    p_verify.add_argument("files", nargs="+")
    p_verify.add_argument("--ref", help="git ref holding the baseline")
    p_verify.add_argument("--baseline", help="path to a saved baseline copy (single file)")
    p_verify.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
