#!/usr/bin/env python3
"""Tests for plain_language_cli.py. Run by path: python3 <this file>."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import get_args

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plain_language_cli as cli

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plain_language_cli.py")


def run_cli(args: list[str], stdin: str | None = None,
            cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, CLI, *args],
        input=stdin, capture_output=True, text=True, cwd=cwd,
    )


class TestFileTyping(unittest.TestCase):
    def test_extension_map_modes(self):
        self.assertEqual(cli.EXTENSIONS[".py"], ("python", "comment"))
        self.assertEqual(cli.EXTENSIONS[".ts"], ("javascript", "comment"))
        self.assertEqual(cli.EXTENSIONS[".sh"], ("shell", "comment"))
        self.assertEqual(cli.EXTENSIONS[".md"], ("markdown", "prose"))
        self.assertEqual(cli.EXTENSIONS[".txt"], ("text", "prose"))

    def test_scan_skips_unknown_extension(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.rs"
            p.write_text("// hi\n")
            proc = run_cli(["scan", str(p)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["skipped_files"], [
                {"path": str(p), "reason": "unknown-extension"}])

    def test_scan_skips_non_utf8(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.txt"
            p.write_bytes(b"\xff\xfe\x00bad")
            report = json.loads(run_cli(["scan", str(p)]).stdout)
            self.assertEqual(report["skipped_files"][0]["reason"], "not-utf8")


class TestSentences(unittest.TestCase):
    def test_plain_split(self):
        got = [s for _, s in cli.split_sentences("One is here. Two is here.")]
        self.assertEqual(got, ["One is here.", "Two is here."])

    def test_bold_leadin_terminates(self):
        text = "**Don't re-invoke steps.** The runner does that."
        got = [s for _, s in cli.split_sentences(text)]
        self.assertEqual(got, ["**Don't re-invoke steps.**", "The runner does that."])

    def test_quote_swallows_punctuation(self):
        got = [s for _, s in cli.split_sentences('He said "stop." Then he left.')]
        self.assertEqual(got, ['He said "stop."', "Then he left."])

    def test_eg_does_not_split(self):
        got = [s for _, s in cli.split_sentences("Use a tag, e.g. v1, for that.")]
        self.assertEqual(len(got), 1)

    def test_initial_does_not_split(self):
        got = [s for _, s in cli.split_sentences("Ask J. Smith about it.")]
        self.assertEqual(len(got), 1)

    def test_possessive_ends_a_sentence(self):
        # "baseline's." must terminate: the trailing "s" is a possessive,
        # not an initial like the "J." above.
        got = [s for _, s in cli.split_sentences(
            "It matches the baseline's. Then it stops.")]
        self.assertEqual(got, ["It matches the baseline's.", "Then it stops."])

    def test_offsets_point_into_text(self):
        text = "First one. Second one."
        for off, s in cli.split_sentences(text):
            self.assertTrue(text[off:].startswith(s.split()[0]))


class TestChecks(unittest.TestCase):
    def _block(self, *lines: str, kind: "cli.BlockKind" = "comment") -> "cli.Block":
        return cli.Block(kind, [cli.ProseLine(i + 1, t)
                                for i, t in enumerate(lines)])

    def test_long_sentence_flagged(self):
        words = " ".join(["word"] * 21) + "."
        self.assertEqual(cli.check_block(self._block(words)), [
            {"kind": "long-sentence", "line": 1, "word_count": 21,
             "sentence": words}])

    def test_twenty_words_pass(self):
        words = " ".join(["word"] * 20) + "."
        self.assertEqual(cli.check_block(self._block(words)), [])

    def test_code_span_counts_one_word(self):
        s = ("Run `cli scan --changed-lines - --all-blocks x y z` "
             + " ".join(["w"] * 17) + ".")
        self.assertEqual(cli.check_block(self._block(s)), [])

    def test_em_dash_flagged_per_line(self):
        v = cli.check_block(self._block("a \u2014 b"))
        self.assertEqual(v, [{"kind": "em-dash", "line": 1, "count": 1}])

    def test_em_dash_inside_code_span_ignored(self):
        v = cli.check_block(self._block("see `a\u2014b` here"))
        self.assertEqual(v, [])

    def test_banned_token_candidate(self):
        sentence = "This is the load-bearing part."
        self.assertEqual(cli.check_block(self._block(sentence)), [
            {"kind": "banned-token", "line": 1, "token": "load-bearing",
             "sentence": sentence}])

    def test_banned_token_plural_and_phrase(self):
        v = cli.check_block(self._block("Seams and the blast radius grow."))
        tokens = {x["token"] for x in v if x["kind"] == "banned-token"}
        self.assertEqual(tokens, {"seams", "blast radius"})

    def test_banned_token_in_code_span_ignored(self):
        self.assertEqual(cli.check_block(self._block("call `surface()` here")), [])

    def _tokens(self, kind: str, *lines: str) -> set[str]:
        return {v["token"] for v in cli.check_block(self._block(*lines))
                if "token" in v and v["kind"] == kind}

    def test_filler_phrase_candidate(self):
        sentence = "We cache in order to save time."
        self.assertEqual(cli.check_block(self._block(sentence)), [
            {"kind": "filler-phrase", "line": 1, "token": "in order to",
             "sentence": sentence}])

    def test_filler_longest_phrase_wins(self):
        # "the fact that" sits inside "due to the fact that". One hit, not two.
        self.assertEqual(self._tokens("filler-phrase",
                                      "It failed due to the fact that disk was full."),
                         {"due to the fact that"})

    def test_filler_phrase_across_a_line_break(self):
        self.assertEqual(self._tokens("filler-phrase", "It has the", "ability to run."),
                         {"has the ability to"})

    def test_filler_hedge_pair(self):
        self.assertEqual(self._tokens("filler-phrase", "This could potentially break."),
                         {"could potentially"})

    def test_filler_phrase_in_code_span_ignored(self):
        self.assertEqual(self._tokens("filler-phrase", "pass `--in order to` here"), set())

    def test_filler_substring_of_a_word_ignored(self):
        self.assertEqual(self._tokens("filler-phrase", "The reorder mode is off."), set())

    def test_copula_avoidance_candidate(self):
        sentence = "The cache serves as the fallback."
        self.assertEqual(cli.check_block(self._block(sentence)), [
            {"kind": "copula-avoidance", "line": 1, "token": "serves as",
             "sentence": sentence}])

    def test_copula_bare_boast(self):
        self.assertEqual(self._tokens("copula-avoidance", "The gallery boasts three rooms."),
                         {"boasts"})

    def test_copula_needs_an_article(self):
        # The nouns stay clear: only "features a"/"features an" is a candidate.
        self.assertEqual(self._tokens("copula-avoidance", "Two features remain."), set())
        self.assertEqual(self._tokens("copula-avoidance", "The build features a flag."),
                         {"features a"})

    def test_copula_in_code_span_ignored(self):
        self.assertEqual(self._tokens("copula-avoidance", "call `serves as` here"), set())

    def test_empty_phrase_authority_trope(self):
        self.assertEqual(self._tokens("empty-phrase", "At its core, it caches."),
                         {"at its core"})

    def test_empty_phrase_signposting(self):
        self.assertEqual(self._tokens("empty-phrase", "Let's dive in to the parser."),
                         {"let's dive in"})

    def test_empty_phrase_not_double_counted_with_filler(self):
        # "at the end of the day" belongs to filler alone. One kind claims it.
        v = cli.check_block(self._block("At the end of the day it ships."))
        self.assertEqual([x["kind"] for x in v], ["filler-phrase"])

    def test_dash_substitute_spaced_en_dash(self):
        self.assertEqual(self._tokens("dash-substitute", "the policy – announced late"),
                         {"–"})

    def test_dash_substitute_glued_en_dash(self):
        self.assertEqual(self._tokens("dash-substitute", "a client–server split"),
                         {"–"})

    def test_dash_substitute_spaced_double_hyphen(self):
        self.assertEqual(self._tokens("dash-substitute", "the fix -- long overdue -- landed"),
                         {"--"})

    def test_dash_substitute_leaves_digit_ranges(self):
        self.assertEqual(self._tokens("dash-substitute", "It ran 2020–2024 without a break."),
                         set())

    def test_dash_substitute_leaves_flags_and_hyphens(self):
        self.assertEqual(self._tokens("dash-substitute", "pass --full to a well-known path"),
                         set())

    def test_dash_substitute_in_code_span_ignored(self):
        self.assertEqual(self._tokens("dash-substitute", "run `a -- b` now"), set())

    def test_em_dash_stays_its_own_kind(self):
        # U+2014 is a verdict; the substitutes are candidates. Never merged.
        v = cli.check_block(self._block("a — b and c – d"))
        self.assertEqual(sorted({x["kind"] for x in v}),
                         ["dash-substitute", "em-dash"])

    def test_diff_anchored_narration(self):
        self.assertEqual(self._tokens("diff-anchored", "This helper was added to fix it."),
                         {"was added"})

    def test_diff_anchored_previous_approach(self):
        self.assertEqual(self._tokens("diff-anchored", "It beats the previous approach."),
                         {"the previous approach"})

    def test_diff_anchored_ignores_used_to_as_purpose(self):
        # "used to hold" is a purpose, not a history. Only "used to be" hits.
        self.assertEqual(self._tokens("diff-anchored", "The buffer used to hold results."),
                         set())
        self.assertEqual(self._tokens("diff-anchored", "The buffer used to be smaller."),
                         {"used to be"})

    def test_every_violation_kind_has_a_total(self):
        totals = cli.empty_report()["totals"]
        for kind in cli.VIOLATION_KINDS:
            self.assertIn(kind, totals)

    def test_candidate_patterns_cover_every_candidate_kind(self):
        self.assertEqual({k for k, _ in cli.CANDIDATE_PATTERNS},
                         set(get_args(cli.CandidateKind)))

    def test_heading_exempt_from_cap(self):
        words = " ".join(["word"] * 25)
        self.assertEqual(cli.check_block(self._block(words, kind="heading")), [])

    def test_violation_line_numbers(self):
        long_sentence = " ".join(["word"] * 21) + "."
        v = cli.check_block(self._block("Short one.", long_sentence))
        self.assertEqual(v[0]["line"], 2)


PY_SAMPLE = '''\
#!/usr/bin/env python3
"""Module docstring here."""

# a comment line
x = 1  # trailing comment


def f():
    """Function docstring.

    Second line.
    """
    return "# not a comment"
'''


def py_spans(src: str) -> list["cli.Span"]:
    spans = cli.python_spans(src)
    assert spans is not None
    return spans


def py_blocks(src: str) -> list["cli.Block"]:
    blocks = cli.comment_blocks(src, "python")
    assert blocks is not None
    return blocks


class TestPythonSpans(unittest.TestCase):
    def test_finds_comments_and_docstrings(self):
        kinds = [(s.kind, s.start_line) for s in py_spans(PY_SAMPLE)]
        self.assertIn(("comment", 1), kinds)      # shebang is a comment token
        self.assertIn(("docstring", 2), kinds)
        self.assertIn(("comment", 4), kinds)
        self.assertIn(("comment", 5), kinds)
        self.assertIn(("docstring", 9), kinds)

    def test_string_hash_not_a_comment(self):
        self.assertFalse(any(s.start_line == 13 for s in py_spans(PY_SAMPLE)))

    def test_parse_failure_returns_none(self):
        self.assertIsNone(cli.python_spans("def f(:\n"))

    def test_utf8_bom_still_parses(self):
        # tokenize accepts a leading BOM but ast.parse rejects it, so a valid
        # Windows-authored file must not read as a parse failure.
        src = "﻿# bom comment\nx = 1\n"
        spans = cli.python_spans(src)
        assert spans is not None
        self.assertEqual([src[s.start:s.end] for s in spans], ["# bom comment"])

    def test_utf8_bom_keeps_docstring_offsets(self):
        src = '﻿"""Doc line."""\nx = 1\n'
        spans = cli.python_spans(src)
        assert spans is not None
        doc = [s for s in spans if s.kind == "docstring"]
        self.assertEqual([src[s.start:s.end] for s in doc], ['"""Doc line."""'])

    def test_strip_removes_only_spans(self):
        stripped = cli.strip_spans(PY_SAMPLE, py_spans(PY_SAMPLE))
        self.assertNotIn("Module docstring here.", stripped)
        self.assertNotIn("a comment line", stripped)
        self.assertNotIn("trailing comment", stripped)
        self.assertNotIn("Function docstring.", stripped)
        self.assertIn("x = 1", stripped)
        # The literal keeps its "#" text: only real comment spans are cut.
        self.assertIn('return "# not a comment"', stripped)

    def test_strip_is_stable_under_prose_edit(self):
        edited = PY_SAMPLE.replace("Module docstring here.", "Rewritten. Twice.")
        a = cli.strip_spans(PY_SAMPLE, py_spans(PY_SAMPLE))
        b = cli.strip_spans(edited, py_spans(edited))
        self.assertEqual(a, b)

    def test_strip_is_stable_when_a_comment_gains_lines(self):
        # Splitting one long sentence across more comment lines is the core
        # move of `apply`; it must not read as a code change.
        before = "# one long sentence here\nx = 1\n"
        after = "# one long sentence.\n# Here is the rest.\nx = 1\n"
        self.assertEqual(cli.strip_spans(before, py_spans(before)),
                         cli.strip_spans(after, py_spans(after)))

    def test_strip_is_stable_for_indented_comment_lines(self):
        before = "def f():\n    # a sentence\n    return 1\n"
        after = "def f():\n    # a sentence.\n    # And another.\n    return 1\n"
        self.assertEqual(cli.strip_spans(before, py_spans(before)),
                         cli.strip_spans(after, py_spans(after)))

    def test_strip_keeps_trailing_comment_line_intact(self):
        # A trailing comment's line still holds code, so its newline stays.
        src = "x = 1  # note\ny = 2\n"
        self.assertEqual(cli.strip_spans(src, py_spans(src)), "x = 1\ny = 2\n")

    def test_strip_still_detects_real_code_change(self):
        before = "# c\nx = 1\n"
        after = "# c\n# c2\nx = 2\n"
        self.assertNotEqual(cli.strip_spans(before, py_spans(before)),
                            cli.strip_spans(after, py_spans(after)))


class TestBlockAssembly(unittest.TestCase):
    def test_consecutive_lines_merge(self):
        src = "# one\n# two\n\n# three\nx = 1\n"
        self.assertEqual([b.lines for b in py_blocks(src)],
                         [[(1, "one"), (2, "two")], [(4, "three")]])

    def test_trailing_comment_is_own_block(self):
        src = "# lead\nx = 1  # trail\n"
        self.assertEqual(py_blocks(src)[1].lines, [(2, "trail")])

    def test_indent_change_splits_blocks(self):
        src = "if True:\n    pass\n# a\nif True:\n    # b\n    pass\n"
        self.assertEqual(len(py_blocks(src)), 2)

    def test_docstring_block_dedented(self):
        src = 'def f():\n    """Top line.\n\n    Body line.\n    """\n'
        block = py_blocks(src)[0]
        self.assertEqual(block.kind, "docstring")
        self.assertEqual(block.lines,
                         [(2, "Top line."), (3, ""), (4, "Body line."), (5, "")])

    def test_parse_failure_propagates(self):
        self.assertIsNone(cli.comment_blocks("def f(:\n", "python"))


class TestJavascriptSpans(unittest.TestCase):
    def _comments(self, src: str) -> list[tuple[str, int]]:
        spans = cli.javascript_spans(src)
        assert spans is not None
        return [(src[s.start:s.end], s.start_line) for s in spans]

    def test_line_and_block_comments(self):
        src = "// lead\nconst x = 1; /* mid */\n"
        self.assertEqual(self._comments(src), [("// lead", 1), ("/* mid */", 2)])

    def test_comment_markers_inside_strings_ignored(self):
        src = 'const a = "// no";\nconst b = \'/* no */\';\nconst c = `// no ${d}`;\n'
        self.assertEqual(self._comments(src), [])

    def test_regex_literal_not_a_comment(self):
        src = "const re = /a\\/b/; // real\n"
        self.assertEqual(self._comments(src), [("// real", 1)])

    def test_division_then_comment(self):
        src = "const x = a / b; // half of it\n"
        self.assertEqual(len(self._comments(src)), 1)

    def test_multiline_block(self):
        src = "/*\n * one\n * two\n */\nlet x;\n"
        spans = cli.javascript_spans(src)
        assert spans is not None
        self.assertEqual((spans[0].start_line, spans[0].end_line), (1, 4))

    def test_jsx_and_ts_via_extension_map(self):
        self.assertEqual(cli.EXTENSIONS[".tsx"], ("javascript", "comment"))


class TestShellSpans(unittest.TestCase):
    def _texts(self, src: str) -> list[str]:
        spans = cli.shell_spans(src)
        assert spans is not None
        return [src[s.start:s.end] for s in spans]

    def test_full_line_and_trailing(self):
        src = "# lead\necho hi # trail\n"
        self.assertEqual(self._texts(src), ["# lead", "# trail"])

    def test_hash_in_quotes_ignored(self):
        src = "echo '# no' \"# also no\"\n"
        self.assertEqual(self._texts(src), [])

    def test_param_expansion_ignored(self):
        src = 'echo $# ${#var}\n'
        self.assertEqual(self._texts(src), [])

    def test_no_space_before_hash_ignored(self):
        src = "echo foo#bar\n"
        self.assertEqual(self._texts(src), [])


class TestSkipRules(unittest.TestCase):
    def _refined(self, lines: list[tuple[int, str]], kind: "cli.BlockKind" = "comment",
                 language: str = "python") -> "cli.Block":
        prose = [cli.ProseLine(n, t) for n, t in lines]
        return cli.refine_block(cli.Block(kind, prose), language)

    def test_shebang_skipped(self):
        got = self._refined([(1, "!/usr/bin/env python3")])
        self.assertEqual(got.skip_reason, "shebang")

    def test_directive_lines_dropped(self):
        self.assertEqual(self._refined([(3, "noqa: E501")]).skip_reason, "directive")
        self.assertEqual(
            self._refined([(3, "type: ignore[arg-type]")]).skip_reason, "directive")
        self.assertEqual(
            self._refined([(3, "eslint-disable-next-line no-console")],
                          language="javascript").skip_reason, "directive")

    def test_directive_mixed_with_prose_keeps_prose(self):
        got = self._refined([(1, "Real sentence here."), (2, "pragma: no cover")])
        self.assertIsNone(got.skip_reason)
        self.assertEqual(got.lines, [(1, "Real sentence here.")])

    def test_license_header_skipped(self):
        got = self._refined([(1, "Copyright 2026 Example Corp."),
                             (2, "Licensed under the MIT license.")])
        self.assertEqual(got.skip_reason, "license")

    def test_commented_out_code_skipped(self):
        got = self._refined([(1, "x = compute(1)"), (2, "return x")])
        self.assertEqual(got.skip_reason, "commented-code")

    def test_doctest_dropped_from_docstring(self):
        got = self._refined([(1, "Adds one."), (2, ""), (3, ">>> f(1)"), (4, "2")],
                            kind="docstring")
        self.assertIsNone(got.skip_reason)
        self.assertEqual([t for _, t in got.lines if t.strip()], ["Adds one."])

    def test_field_list_dropped_from_docstring(self):
        got = self._refined([(1, "Does a thing."), (2, ""), (3, "Args:"),
                             (4, "    x: the input")], kind="docstring")
        self.assertEqual([t for _, t in got.lines if t.strip()], ["Does a thing."])
        got = self._refined([(1, "Doc."), (2, ":param x: the input")], kind="docstring")
        self.assertEqual([t for _, t in got.lines if t.strip()], ["Doc."])

    def test_jsdoc_tags_dropped(self):
        got = self._refined([(1, "Fetches a user."), (2, "@param id the id")],
                            language="javascript")
        self.assertEqual([t for _, t in got.lines if t.strip()], ["Fetches a user."])

    def test_fenced_code_in_docstring_dropped(self):
        got = self._refined([(1, "Example below."), (2, "```"), (3, "x = 1"),
                             (4, "```")], kind="docstring")
        self.assertEqual([t for _, t in got.lines if t.strip()], ["Example below."])


MD_SAMPLE = """\
---
title: sample
---

# Heading words beyond twenty would still pass because headings are labels not sentences in this standard truly

Intro paragraph with `code span` and a [link](https://example.com/x).

```python
code = "untouched"
```

- item one continues here
- item two

| Col A | Col B |
| ----- | ----- |
| cell prose | more prose |

[ref]: https://example.com/ref
"""


class TestMarkdownExtraction(unittest.TestCase):
    def setUp(self):
        self.blocks, self.protected = cli.markdown_blocks(MD_SAMPLE)

    def test_frontmatter_protected(self):
        self.assertEqual(self.protected.frontmatter, "---\ntitle: sample\n---")

    def test_fence_protected_and_not_prose(self):
        self.assertEqual(len(self.protected.fences), 1)
        self.assertIn('code = "untouched"', self.protected.fences[0])
        joined = "\n".join(cli.block_text(b) for b in self.blocks)
        self.assertNotIn("untouched", joined)

    def test_heading_kind(self):
        self.assertIn("heading", [b.kind for b in self.blocks])

    def test_link_url_captured_text_kept(self):
        self.assertIn("https://example.com/x", self.protected.link_urls)
        self.assertIn("https://example.com/ref", self.protected.link_urls)
        intro = next(b for b in self.blocks if "Intro" in cli.block_text(b))
        self.assertIn("link", cli.block_text(intro))
        self.assertNotIn("example.com/x", cli.block_text(intro))

    def test_table_cells_and_shape(self):
        cells = [cli.block_text(b) for b in self.blocks if b.kind == "table-cell"]
        self.assertIn("cell prose", cells)
        self.assertEqual(self.protected.table_shapes, [[2, 2, 2]])

    def test_list_markers_stripped(self):
        items = [cli.block_text(b) for b in self.blocks]
        self.assertTrue(any(t.startswith("item one") for t in items))

    def test_line_numbers_are_real(self):
        intro = next(b for b in self.blocks if "Intro" in cli.block_text(b))
        self.assertEqual(intro.lines[0][0], 7)


class TestTextExtraction(unittest.TestCase):
    def test_paragraphs_split_on_blank(self):
        blocks = cli.text_blocks("one one.\n\ntwo two.\n")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[1].lines[0][0], 3)


class TestScanCommand(unittest.TestCase):
    def _scan(self, tree: dict[str, str], args: tuple[str, ...] = ()) -> dict:
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for name, content in tree.items():
                p = Path(d) / name
                p.write_text(content, encoding="utf-8")
                paths.append(str(p))
            proc = run_cli(["scan", *paths, *args])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout)

    def test_python_violations_reported(self):
        long_comment = "# " + " ".join(["word"] * 21) + ".\n"
        report = self._scan({"a.py": long_comment + "x = 1\n"})
        self.assertEqual(report["totals"]["long-sentence"], 1)
        block = report["files"][0]["blocks"][0]
        self.assertEqual(block["violations"][0]["kind"], "long-sentence")

    def test_clean_file_not_listed(self):
        report = self._scan({"a.py": "# Short and fine.\nx = 1\n"})
        self.assertEqual(report["files"], [])
        self.assertEqual(report["totals"]["files_clean"], 1)

    def test_parse_error_reported(self):
        report = self._scan({"bad.py": "def f(:\n"})
        self.assertEqual(report["errors"][0]["error"], "parse-failed")

    def test_markdown_em_dash_found(self):
        report = self._scan({"doc.md": "Uses a dash \u2014 badly.\n"})
        self.assertEqual(report["totals"]["em-dash"], 1)

    def test_changed_lines_filter(self):
        long_comment = "# " + " ".join(["word"] * 21) + ".\n"
        content = long_comment + "x = 1\n" + long_comment
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.py"
            p.write_text(content, encoding="utf-8")
            changed = json.dumps({str(p): [[3, 3]]})
            proc = run_cli(["scan", "--changed-lines", "-", str(p)], stdin=changed)
            report = json.loads(proc.stdout)
        self.assertEqual(report["totals"]["long-sentence"], 1)
        self.assertEqual(report["files"][0]["blocks"][0]["start_line"], 3)

    def test_bad_changed_lines_input_is_named(self):
        # A malformed map must name the offending path, not surface later as
        # a bare unpack error.
        for payload, needle in (
            ('{"a.py": [[3]]}', "a.py"),
            ('{"a.py": 7}', "a.py"),
            ("[1, 2]", "JSON object"),
            ("not json", "bad --changed-lines"),
        ):
            with self.subTest(payload=payload):
                proc = run_cli(["scan", "--changed-lines", "-", "a.py"], stdin=payload)
                self.assertEqual(proc.returncode, 2)
                self.assertIn(needle, json.loads(proc.stdout)["error"])

    def test_changed_lines_accepts_valid_map(self):
        self.assertEqual(
            cli.parse_changed_lines({"./a.py": [[1, 2], [9, 9]]}),
            {"a.py": [cli.LineRange(1, 2), cli.LineRange(9, 9)]})

    def test_skipped_blocks_surface_reasons(self):
        report = self._scan({"a.sh": "#!/bin/sh\n# shellcheck disable=SC2086\necho hi\n"})
        self.assertEqual(report["files"], [])
        report2 = self._scan({"a.sh": "#!/bin/sh\necho hi\n"}, args=("--all-blocks",))
        reasons = [b["skip_reason"]
                   for f in report2["files"] for b in f["skipped_blocks"]]
        self.assertIn("shebang", reasons)


class TestVerifyCommand(unittest.TestCase):
    def _git_repo(self, d: str, files: dict[str, str]) -> None:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "root"], cwd=d, check=True)
        for name, content in files.items():
            Path(d, name).write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=d, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "-m", "base"], cwd=d, check=True)

    def test_comment_edit_passes(self):
        with tempfile.TemporaryDirectory() as d:
            self._git_repo(d, {"a.py": "# old comment words\nx = 1\n"})
            Path(d, "a.py").write_text("# new words. Split here.\nx = 1\n")
            proc = run_cli(["verify", "--ref", "HEAD", "a.py"], cwd=d)
            self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_code_edit_fails(self):
        with tempfile.TemporaryDirectory() as d:
            self._git_repo(d, {"a.py": "# c\nx = 1\n"})
            Path(d, "a.py").write_text("# c\nx = 2\n")
            proc = run_cli(["verify", "--ref", "HEAD", "a.py"], cwd=d)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(json.loads(proc.stdout)["results"][0]["reason"],
                             "code-changed")

    def test_markdown_prose_edit_passes_fence_edit_fails(self):
        md = "Intro text.\n\n```\ncode\n```\n"
        with tempfile.TemporaryDirectory() as d:
            self._git_repo(d, {"doc.md": md})
            Path(d, "doc.md").write_text("Intro text, rewritten.\n\n```\ncode\n```\n")
            self.assertEqual(
                run_cli(["verify", "--ref", "HEAD", "doc.md"], cwd=d).returncode, 0)
            Path(d, "doc.md").write_text("Intro text.\n\n```\nchanged\n```\n")
            proc = run_cli(["verify", "--ref", "HEAD", "doc.md"], cwd=d)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(json.loads(proc.stdout)["results"][0]["reason"],
                             "fence-changed")

    def test_missing_in_ref_fails(self):
        with tempfile.TemporaryDirectory() as d:
            self._git_repo(d, {"a.py": "x = 1\n"})
            Path(d, "new.py").write_text("# hi\n")
            proc = run_cli(["verify", "--ref", "HEAD", "new.py"], cwd=d)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(json.loads(proc.stdout)["results"][0]["reason"],
                             "missing-baseline")

    def test_baseline_file_mode(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "base.py"
            cur = Path(d) / "cur.py"
            base.write_text("# old\nx = 1\n")
            cur.write_text("# new\nx = 1\n")
            self.assertEqual(
                run_cli(["verify", "--baseline", str(base), str(cur)]).returncode, 0)
            cur.write_text("# new\nx = 2\n")
            self.assertEqual(
                run_cli(["verify", "--baseline", str(base), str(cur)]).returncode, 1)

    def test_symlinked_repo_path_still_finds_baseline(self):
        # `git rev-parse` resolves symlinks and abspath does not. On macOS a
        # repo under /tmp or /var lives behind a /private symlink, and the
        # mismatch used to yield a false "missing-baseline".
        with tempfile.TemporaryDirectory() as real_root:
            repo = os.path.join(real_root, "repo")
            os.mkdir(repo)
            self._git_repo(repo, {"a.py": "# old\nx = 1\n"})
            link_root = os.path.join(real_root, "link")
            os.symlink(repo, link_root)
            Path(link_root, "a.py").write_text("# new words.\nx = 1\n")
            proc = run_cli(["verify", "--ref", "HEAD",
                            os.path.join(link_root, "a.py")])
            self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_ref_and_baseline_exclusive(self):
        proc = run_cli(["verify", "--ref", "HEAD", "--baseline", "x", "a.py"])
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
