#!/usr/bin/env python3
"""The shared PreToolUse adapter, `scripts/hook_runtime.sh`.

Two things are checked here, and they belong together. The first is that every
vendored copy is byte-identical. An installed plugin ships alone, so it cannot
source a sibling's copy. The second is the adapter's contract: which harness a
payload came from, and which decision shape that harness is sent.

This test spans plugins, so it lives at the repo root rather than inside any
one of them.

Run: python3 -m unittest discover -s tests
"""
import json
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS = REPO_ROOT / "plugins"

# Every plugin whose allow-script runs through the adapter.
VENDORED = sorted(PLUGINS.glob("*/scripts/hook_runtime.sh"))

CLAUDE_SHAPE = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": "ok",
    }
}
CURSOR_SHAPE = {"permission": "allow", "agent_message": "ok"}
GROK_SHAPE = {"decision": "allow", "reason": "ok"}


def run_adapter(payload: dict, reason: str = "ok") -> tuple[str, str]:
    """Source one copy of the adapter, feed it a payload, return (runtime, stdout)."""
    script = (
        f'source "{VENDORED[0]}"\n'
        'hook_runtime_init "$1"\n'
        'printf "%s\\n" "$HOOK_RUNTIME"\n'
        f'hook_runtime_emit_allow "{reason}"\n'
    )
    done = subprocess.run(
        ["bash", "-c", script, "bash", json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert done.returncode == 0, done.stderr
    runtime, _, rest = done.stdout.partition("\n")
    return runtime, rest.strip()


def command_of(payload: dict) -> str:
    """Return the command the adapter extracted from a payload."""
    script = (
        f'source "{VENDORED[0]}"\n'
        'hook_runtime_init "$1"\n'
        'printf "%s" "$HOOK_COMMAND"\n'
    )
    done = subprocess.run(
        ["bash", "-c", script, "bash", json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


class TestVendoredCopiesMatch(unittest.TestCase):
    """One authored file, six shipped copies."""

    def test_every_plugin_that_needs_it_has_a_copy(self):
        with_allow_script = {
            p.parent.parent.name for p in PLUGINS.glob("*/scripts/*-allow.sh")
        }
        # codepath-visualizer ships an allow-script that no hooks.json wires up,
        # so it never reaches the adapter and carries no copy.
        with_allow_script.discard("codepath-visualizer")
        self.assertEqual({p.parent.parent.name for p in VENDORED}, with_allow_script)

    def test_all_copies_are_byte_identical(self):
        self.assertTrue(VENDORED, "no vendored copies found")
        first = VENDORED[0].read_bytes()
        for copy in VENDORED[1:]:
            self.assertEqual(
                copy.read_bytes(),
                first,
                f"{copy.relative_to(REPO_ROOT)} has drifted from "
                f"{VENDORED[0].relative_to(REPO_ROOT)}",
            )


class TestRuntimeDetection(unittest.TestCase):
    """Detection reads a field the harness in question actually sends."""

    def test_claude_sends_pascal_case_under_a_snake_case_key(self):
        runtime, _ = run_adapter({"hook_event_name": "PreToolUse"})
        self.assertEqual(runtime, "claude")

    def test_cursor_sends_camel_case_under_a_snake_case_key(self):
        runtime, _ = run_adapter({"hook_event_name": "preToolUse"})
        self.assertEqual(runtime, "cursor")

    def test_grok_sends_a_camel_case_key(self):
        runtime, _ = run_adapter({"hookEventName": "pre_tool_use"})
        self.assertEqual(runtime, "grok")

    def test_an_unrecognised_payload_is_not_guessed_at(self):
        """No harness is the fall-through, so an unknown one gets silence."""
        runtime, out = run_adapter({"tool_input": {"command": "x"}})
        self.assertEqual(runtime, "unknown")
        self.assertEqual(out, "")


class TestCommandExtraction(unittest.TestCase):
    """Grok camelCases the command field too, not just the event field."""

    def test_snake_case_command(self):
        self.assertEqual(
            command_of({"hook_event_name": "PreToolUse",
                        "tool_input": {"command": "python3 a.py"}}),
            "python3 a.py",
        )

    def test_camel_case_command(self):
        self.assertEqual(
            command_of({"hookEventName": "pre_tool_use",
                        "toolInput": {"command": "python3 a.py"}}),
            "python3 a.py",
        )

    def test_a_payload_with_no_command_yields_empty(self):
        self.assertEqual(command_of({"hook_event_name": "PreToolUse"}), "")


class TestDecisionShapes(unittest.TestCase):
    """Each harness is answered in its own dialect."""

    def test_claude_shape(self):
        _, out = run_adapter({"hook_event_name": "PreToolUse"})
        self.assertEqual(json.loads(out), CLAUDE_SHAPE)

    def test_cursor_shape(self):
        _, out = run_adapter({"hook_event_name": "preToolUse"})
        self.assertEqual(json.loads(out), CURSOR_SHAPE)

    def test_grok_shape(self):
        """Grok's decision struct holds exactly `decision` and `reason`."""
        _, out = run_adapter({"hookEventName": "pre_tool_use"})
        self.assertEqual(json.loads(out), GROK_SHAPE)

    def test_grok_is_not_sent_either_of_the_other_two_shapes(self):
        _, out = run_adapter({"hookEventName": "pre_tool_use"})
        self.assertNotIn("hookSpecificOutput", out)
        self.assertNotIn("permission", out)

    def test_a_quote_in_the_reason_cannot_break_the_json(self):
        for payload in ({"hook_event_name": "PreToolUse"},
                        {"hook_event_name": "preToolUse"},
                        {"hookEventName": "pre_tool_use"}):
            with self.subTest(payload=payload):
                _, out = run_adapter(payload, reason="it is \\\"quoted\\\"")
                json.loads(out)  # raises if the reason escaped its string


if __name__ == "__main__":
    unittest.main()
