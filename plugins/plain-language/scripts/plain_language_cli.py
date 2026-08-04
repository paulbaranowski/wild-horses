#!/usr/bin/env python3
"""plain-language CLI.

Deterministic checks for the plain-language writing standard. Two subcommands:

- ``scan`` extracts prose blocks (comments, docstrings, markdown paragraphs)
  and reports violations. Two are verdicts: long-sentence and em-dash. Six
  are candidates the model rules on: banned-token, filler-phrase,
  copula-avoidance, empty-phrase, dash-substitute, diff-anchored.
- ``verify`` proves an edit touched prose only. Comment mode: the
  comment-stripped file is byte-identical to the baseline's stripped form.
  Prose mode: fences, inline code spans, frontmatter, link URLs, and table
  shapes are unchanged.

Judgment stays with the model. A candidate hit is a place to look, not a
verdict.
Both subcommands print JSON on stdout. ``scan`` exits 0 whenever it ran.
``verify`` exits 1 when protected content changed, 2 on usage errors.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict

WORD_CAP = 20
EM_DASH = "\u2014"

# Verdicts: the scanner decides alone, and `apply` loops until both reach
# zero. Candidates: the scanner finds a hit, and the model rules on it in
# context. A candidate never gates the loop, because a hit judged correct
# stays in the text and would never clear.
VerdictKind = Literal["long-sentence", "em-dash"]
CandidateKind = Literal["banned-token", "filler-phrase", "copula-avoidance",
                        "empty-phrase", "dash-substitute", "diff-anchored"]
# The kinds `check_block` can emit. `empty_report` builds its counters from
# this tuple, so a new kind cannot land without its counter.
ViolationKind = VerdictKind | CandidateKind
VIOLATION_KINDS: tuple[ViolationKind, ...] = (
    "long-sentence", "em-dash", "banned-token", "filler-phrase",
    "copula-avoidance", "empty-phrase", "dash-substitute", "diff-anchored")

BlockKind = Literal["comment", "docstring", "paragraph", "heading", "table-cell"]
SpanKind = Literal["comment", "docstring"]
Mode = Literal["comment", "prose"]
Language = Literal["python", "javascript", "shell", "markdown", "text"]
# The languages with a span extractor. `spans_for` raises for anything else,
# so a new comment-mode entry in EXTENSIONS needs an extractor first.
CommentLanguage = Literal["python", "javascript", "shell"]
SkipReason = Literal["shebang", "fenced-code", "field-list", "doctest",
                     "directive", "empty", "license", "commented-code"]

# One line of prose and one sentence are both (int, str). The ints mean
# different things: a 1-based line number against a character offset into
# the joined block text. Naming them keeps the two apart.


class ProseLine(NamedTuple):
    lineno: int
    text: str


class Sentence(NamedTuple):
    offset: int  # character offset into the joined block text
    text: str


class LineRange(NamedTuple):
    start: int
    end: int


# --- The scan report: this is the plugin's published stdout contract. ---


class EmDashViolation(TypedDict):
    kind: Literal["em-dash"]
    line: int
    count: int


class LongSentenceViolation(TypedDict):
    kind: Literal["long-sentence"]
    line: int
    word_count: int
    sentence: str


class CandidateViolation(TypedDict):
    """Every candidate kind reports the same way. The payload carries the
    text that matched, plus the sentence the model needs to rule on it."""
    kind: CandidateKind
    line: int
    token: str
    sentence: str


Violation = EmDashViolation | LongSentenceViolation | CandidateViolation

# Hyphenated wire keys, so the functional form keeps the names exact. Every
# ViolationKind needs an entry here; cmd_scan tallies straight into it.
Totals = TypedDict("Totals", {
    "long-sentence": int, "em-dash": int, "banned-token": int,
    "filler-phrase": int, "copula-avoidance": int, "empty-phrase": int,
    "dash-substitute": int, "diff-anchored": int,
    "files_scanned": int, "files_clean": int,
})


class SkippedFile(TypedDict):
    path: str
    reason: Literal["unknown-extension", "not-utf8"]


class ScanError(TypedDict):
    path: str
    error: Literal["parse-failed"]


class ReportedBlock(TypedDict):
    kind: BlockKind
    start_line: int
    end_line: int
    violations: list[Violation]


class SkippedBlock(TypedDict):
    kind: BlockKind
    start_line: int
    end_line: int
    skip_reason: SkipReason


class FileEntry(TypedDict):
    path: str
    language: Language
    mode: Mode
    blocks: list[ReportedBlock]
    skipped_blocks: list[SkippedBlock]


class ScanReport(TypedDict):
    files: list[FileEntry]
    skipped_files: list[SkippedFile]
    errors: list[ScanError]
    totals: Totals


# --- The verify report: the second published stdout contract. ---

VerifyReason = Literal[
    "unknown-extension", "not-utf8", "missing-baseline", "parse-failed",
    "baseline-parse-failed", "code-changed", "frontmatter-changed",
    "fence-changed", "code-span-changed", "link-url-changed",
    "table-shape-changed",
]


class _VerifyResultBase(TypedDict):
    path: str
    mode: str  # a Mode, or "unknown" for an extension the plugin does not read
    ok: bool


class VerifyResult(_VerifyResultBase, total=False):
    reason: VerifyReason  # absent when ok is True
    detail: str           # present only on code-changed


class VerifyReport(TypedDict):
    results: list[VerifyResult]
    ok: bool

# Candidate figures of speech from the standard. The model judges each hit:
# literal uses ("attack surface", "half the rows") stay.
BANNED_RE = re.compile(
    r"(?<![\w-])(blast\s+radius|load[-\s]bearing|seams?|spines?|surfaces?|halves|half)(?![\w-])",
    re.IGNORECASE)


def _phrase_alt(*phrases: str) -> str:
    """One alternation group from literal phrases, matched across line breaks.

    A block joins its lines with "\\n", so a phrase can straddle two lines.
    Every space becomes ``\\s+`` so the match survives the wrap.

    The caller controls precedence. Python alternation is leftmost-first,
    not longest. A phrase must precede any shorter phrase it contains.
    """
    return "|".join(p.replace(" ", r"\s+") for p in phrases)


# Filler and hedging from the standard. Longer phrases first: "due to the
# fact that" has to win over "the fact that".
FILLER_RE = re.compile(
    r"(?<![\w-])(" + _phrase_alt(
        "due to the fact that",
        "at this point in time",
        "as a matter of fact",
        "at the end of the day",
        "in the event that",
        "for the purpose of",
        "in the process of",
        "needless to say",
        "in order to",
        "in terms of",
        "the fact that",
        "a number of",
    ) + r"|it\s+(?:is|should\s+be)\s+(?:important\s+to\s+note|noted|worth\s+noting)(?:\s+that)?"
    r"|it\s+could\s+be\s+argued(?:\s+that)?"
    r"|ha(?:s|ve|d)\s+the\s+ability\s+to"
    r"|(?:could|may|might)\s+(?:potentially|possibly)"
    r")(?![\w-])", re.IGNORECASE)

# Copula avoidance: an elaborate verb standing in for "is", "are", or "has".
# "a" and "an" gate the transitive hits so the nouns ("the marks", "two
# features") do not match on their own.
COPULA_RE = re.compile(
    r"(?<![\w-])("
    r"(?:serves?|served|stands?|stood|functions?|functioned)\s+as"
    r"|(?:represents?|represented|marks?|marked|features?|featured)\s+an?"
    r"|boasts?|boasted"
    r")(?![\w-])", re.IGNORECASE)

# Ceremony that sounds decisive and names nothing. Two shapes hit most
# often. One claims to cut through to a deeper truth. The other announces
# the writing instead of doing it.
EMPTY_PHRASE_RE = re.compile(
    r"(?<![\w-])(" + _phrase_alt(
        "the real question is",
        "the deeper issue",
        "the heart of the matter",
        "what really matters",
        "at its core",
        "in reality",
        "fundamentally",
        "here is what you need to know",
        "here's what you need to know",
        "without further ado",
        "let us dive in",
    ) + r"|let'?s\s+(?:dive\s+in|explore|look\s+at|break\s+(?:this|it)\s+down)"
    r"|now\s+let'?s\s+(?:look\s+at|turn\s+to)"
    r")(?![\w-])", re.IGNORECASE)

# A dash the standard does not want, written as something other than U+2014.
# All three arms use lookarounds, so group 1 is the dash by itself. Digit
# ranges ("2020-2024" with an en-dash) stay legal and never match.
DASH_SUBSTITUTE_RE = re.compile(
    r"((?<=\s)–(?=\s)"          # spaced en-dash
    r"|(?<=[A-Za-z])–(?=[A-Za-z])"  # letter-glued en-dash
    r"|(?<=\s)--(?=\s))")            # spaced double hyphen

# Prose that narrates a change instead of describing the thing as it is.
# Legal in a changelog, a release note, or a migration guide. The model
# rules on which document it is reading.
DIFF_ANCHORED_RE = re.compile(
    r"(?<![\w-])(" + _phrase_alt(
        "instead of the old",
        "the previous approach",
        "the previous version",
        "the previous implementation",
        "the old approach",
        "the old version",
        "the old implementation",
        "as of this commit",
        "in this pr",
        "this change",
        "used to be",
        "previously",
        "formerly",
    ) + r"|(?:was|were)\s+(?:added|removed|renamed|replaced)"
    r"|(?:has|have)\s+been\s+(?:replaced|renamed|removed)"
    r"|we\s+(?:changed|replaced|removed|renamed)"
    r")(?![\w-])", re.IGNORECASE)

# The order the scanner reports candidate hits in, per sentence.
CANDIDATE_PATTERNS: tuple[tuple[CandidateKind, re.Pattern[str]], ...] = (
    ("banned-token", BANNED_RE),
    ("filler-phrase", FILLER_RE),
    ("copula-avoidance", COPULA_RE),
    ("empty-phrase", EMPTY_PHRASE_RE),
    ("dash-substitute", DASH_SUBSTITUTE_RE),
    ("diff-anchored", DIFF_ANCHORED_RE),
)

# Tokens like "e.g." must not end a sentence. Compared lowercase, dots kept.
ABBREVIATIONS = {"e.g", "i.e", "etc", "vs", "cf", "ca", "approx", "resp",
                 "incl", "vol", "no", "fig", "eq", "sec", "ch", "pp",
                 "st", "dr", "mr", "mrs", "ms", "jr", "sr"}
TERMINATORS = ".!?"
# Characters that may follow the terminator and stay inside the sentence:
# quotes, bold/italic markers, closing brackets.
CLOSERS = "\"'`*_)]}”’"
CODE_SPAN_RE = re.compile(r"`[^`\n]+`")


class FileType(NamedTuple):
    """How a file is read. Mode "comment" edits comment prose only; mode
    "prose" treats the whole file as prose."""

    language: Language
    mode: Mode


EXTENSIONS: dict[str, FileType] = {
    ".py": FileType("python", "comment"),
    ".js": FileType("javascript", "comment"),
    ".jsx": FileType("javascript", "comment"),
    ".ts": FileType("javascript", "comment"),
    ".tsx": FileType("javascript", "comment"),
    ".mjs": FileType("javascript", "comment"),
    ".cjs": FileType("javascript", "comment"),
    ".sh": FileType("shell", "comment"),
    ".bash": FileType("shell", "comment"),
    ".zsh": FileType("shell", "comment"),
    ".md": FileType("markdown", "prose"),
    ".markdown": FileType("markdown", "prose"),
    ".txt": FileType("text", "prose"),
}


@dataclass
class Block:
    kind: BlockKind
    lines: list[ProseLine]
    skip_reason: SkipReason | None = None


def split_sentences(text: str) -> list[Sentence]:
    """Return (offset, sentence) pairs.

    Three quirks the standard's own prose needs (seen in PR #185):
    a bold lead-in ending ``.**`` terminates its sentence, closing quotes
    stay attached to their sentence, and abbreviations never split.
    """
    out: list[Sentence] = []
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


def _push_sentence(out: list[Sentence], text: str, start: int, end: int) -> None:
    chunk = text[start:end]
    lead = len(chunk) - len(chunk.lstrip())
    stripped = chunk.strip()
    if stripped:
        out.append(Sentence(start + lead, stripped))


def _is_abbreviation(text: str, dot: int) -> bool:
    if text[dot] != ".":
        return False
    k = dot - 1
    chars: list[str] = []
    while k >= 0 and (text[k].isalnum() or text[k] == "."):
        chars.append(text[k])
        k -= 1
    raw = "".join(reversed(chars)).strip(".")
    if not raw:
        return False
    # A single letter is an initial ("Ask J. Smith") only when capitalized.
    # A lowercase one is the tail of a possessive ("the baseline's."), which
    # does end the sentence.
    if len(raw) == 1:
        return raw.isupper()
    return raw.lower() in ABBREVIATIONS


def word_count(sentence: str) -> int:
    """Whitespace tokens, with each inline code span collapsed to one word."""
    return len(CODE_SPAN_RE.sub("code", sentence).split())


def _mask_code_spans(text: str) -> str:
    """Same-length text with code spans letter-masked, so their contents
    never trigger em-dash or banned-token hits."""
    return CODE_SPAN_RE.sub(lambda m: "x" * len(m.group(0)), text)


def _matched_token(m: re.Match[str]) -> str:
    """The match, lowercased, with any line break collapsed to one space."""
    return " ".join(m.group(1).lower().split())


def block_text(block: Block) -> str:
    return "\n".join(t for _, t in block.lines)


def _line_for_offset(block: Block, offset: int) -> int:
    for lineno, text in block.lines:
        if offset <= len(text):
            return lineno
        offset -= len(text) + 1
    return block.lines[-1][0] if block.lines else 0


def check_block(block: Block) -> list[Violation]:
    violations: list[Violation] = []
    text = block_text(block)
    # Masked twice on purpose: once per block for the line scan, once per
    # sentence below. A code span can straddle a sentence boundary, for
    # example a backticked snippet containing a ".". Slicing the block-level
    # mask per sentence would not give the same answer.
    masked_all = _mask_code_spans(text)
    for (lineno, _), masked_line in zip(block.lines, masked_all.split("\n")):
        count = masked_line.count(EM_DASH)
        if count:
            violations.append({"kind": "em-dash", "line": lineno, "count": count})
    for offset, sentence in split_sentences(text):
        line = _line_for_offset(block, offset)
        words = word_count(sentence)
        if block.kind != "heading" and words > WORD_CAP:
            violations.append({"kind": "long-sentence", "line": line,
                               "word_count": words, "sentence": sentence})
        # One masked copy, one pass per candidate kind, in table order.
        masked = _mask_code_spans(sentence)
        for kind, pattern in CANDIDATE_PATTERNS:
            for m in pattern.finditer(masked):
                violations.append({"kind": kind, "line": line,
                                   "token": _matched_token(m),
                                   "sentence": sentence})
    return violations


@dataclass(order=True)
class Span:
    """A comment or docstring's place in the source. Ordering is by position
    only: every `sorted(spans)` call means "in source order"."""

    start: int  # character offset, inclusive
    end: int    # character offset, exclusive
    kind: SpanKind = field(compare=False)
    start_line: int = field(compare=False)
    end_line: int = field(compare=False)


def line_starts(source: str) -> list[int]:
    starts = [0]
    pos = 0
    for line in source.split("\n"):
        pos += len(line) + 1
        starts.append(pos)
    return starts


def _offset(starts: list[int], line: int, col: int) -> int:
    return starts[line - 1] + col


def _line_prefix(source: str, offset: int) -> str:
    """The text between the start of ``offset``'s line and ``offset``.

    A comment whose prefix has non-whitespace is a trailing comment. Both
    ``comment_blocks`` (which feeds ``scan``) and ``_widen_comment_span``
    (which feeds ``verify``) ask that question, and they must answer it the
    same way. A disagreement makes a legitimate ``apply`` edit fail
    verification, so the test lives here once.
    """
    return source[source.rfind("\n", 0, offset) + 1:offset]


def _is_trailing_comment(prefix: str) -> bool:
    return bool(prefix.strip())


BOM = "﻿"


def python_spans(source: str) -> list[Span] | None:
    starts = line_starts(source)
    spans: list[Span] = []
    # tokenize accepts a leading BOM; ast.parse rejects it. Strip it for the
    # parse and shift line-1 columns back, so a BOM file is not a false
    # parse failure.
    bom_shift = len(BOM) if source.startswith(BOM) else 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                spans.append(Span(_offset(starts, *tok.start), _offset(starts, *tok.end),
                                  "comment", tok.start[0], tok.end[0]))
        tree = ast.parse(source[bom_shift:])
    except (SyntaxError, tokenize.TokenError, ValueError):
        return None

    def at(line: int, col: int) -> int:
        return _offset(starts, line, col + (bom_shift if line == 1 else 0))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                c = body[0].value
                end_lineno = c.end_lineno if c.end_lineno is not None else c.lineno
                end_col = c.end_col_offset if c.end_col_offset is not None else 0
                spans.append(Span(at(c.lineno, c.col_offset), at(end_lineno, end_col),
                                  "docstring", c.lineno, end_lineno))
    return sorted(spans)


def _widen_comment_span(source: str, span: Span) -> tuple[int, int]:
    """Grow a comment span to the whole line when nothing but whitespace
    precedes it, trailing newline included.

    Splitting one long sentence across more comment lines is what ``apply``
    does. Cutting only the comment text would leave the added indent and
    newline behind. ``verify`` would then read that as a code change.
    Widening to whole lines makes the stripped code depend on the code
    alone. A trailing comment keeps its newline, because its line still
    holds code.
    """
    if span.kind != "comment":
        return span.start, span.end
    prefix = _line_prefix(source, span.start)
    line_start = span.start - len(prefix)
    if _is_trailing_comment(prefix):
        return line_start + len(prefix.rstrip()), span.end
    end = span.end + 1 if source[span.end:span.end + 1] == "\n" else span.end
    return line_start, end


def strip_spans(source: str, spans: list[Span]) -> str:
    """Source with every span sliced out. verify compares these strings."""
    out: list[str] = []
    pos = 0
    for span in sorted(spans):
        start, end = _widen_comment_span(source, span)
        # Clamped because widening can make a full-line comment's span reach
        # back over a preceding span's end. Without it a slice would repeat
        # text already emitted.
        out.append(source[pos:max(pos, start)])
        pos = max(pos, end)
    out.append(source[pos:])
    return "".join(out)


def spans_for(language: str, source: str) -> list[Span] | None:
    """Comment and docstring spans, or None when the source will not parse.

    Raises ValueError for a language with no extractor, which is a caller
    bug rather than a bad input file.
    """
    if language == "python":
        return python_spans(source)
    if language == "javascript":
        return javascript_spans(source)
    if language == "shell":
        return shell_spans(source)
    raise ValueError(f"no span extractor for {language}")


# After these, a "/" starts a regex literal, not division.
_JS_REGEX_KEYWORDS = {"return", "typeof", "instanceof", "in", "of", "new",
                      "delete", "void", "throw", "case", "do", "else",
                      "yield", "await"}
_JS_REGEX_PUNCT = set("=([{,;:!&|?+-*%^~<>")


def javascript_spans(source: str) -> list[Span]:
    """Comment spans via a small lexer.

    Strings, template literals, and regex literals are skipped so their
    contents never read as comments. Two v1 limits are safe because scan and
    verify share this lexer. Template interpolations are treated as template
    text. The regex heuristic is the standard prev-token test.
    """
    spans: list[Span] = []
    i = 0
    n = len(source)
    line = 1
    prev_char = ""   # last significant char outside comments and literals
    prev_word = ""   # last identifier or keyword
    word: list[str] = []
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch == "/" and nxt == "/":
            start, start_line = i, line
            while i < n and source[i] != "\n":
                i += 1
            spans.append(Span(start, i, "comment", start_line, start_line))
            continue
        if ch == "/" and nxt == "*":
            start, start_line = i, line
            i += 2
            while i < n - 1 and not (source[i] == "*" and source[i + 1] == "/"):
                if source[i] == "\n":
                    line += 1
                i += 1
            i = min(i + 2, n)
            spans.append(Span(start, i, "comment", start_line, line))
            prev_char, prev_word, word = "", "", []
            continue
        if ch in "'\"":
            i, line = _js_skip_quoted(source, i, line, ch)
            prev_char, prev_word, word = ch, "", []
            continue
        if ch == "`":
            i, line = _js_skip_quoted(source, i, line, "`", multiline=True)
            prev_char, prev_word, word = "`", "", []
            continue
        if ch == "/":
            regex_here = (prev_char == "" or prev_char in _JS_REGEX_PUNCT
                          or prev_word in _JS_REGEX_KEYWORDS)
            if regex_here:
                i, line = _js_skip_regex(source, i, line)
                prev_char, prev_word, word = "/", "", []
                continue
            prev_char, prev_word, word = ch, "", []
            i += 1
            continue
        if ch.isalnum() or ch in "_$":
            word.append(ch)
            prev_char = ch
            prev_word = "".join(word)
        elif not ch.isspace():
            prev_char, prev_word, word = ch, "", []
        i += 1
    return spans


def _js_skip_quoted(source: str, i: int, line: int, quote: str,
                    multiline: bool = False) -> tuple[int, int]:
    i += 1
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1, line
        if ch == "\n":
            if not multiline:
                return i, line
            line += 1
        i += 1
    return i, line


def _js_skip_regex(source: str, i: int, line: int) -> tuple[int, int]:
    i += 1
    n = len(source)
    in_class = False
    while i < n:
        ch = source[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            return i + 1, line
        elif ch == "\n":
            return i, line
        i += 1
    return i, line


# `<<` or `<<-`, an optional quote, then the delimiter word. `<<<` is a
# here-string and must not match, so the lookahead rejects a third `<`.
HEREDOC_RE = re.compile(r"<<(-?)(?!<)\s*(['\"]?)([A-Za-z_][\w.-]*)\2")


def _skip_heredoc_body(source: str, newline: int, delimiter: str,
                       strip_tabs: bool) -> tuple[int, int]:
    """Consume a heredoc body. Returns (index after it, lines consumed).

    The body is data the script writes out, not script text, so no comment
    span may come from it.
    """
    i = newline + 1
    consumed = 1  # the newline that opened the body
    n = len(source)
    while i <= n:
        end = source.find("\n", i)
        if end == -1:
            end = n
        candidate = source[i:end]
        if (candidate.lstrip("\t") if strip_tabs else candidate).strip() == delimiter:
            return (end + 1 if end < n else n), consumed + (1 if end < n else 0)
        if end >= n:
            return n, consumed
        i = end + 1
        consumed += 1
    return n, consumed


def shell_spans(source: str) -> list[Span]:
    """Comment spans for shell.

    A ``#`` opens a comment when unquoted and at line start or after
    whitespace. Quotes span lines, so quote state carries across newlines.
    A heredoc body is skipped whole. Its lines are content the script emits,
    so a ``#`` there is not a comment and must never be rewritten.
    """
    spans: list[Span] = []
    i = 0
    n = len(source)
    line = 1
    in_single = in_double = False
    at_line_start = True
    prev = ""
    while i < n:
        ch = source[i]
        if ch == "<" and not in_single and not in_double:
            m = HEREDOC_RE.match(source, i)
            if m:
                newline = source.find("\n", m.end())
                if newline == -1:
                    return spans  # opener with no body
                # Lex the rest of the opener line for comments, then jump the
                # body. `cat <<EOF  # note` keeps its trailing comment.
                rest = shell_spans(source[m.end():newline])
                for s in rest:
                    spans.append(Span(s.start + m.end(), s.end + m.end(),
                                      "comment", line, line))
                i, consumed = _skip_heredoc_body(source, newline, m.group(3),
                                                 strip_tabs=bool(m.group(1)))
                line += consumed
                at_line_start = True
                prev = ""
                continue
        if ch == "\n":
            line += 1
            at_line_start = True
            prev = ""
            i += 1
            continue
        if ch == "\\" and not in_single:
            # A backslash escapes the next character, and that character can
            # be the newline. Count it, or every later span reports a line
            # number one too low for each continuation it passed.
            if source[i + 1:i + 2] == "\n":
                line += 1
            i += 2
            prev = ""
            at_line_start = False
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif (ch == "#" and not in_single and not in_double
              and (at_line_start or prev.isspace())):
            start = i
            while i < n and source[i] != "\n":
                i += 1
            spans.append(Span(start, i, "comment", line, line))
            continue
        at_line_start = False
        prev = ch
        i += 1
    return spans


DOCSTRING_OPEN_RE = re.compile(r'^[rRbBuUfF]{0,2}("""|\'\'\'|"|\')')


def _strip_line_marker(text: str, language: str) -> str:
    marker = "//" if language == "javascript" else "#"
    if text.startswith(marker):
        text = text[len(marker):]
        if text.startswith(" "):
            text = text[1:]
    return text.rstrip()


def _block_comment_lines(raw: str, start_line: int) -> list[ProseLine]:
    """Lines of a /* */ comment with delimiters and leading * stripped."""
    out: list[ProseLine] = []
    for k, line in enumerate(raw.split("\n")):
        t = line.strip()
        for prefix in ("/**", "/*"):
            if t.startswith(prefix):
                t = t[len(prefix):].strip()
                break
        if t.endswith("*/"):
            t = t[:-2].strip()
        if t.startswith("*"):
            t = t[1:].strip()
        out.append(ProseLine(start_line + k, t))
    return out


def _docstring_block(source: str, span: Span) -> Block:
    raw = source[span.start:span.end]
    m = DOCSTRING_OPEN_RE.match(raw)
    quote = m.group(1) if m else '"""'
    inner = raw[m.end():] if m else raw
    if inner.endswith(quote):
        inner = inner[: -len(quote)]
    lines = inner.split("\n")
    indents = [len(ln) - len(ln.lstrip()) for ln in lines[1:] if ln.strip()]
    common = min(indents, default=0)
    out = [ProseLine(span.start_line, lines[0].strip())]
    for k, line in enumerate(lines[1:], start=1):
        out.append(ProseLine(span.start_line + k,
                             line[common:].rstrip() if line.strip() else ""))
    return Block("docstring", out)


def comment_blocks(source: str, language: str) -> list[Block] | None:
    """Group spans into prose blocks: line-comment runs merge, trailing
    comments and block comments stand alone, docstrings stand alone."""
    spans = spans_for(language, source)
    if spans is None:
        return None
    blocks: list[Block] = []
    run: list[ProseLine] = []
    # Both stay None until a run opens; `if run` gates every read.
    run_end_line: int | None = None
    run_indent: int | None = None

    def flush_run() -> None:
        nonlocal run
        if run:
            blocks.append(Block("comment", run))
            run = []

    for span in spans:
        if span.kind == "docstring":
            flush_run()
            blocks.append(_docstring_block(source, span))
            continue
        raw = source[span.start:span.end]
        prefix = _line_prefix(source, span.start)
        if raw.startswith("/*"):
            flush_run()
            blocks.append(Block("comment", _block_comment_lines(raw, span.start_line)))
            continue
        text = _strip_line_marker(raw, language)
        if _is_trailing_comment(prefix):
            flush_run()
            blocks.append(Block("comment", [ProseLine(span.start_line, text)]))
            continue
        continues_run = (run_end_line is not None
                         and span.start_line == run_end_line + 1
                         and len(prefix) == run_indent)
        if run and continues_run:
            run.append(ProseLine(span.start_line, text))
        else:
            flush_run()
            run = [ProseLine(span.start_line, text)]
        run_end_line = span.start_line
        run_indent = len(prefix)
    flush_run()
    return blocks


FENCE_RE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})(.*)$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|(\s*:?-{2,}:?\s*\|)+\s*$")
REF_DEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)")
LIST_MARKER_RE = re.compile(r"^(\s*)(?:[-*+]|\d{1,3}[.)])\s+")
BLOCKQUOTE_RE = re.compile(r"^\s{0,3}>\s?")
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


@dataclass
class MarkdownProtected:
    """Regions a prose edit must leave byte-identical. verify compares two
    of these field by field."""

    frontmatter: str = ""
    fences: list[str] = field(default_factory=list)
    code_spans: list[str] = field(default_factory=list)
    link_urls: list[str] = field(default_factory=list)
    table_shapes: list[list[int]] = field(default_factory=list)


def _extract_inline(text: str, protected: MarkdownProtected) -> str:
    """Record code spans and link URLs; return text with link syntax removed."""
    for m in CODE_SPAN_RE.finditer(text):
        protected.code_spans.append(m.group(0))
    # Mask so a link-looking sequence inside a code span is not treated as a
    # link. The fill char is never "]", ")", or whitespace, so LINK_RE cannot
    # match across it.
    masked = _mask_code_spans(text)
    out: list[str] = []
    pos = 0
    for m in LINK_RE.finditer(masked):
        protected.link_urls.append(m.group(2))
        out.append(text[pos:m.start()])
        out.append(text[m.start(1):m.end(1)])
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


class MarkdownParse(NamedTuple):
    blocks: list[Block]
    protected: MarkdownProtected


def markdown_blocks(source: str) -> MarkdownParse:
    lines = source.split("\n")
    protected = MarkdownProtected()
    blocks: list[Block] = []
    para: list[ProseLine] = []
    table_rows: list[int] = []
    i = 0
    if lines and lines[0].strip() == "---":
        j = 1
        while j < len(lines) and lines[j].strip() not in ("---", "..."):
            j += 1
        if j < len(lines):
            protected.frontmatter = "\n".join(lines[: j + 1])
            i = j + 1

    def flush_para() -> None:
        nonlocal para
        if para:
            blocks.append(Block("paragraph", para))
            para = []

    def flush_table() -> None:
        nonlocal table_rows
        if table_rows:
            protected.table_shapes.append(table_rows)
            table_rows = []

    fence_buf: list[str] | None = None
    fence_char = ""
    fence_len = 0
    prev_blank = True
    while i < len(lines):
        raw = lines[i]
        lineno = i + 1
        fence_match = FENCE_RE.match(raw)
        if fence_buf is not None:
            fence_buf.append(raw)
            if (fence_match and fence_match.group(2)[0] == fence_char
                    and len(fence_match.group(2)) >= fence_len
                    and not fence_match.group(3).strip()):
                protected.fences.append("\n".join(fence_buf))
                fence_buf = None
            i += 1
            continue
        if fence_match:
            flush_para()
            flush_table()
            fence_buf = [raw]
            fence_char = fence_match.group(2)[0]
            fence_len = len(fence_match.group(2))
            prev_blank = False
            i += 1
            continue
        if TABLE_ROW_RE.match(raw):
            flush_para()
            cells = raw.strip().strip("|").split("|")
            table_rows.append(len(cells))
            if not TABLE_SEP_RE.match(raw):
                for cell in cells:
                    text = cell.strip()
                    if text:
                        blocks.append(Block("table-cell", [ProseLine(
                            lineno, _extract_inline(text, protected))]))
            prev_blank = False
            i += 1
            continue
        flush_table()
        heading = HEADING_RE.match(raw)
        if heading:
            flush_para()
            blocks.append(Block("heading", [ProseLine(
                lineno, _extract_inline(heading.group(2), protected))]))
            prev_blank = False
            i += 1
            continue
        ref = REF_DEF_RE.match(raw)
        if ref:
            flush_para()
            protected.link_urls.append(ref.group(1))
            prev_blank = False
            i += 1
            continue
        if raw.lstrip().startswith("<!--"):
            flush_para()
            while i < len(lines) and "-->" not in lines[i]:
                i += 1
            i += 1
            prev_blank = False
            continue
        if not raw.strip():
            flush_para()
            prev_blank = True
            i += 1
            continue
        if prev_blank and raw.startswith("    ") and not para:
            chunk = [raw]
            while i + 1 < len(lines) and (lines[i + 1].startswith("    ")
                                          or not lines[i + 1].strip()):
                i += 1
                chunk.append(lines[i])
            protected.fences.append("\n".join(chunk).rstrip("\n"))
            prev_blank = True
            i += 1
            continue
        text = BLOCKQUOTE_RE.sub("", raw)
        text = LIST_MARKER_RE.sub(r"\1", text).strip()
        para.append(ProseLine(lineno, _extract_inline(text, protected)))
        prev_blank = False
        i += 1
    flush_para()
    flush_table()
    if fence_buf is not None:
        protected.fences.append("\n".join(fence_buf))
    return MarkdownParse(blocks, protected)


def text_blocks(source: str) -> list[Block]:
    blocks: list[Block] = []
    para: list[ProseLine] = []
    for idx, raw in enumerate(source.split("\n")):
        if raw.strip():
            para.append(ProseLine(idx + 1, raw.strip()))
        elif para:
            blocks.append(Block("paragraph", para))
            para = []
    if para:
        blocks.append(Block("paragraph", para))
    return blocks


DIRECTIVE_RE = re.compile(r"""(?xi)^\s*(?:
      eslint-(?:disable|enable)\S*
    | biome-ignore \b
    | prettier-ignore \b
    | @?ts-(?:ignore|expect-error|nocheck) \b
    | noqa \b
    | type:\s*ignore
    | pragma \b
    | pylint: | mypy: | ruff: | isort: | flake8:
    | fmt:\s*(?:off|on)
    | shellcheck \b
    | istanbul \s+ ignore \b
    | coverage: | markdownlint- | spellchecker: | cspell:
    | vim: | vi: | emacs:
    )""")
LICENSE_RE = re.compile(r"(?i)\b(copyright|licen[cs]ed?|spdx-license-identifier)\b")
FIELD_HEADER_RE = re.compile(
    r"""(?x)^(?:
        (?:Args|Arguments|Keyword\ Args|Kwargs|Returns?|Yields?|Raises|
           Attributes|Parameters)\s*:\s*$
      | :(?:param|returns?|rtype|raises|type|yields?|ivar|cvar)\b
    )""")
CODE_LINE_RE = re.compile(r"""(?x)
      [;{}]\s*$
    | ^\s*(?:def|class|return|import|from|if|elif|else:|for|while|try:|except
            |const|let|var|function|export|await|print)\b
    | ^\s*[A-Za-z_][\w.\[\]]*\s*(?:=|\+=|-=)\s
    | ^\s*[A-Za-z_][\w.]*\(.*\)\s*$
""")


def refine_block(block: Block, language: str) -> Block:
    """Drop non-prose lines; mark fully non-prose blocks with a skip reason."""
    lines = list(block.lines)
    reasons: list[str] = []
    if lines and lines[0][0] == 1 and lines[0][1].startswith("!"):
        lines = lines[1:]
        reasons.append("shebang")
    kept: list[ProseLine] = []
    in_fence = in_doctest = in_fields = False
    for lineno, text in lines:
        stripped = text.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            reasons.append("fenced-code")
            continue
        if in_fence:
            continue
        # A blank line ends the field list, the same way it ends a doctest.
        # Without this the latch never clears and every later line is
        # dropped. A trailing paragraph then never reaches the scan, and the
        # file reads as clean. Continuation lines of one field are not
        # blank, so they stay dropped.
        if in_fields and not stripped:
            in_fields = False
        if block.kind == "docstring" and FIELD_HEADER_RE.match(stripped):
            in_fields = True
        if language == "javascript" and stripped.startswith("@"):
            in_fields = True
        if in_fields:
            reasons.append("field-list")
            continue
        if stripped.startswith(">>>"):
            in_doctest = True
        if in_doctest:
            if not stripped:
                in_doctest = False
            else:
                reasons.append("doctest")
                continue
        if DIRECTIVE_RE.match(stripped):
            reasons.append("directive")
            continue
        kept.append(ProseLine(lineno, text))
    nonempty = [t for _, t in kept if t.strip()]
    if not nonempty:
        return replace(block, lines=kept,
                       skip_reason=reasons[0] if reasons else "empty")
    if LICENSE_RE.search("\n".join(nonempty)):
        return replace(block, lines=kept, skip_reason="license")
    codeish = sum(1 for t in nonempty if CODE_LINE_RE.search(t))
    if len(nonempty) >= 2 and codeish * 2 >= len(nonempty):
        return replace(block, lines=kept, skip_reason="commented-code")
    return replace(block, lines=kept, skip_reason=None)


def read_source(path: str) -> str | None:
    """Bytes in, exact text out.

    None when the file cannot be read at all (missing, a directory, no
    permission) or is not UTF-8 text. Callers report both as "not-utf8".
    """
    try:
        return Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def empty_report() -> ScanReport:
    totals: dict[str, int] = {kind: 0 for kind in VIOLATION_KINDS}
    totals["files_scanned"] = 0
    totals["files_clean"] = 0
    return {"files": [], "skipped_files": [], "errors": [],
            "totals": totals}  # type: ignore[typeddict-item]


def _blocks_for(source: str, language: Language, mode: Mode) -> list[Block] | None:
    if mode == "comment":
        return comment_blocks(source, language)
    if language == "markdown":
        return markdown_blocks(source).blocks
    return text_blocks(source)


def parse_changed_lines(raw: object) -> dict[str, list[LineRange]]:
    """Validate the {path: [[start, end], ...]} stdin map.

    Raises ValueError naming the offending path. Without this the bad shape
    surfaces later as a bare unpack error with no path attached.
    """
    if not isinstance(raw, dict):
        raise ValueError("--changed-lines expects a JSON object of path -> ranges")
    out: dict[str, list[LineRange]] = {}
    for key, ranges in raw.items():
        if not isinstance(ranges, list):
            raise ValueError(f"{key}: expected a list of [start, end] pairs")
        pairs: list[LineRange] = []
        for item in ranges:
            if (not isinstance(item, list) or len(item) != 2
                    or not all(isinstance(v, int) for v in item)):
                raise ValueError(f"{key}: bad range {item!r}, want [start, end]")
            pairs.append(LineRange(item[0], item[1]))
        out[os.path.normpath(key)] = pairs
    return out


def _overlaps(block: Block, ranges: list[LineRange]) -> bool:
    if not block.lines:
        return False
    lo = block.lines[0].lineno
    hi = block.lines[-1].lineno
    return any(start <= hi and lo <= end for start, end in ranges)


def _scan_file(path: str, blocks: list[Block], file_type: FileType,
               all_blocks: bool) -> FileEntry:
    """Turn one file's refined blocks into its report entry."""
    entry: FileEntry = {"path": path, "language": file_type.language,
                        "mode": file_type.mode, "blocks": [], "skipped_blocks": []}
    for block in blocks:
        start = block.lines[0].lineno if block.lines else 0
        end = block.lines[-1].lineno if block.lines else 0
        if block.skip_reason:
            entry["skipped_blocks"].append(
                {"kind": block.kind, "start_line": start, "end_line": end,
                 "skip_reason": block.skip_reason})
            continue
        violations = check_block(block)
        if violations or all_blocks:
            entry["blocks"].append(
                {"kind": block.kind, "start_line": start, "end_line": end,
                 "violations": violations})
    return entry


def cmd_scan(args: argparse.Namespace) -> int:
    changed: dict[str, list[LineRange]] | None = None
    if args.changed_lines == "-":
        try:
            changed = parse_changed_lines(json.load(sys.stdin))
        except (ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"error": f"bad --changed-lines input: {exc}"}))
            return 2
    report = empty_report()
    for path in args.files:
        ext = Path(path).suffix.lower()
        if ext not in EXTENSIONS:
            report["skipped_files"].append({"path": path, "reason": "unknown-extension"})
            continue
        source = read_source(path)
        if source is None:
            report["skipped_files"].append({"path": path, "reason": "not-utf8"})
            continue
        file_type = EXTENSIONS[ext]
        blocks = _blocks_for(source, file_type.language, file_type.mode)
        if blocks is None:
            report["errors"].append({"path": path, "error": "parse-failed"})
            continue
        report["totals"]["files_scanned"] += 1
        refined = [refine_block(b, file_type.language) for b in blocks]
        if changed is not None:
            ranges = changed.get(os.path.normpath(path), [])
            refined = [b for b in refined if _overlaps(b, ranges)]
        entry = _scan_file(path, refined, file_type, args.all_blocks)
        has_violations = any(b["violations"] for b in entry["blocks"])
        if has_violations or (args.all_blocks
                              and (entry["blocks"] or entry["skipped_blocks"])):
            report["files"].append(entry)
        if has_violations:
            for block in entry["blocks"]:
                for v in block["violations"]:
                    report["totals"][v["kind"]] += 1
        else:
            report["totals"]["files_clean"] += 1
    print(json.dumps(report, indent=2))
    return 0


def _git_toplevel(directory: str, _memo: dict[str, str | None] = {}) -> str | None:
    """The repo root for a directory, asked of git once per directory.

    verify runs over a file list that usually shares one root, and the answer
    cannot change mid-run.
    """
    if directory not in _memo:
        try:
            _memo[directory] = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"], capture_output=True,
                text=True, check=True, cwd=directory).stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            _memo[directory] = None
    return _memo[directory]


def _git_baseline(path: str, ref: str) -> bytes | None:
    # realpath on both sides, because `git rev-parse` resolves symlinks and
    # abspath does not. On macOS /tmp and /var are symlinks into /private.
    # A repo under either would otherwise produce a relpath that escapes the
    # repo, and a false "missing-baseline".
    real = os.path.realpath(path)
    top = _git_toplevel(os.path.dirname(real) or ".")
    if top is None:
        return None
    rel = os.path.relpath(real, os.path.realpath(top))
    # --end-of-options so a ref beginning with "-" is read as a ref, never as
    # a git option. Without it `--ref '--output=/tmp/x HEAD'` makes `git show`
    # write a file, and the PreToolUse hook approves this CLI unprompted.
    proc = subprocess.run(["git", "-C", top, "show", "--end-of-options", f"{ref}:{rel}"],
                          capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


# MarkdownProtected attribute -> the reason its change is reported under.
_PROTECTED_REGIONS: tuple[tuple[str, VerifyReason], ...] = (
    ("frontmatter", "frontmatter-changed"),
    ("fences", "fence-changed"),
    ("code_spans", "code-span-changed"),
    ("link_urls", "link-url-changed"),
    ("table_shapes", "table-shape-changed"),
)


def _verify_one(path: str, base_text: str | None) -> VerifyResult:
    """One file's verdict. Every uncertain case fails: verify never guesses
    in favor of the edit."""
    ext = Path(path).suffix.lower()
    file_type = EXTENSIONS.get(ext)
    mode = file_type.mode if file_type else "unknown"
    result: VerifyResult = {"path": path, "mode": mode, "ok": False}

    def fail(reason: VerifyReason, detail: str | None = None) -> VerifyResult:
        out: VerifyResult = {**result, "reason": reason}
        if detail is not None:
            out["detail"] = detail
        return out

    if file_type is None:
        return fail("unknown-extension")
    current = read_source(path)
    if current is None:
        return fail("not-utf8")
    if base_text is None:
        return fail("missing-baseline")
    if file_type.mode == "comment":
        cur_spans = spans_for(file_type.language, current)
        base_spans = spans_for(file_type.language, base_text)
        if cur_spans is None:
            return fail("parse-failed")
        if base_spans is None:
            return fail("baseline-parse-failed")
        cur_code = strip_spans(current, cur_spans)
        base_code = strip_spans(base_text, base_spans)
        if cur_code != base_code:
            diff_at = next((k for k, (a, b) in enumerate(zip(cur_code, base_code))
                            if a != b), min(len(cur_code), len(base_code)))
            return fail("code-changed",
                        detail=f"first difference at stripped offset {diff_at}")
        return {**result, "ok": True}
    if file_type.language == "markdown":
        cur_p = markdown_blocks(current).protected
        base_p = markdown_blocks(base_text).protected
        for attr, reason in _PROTECTED_REGIONS:
            if getattr(cur_p, attr) != getattr(base_p, attr):
                return fail(reason)
        return {**result, "ok": True}
    return {**result, "ok": True}  # plain text has no protected regions


def cmd_verify(args: argparse.Namespace) -> int:
    if bool(args.ref) == bool(args.baseline):
        print(json.dumps({"error": "pass exactly one of --ref or --baseline"}))
        return 2
    if args.baseline and len(args.files) != 1:
        print(json.dumps({"error": "--baseline takes exactly one file"}))
        return 2
    results: list[VerifyResult] = []
    for path in args.files:
        if args.baseline:
            # read_source covers the missing / unreadable / not-UTF-8 cases
            # the same way it does for the file under test.
            base_text = read_source(args.baseline)
        else:
            base_bytes = _git_baseline(path, args.ref)
            base_text = None
            if base_bytes is not None:
                try:
                    base_text = base_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    base_text = None
        results.append(_verify_one(path, base_text))
    ok = all(r["ok"] for r in results)
    report: VerifyReport = {"results": results, "ok": ok}
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


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
