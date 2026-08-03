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

    def test_offsets_point_into_text(self):
        text = "First one. Second one."
        for off, s in cli.split_sentences(text):
            self.assertTrue(text[off:].startswith(s.split()[0]))


class TestChecks(unittest.TestCase):
    def _block(self, *lines: str, kind: str = "comment") -> "cli.Block":
        return cli.Block(kind, [(i + 1, t) for i, t in enumerate(lines)])

    def test_long_sentence_flagged(self):
        words = " ".join(["word"] * 21) + "."
        v = cli.check_block(self._block(words))
        self.assertEqual(v[0]["kind"], "long-sentence")
        self.assertEqual(v[0]["word_count"], 21)

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
        v = cli.check_block(self._block("This is the load-bearing part."))
        self.assertEqual(v[0]["kind"], "banned-token")
        self.assertEqual(v[0]["token"], "load-bearing")

    def test_banned_token_plural_and_phrase(self):
        v = cli.check_block(self._block("Seams and the blast radius grow."))
        self.assertEqual({x["token"] for x in v}, {"seams", "blast radius"})

    def test_banned_token_in_code_span_ignored(self):
        self.assertEqual(cli.check_block(self._block("call `surface()` here")), [])

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
    def _kinds(self, src: str) -> list[tuple[str, int]]:
        spans = cli.javascript_spans(src)
        assert spans is not None
        return [(src[s.start:s.end], s.start_line) for s in spans]

    def test_line_and_block_comments(self):
        src = "// lead\nconst x = 1; /* mid */\n"
        self.assertEqual(self._kinds(src), [("// lead", 1), ("/* mid */", 2)])

    def test_comment_markers_inside_strings_ignored(self):
        src = 'const a = "// no";\nconst b = \'/* no */\';\nconst c = `// no ${d}`;\n'
        self.assertEqual(self._kinds(src), [])

    def test_regex_literal_not_a_comment(self):
        src = "const re = /a\\/b/; // real\n"
        self.assertEqual(self._kinds(src), [("// real", 1)])

    def test_division_then_comment(self):
        src = "const x = a / b; // half of it\n"
        self.assertEqual(len(self._kinds(src)), 1)

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
    def _refined(self, lines: list[tuple[int, str]], kind: str = "comment",
                 language: str = "python") -> "cli.Block":
        return cli.refine_block(cli.Block(kind, lines), language)

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


if __name__ == "__main__":
    unittest.main()
