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
import ast
import io
import json
import re
import sys
import tokenize
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


@dataclass(order=True)
class Span:
    start: int  # character offset, inclusive
    end: int    # character offset, exclusive
    kind: str   # "comment" | "docstring"
    start_line: int
    end_line: int


def line_starts(source: str) -> list[int]:
    starts = [0]
    for idx, ch in enumerate(source):
        if ch == "\n":
            starts.append(idx + 1)
    return starts


def _offset(starts: list[int], line: int, col: int) -> int:
    return starts[line - 1] + col


def python_spans(source: str) -> list[Span] | None:
    starts = line_starts(source)
    spans: list[Span] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                spans.append(Span(_offset(starts, *tok.start), _offset(starts, *tok.end),
                                  "comment", tok.start[0], tok.end[0]))
        tree = ast.parse(source)
    except (SyntaxError, tokenize.TokenError, ValueError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                c = body[0].value
                end_lineno = c.end_lineno if c.end_lineno is not None else c.lineno
                end_col = c.end_col_offset if c.end_col_offset is not None else 0
                spans.append(Span(_offset(starts, c.lineno, c.col_offset),
                                  _offset(starts, end_lineno, end_col),
                                  "docstring", c.lineno, end_lineno))
    return sorted(spans)


def strip_spans(source: str, spans: list[Span]) -> str:
    """Source with every span sliced out. verify compares these strings."""
    out: list[str] = []
    pos = 0
    for span in sorted(spans):
        out.append(source[pos:span.start])
        pos = max(pos, span.end)
    out.append(source[pos:])
    return "".join(out)


def spans_for(language: str, source: str) -> list[Span] | None:
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


def javascript_spans(source: str) -> list[Span] | None:
    """Comment spans via a small lexer.

    Strings, template literals, and regex literals are skipped so their
    contents never read as comments. Known v1 limits, safe because scan and
    verify share this lexer: template interpolations are treated as template
    text, and the regex heuristic is the standard prev-token test.
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


def shell_spans(source: str) -> list[Span] | None:
    """Comment spans for shell.

    A ``#`` opens a comment when unquoted and at line start or after
    whitespace. Quotes span lines, so quote state carries across newlines.
    Heredoc bodies are lexed like ordinary lines; scan and verify share the
    behavior, so the pair stays consistent.
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
        if ch == "\n":
            line += 1
            at_line_start = True
            prev = ""
            i += 1
            continue
        if ch == "\\" and not in_single:
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


def _block_comment_lines(raw: str, start_line: int) -> list[tuple[int, str]]:
    """Lines of a /* */ comment with delimiters and leading * stripped."""
    out: list[tuple[int, str]] = []
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
        out.append((start_line + k, t))
    return out


def _docstring_block(source: str, span: Span) -> Block:
    raw = source[span.start:span.end]
    m = DOCSTRING_OPEN_RE.match(raw)
    quote = m.group(1) if m else '"""'
    inner = raw[m.end():] if m else raw
    if inner.endswith(quote):
        inner = inner[: -len(quote)]
    lines = inner.split("\n")
    indents = [len(l) - len(l.lstrip()) for l in lines[1:] if l.strip()]
    common = min(indents, default=0)
    out = [(span.start_line, lines[0].strip())]
    for k, line in enumerate(lines[1:], start=1):
        out.append((span.start_line + k, line[common:].rstrip() if line.strip() else ""))
    return Block("docstring", out)


def comment_blocks(source: str, language: str) -> list[Block] | None:
    """Group spans into prose blocks: line-comment runs merge, trailing
    comments and block comments stand alone, docstrings stand alone."""
    spans = spans_for(language, source)
    if spans is None:
        return None
    starts = line_starts(source)
    blocks: list[Block] = []
    run: list[tuple[int, str]] = []
    run_end_line = -2
    run_indent = -1

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
        prefix = source[starts[span.start_line - 1]:span.start]
        if raw.startswith("/*"):
            flush_run()
            blocks.append(Block("comment", _block_comment_lines(raw, span.start_line)))
            continue
        text = _strip_line_marker(raw, language)
        if prefix.strip():  # code before the marker: a trailing comment
            flush_run()
            blocks.append(Block("comment", [(span.start_line, text)]))
            run_end_line = -2
            continue
        if run and span.start_line == run_end_line + 1 and len(prefix) == run_indent:
            run.append((span.start_line, text))
        else:
            flush_run()
            run = [(span.start_line, text)]
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
    masked = CODE_SPAN_RE.sub(lambda m: "\x00" * len(m.group(0)), text)
    for m in LINK_RE.finditer(masked):
        protected.link_urls.append(m.group(2))
    out: list[str] = []
    pos = 0
    for m in LINK_RE.finditer(masked):
        out.append(text[pos:m.start()])
        out.append(text[m.start(1):m.end(1)])
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def markdown_blocks(source: str) -> tuple[list[Block], MarkdownProtected]:
    lines = source.split("\n")
    protected = MarkdownProtected()
    blocks: list[Block] = []
    para: list[tuple[int, str]] = []
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
                        blocks.append(Block("table-cell",
                                            [(lineno, _extract_inline(text, protected))]))
            prev_blank = False
            i += 1
            continue
        flush_table()
        heading = HEADING_RE.match(raw)
        if heading:
            flush_para()
            blocks.append(Block("heading",
                                [(lineno, _extract_inline(heading.group(2), protected))]))
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
        para.append((lineno, _extract_inline(text, protected)))
        prev_blank = False
        i += 1
    flush_para()
    flush_table()
    if fence_buf is not None:
        protected.fences.append("\n".join(fence_buf))
    return blocks, protected


def text_blocks(source: str) -> list[Block]:
    blocks: list[Block] = []
    para: list[tuple[int, str]] = []
    for idx, raw in enumerate(source.split("\n")):
        if raw.strip():
            para.append((idx + 1, raw.strip()))
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
    kept: list[tuple[int, str]] = []
    in_fence = in_doctest = in_fields = False
    for lineno, text in lines:
        stripped = text.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            reasons.append("fenced-code")
            continue
        if in_fence:
            continue
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
        kept.append((lineno, text))
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
