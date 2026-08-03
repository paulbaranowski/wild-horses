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


if __name__ == "__main__":
    unittest.main()
