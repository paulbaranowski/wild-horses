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
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

WORD_CAP = 20
EM_DASH = "\u2014"

# Candidate figures of speech from the standard. The model judges each hit:
# literal uses ("attack surface", "half the rows") stay.
BANNED_RE = re.compile(
    r"(?<![\w-])(blast\s+radius|load[-\s]bearing|seams?|spines?|surfaces?|halves|half)(?![\w-])",
    re.IGNORECASE)

# Tokens like "e.g." must not end a sentence. Compared lowercase, dots kept.
ABBREVIATIONS = {"e.g", "i.e", "etc", "vs", "cf", "ca", "approx", "resp",
                 "incl", "vol", "no", "fig", "eq", "sec", "ch", "pp",
                 "st", "dr", "mr", "mrs", "ms", "jr", "sr"}
TERMINATORS = ".!?"
# Characters that may follow the terminator and stay inside the sentence:
# quotes, bold/italic markers, closing brackets.
CLOSERS = "\"'`*_)]}”’"
CODE_SPAN_RE = re.compile(r"`[^`\n]+`")

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


@dataclass
class Block:
    kind: str  # "comment" | "docstring" | "paragraph" | "heading" | "table-cell"
    lines: list[tuple[int, str]]  # (1-based line number, prose text)
    skip_reason: str | None = None


def split_sentences(text: str) -> list[tuple[int, str]]:
    """Return (offset, sentence) pairs.

    Three quirks the standard's own prose needs (seen in PR #185):
    a bold lead-in ending ``.**`` terminates its sentence, closing quotes
    stay attached to their sentence, and abbreviations never split.
    """
    out: list[tuple[int, str]] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] in TERMINATORS:
            j = i + 1
            while j < n and text[j] in CLOSERS:
                j += 1
            at_boundary = j >= n or text[j].isspace()
            if at_boundary and not _is_abbreviation(text, i):
                _push_sentence(out, text, start, j)
                start = j
                i = j
                continue
        i += 1
    _push_sentence(out, text, start, n)
    return out


def _push_sentence(out: list[tuple[int, str]], text: str, start: int, end: int) -> None:
    chunk = text[start:end]
    lead = len(chunk) - len(chunk.lstrip())
    stripped = chunk.strip()
    if stripped:
        out.append((start + lead, stripped))


def _is_abbreviation(text: str, dot: int) -> bool:
    if text[dot] != ".":
        return False
    k = dot - 1
    chars: list[str] = []
    while k >= 0 and (text[k].isalnum() or text[k] == "."):
        chars.append(text[k])
        k -= 1
    token = "".join(reversed(chars)).lower().strip(".")
    return bool(token) and (token in ABBREVIATIONS or len(token) == 1)


def word_count(sentence: str) -> int:
    """Whitespace tokens, with each inline code span collapsed to one word."""
    return len(CODE_SPAN_RE.sub("code", sentence).split())


def _mask_code_spans(text: str) -> str:
    """Same-length text with code spans letter-masked, so their contents
    never trigger em-dash or banned-token hits."""
    return CODE_SPAN_RE.sub(lambda m: "x" * len(m.group(0)), text)


def block_text(block: Block) -> str:
    return "\n".join(t for _, t in block.lines)


def _offset_line(block: Block, offset: int) -> int:
    for lineno, text in block.lines:
        if offset <= len(text):
            return lineno
        offset -= len(text) + 1
    return block.lines[-1][0] if block.lines else 0


def check_block(block: Block) -> list[dict]:
    violations: list[dict] = []
    masked_all = _mask_code_spans(block_text(block))
    for (lineno, _), masked_line in zip(block.lines, masked_all.split("\n")):
        count = masked_line.count(EM_DASH)
        if count:
            violations.append({"kind": "em-dash", "line": lineno, "count": count})
    for offset, sentence in split_sentences(block_text(block)):
        line = _offset_line(block, offset)
        words = word_count(sentence)
        if block.kind != "heading" and words > WORD_CAP:
            violations.append({"kind": "long-sentence", "line": line,
                               "word_count": words, "sentence": sentence})
        for m in BANNED_RE.finditer(_mask_code_spans(sentence)):
            violations.append({"kind": "banned-token", "line": line,
                               "token": " ".join(m.group(1).lower().split()),
                               "sentence": sentence})
    return violations


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
