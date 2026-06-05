#!/usr/bin/env python3
"""`plan-keeper upgrade` orchestration (upgrade.py).

Part of the plan_keeper test suite; shared harness lives in support.py.
Run all: python3 -m unittest discover -s plugins/plan-keeper/scripts/tests

The orchestration (run_upgrade) is driven with fake `which`/stream/capture
runners and an in-memory `out`, so no real `brew` or `plan-keeper` binary is
touched. Each test asserts both the printed report and the exit code, plus the
exact command sequence the orchestration issued.
"""
import io
import unittest

from plan_keeper.upgrade import TAP_INSTALL, _parse_version, run_upgrade


class FakeProc:
    """Records every command and answers from scripted tables.

    `which_table` maps an executable name to a resolved path (or None to mean
    'absent'). `capture_table` maps a command tuple to (rc, output). `stream_rc`
    maps a command tuple to an exit code (default 0). Every stream/capture call
    is appended to `calls` so tests can assert ordering.
    """

    def __init__(self, *, which_table=None, capture_table=None, stream_rc=None):
        self.which_table = which_table or {}
        self.capture_table = capture_table or {}
        self.stream_rc = stream_rc or {}
        self.calls = []

    def which(self, name):
        return self.which_table.get(name)

    def stream(self, cmd):
        cmd = tuple(cmd)
        self.calls.append(cmd)
        return self.stream_rc.get(cmd, 0)

    def capture(self, cmd):
        cmd = tuple(cmd)
        self.calls.append(cmd)
        return self.capture_table.get(cmd, (0, ""))


# A `which` table for the happy path: both brew and plan-keeper resolve.
HEALTHY_WHICH = {
    "brew": "/opt/homebrew/bin/brew",
    "plan-keeper": "/opt/homebrew/bin/plan-keeper",
}


class TestParseVersion(unittest.TestCase):
    def test_pulls_trailing_token(self):
        self.assertEqual(_parse_version("plan-keeper 5.3.0"), "5.3.0")

    def test_empty_is_none(self):
        self.assertIsNone(_parse_version("   "))


class TestUpgradeGuards(unittest.TestCase):
    """The two 'not a brew install' refusal paths."""

    def test_no_brew_warns_and_stops(self):
        fake = FakeProc(which_table={"brew": None})
        out = io.StringIO()
        rc = run_upgrade(
            old_version="5.3.0", which=fake.which, stream=fake.stream,
            capture=fake.capture, out=out,
        )
        self.assertEqual(rc, 1)
        self.assertIn("not a Homebrew install", out.getvalue())
        self.assertIn(TAP_INSTALL, out.getvalue())
        # Refused before issuing any brew/crew command.
        self.assertEqual(fake.calls, [])

    def test_brew_present_but_formula_not_owned_warns_and_stops(self):
        fake = FakeProc(
            which_table={"brew": "/opt/homebrew/bin/brew"},
            capture_table={("brew", "list", "--versions", "plan-keeper"): (1, "")},
        )
        out = io.StringIO()
        rc = run_upgrade(
            old_version="5.3.0", which=fake.which, stream=fake.stream,
            capture=fake.capture, out=out,
        )
        self.assertEqual(rc, 1)
        self.assertIn("git pull", out.getvalue())
        self.assertIn(TAP_INSTALL, out.getvalue())
        # Only the ownership probe ran; no upgrade was attempted.
        self.assertEqual(
            fake.calls, [("brew", "list", "--versions", "plan-keeper")]
        )


class TestUpgradeHappyPath(unittest.TestCase):
    def test_reports_version_delta_and_rewires_crew(self):
        fake = FakeProc(
            which_table=HEALTHY_WHICH,
            capture_table={
                ("brew", "list", "--versions", "plan-keeper"): (0, "plan-keeper 5.3.0"),
                ("/opt/homebrew/bin/plan-keeper", "--version"): (0, "plan-keeper 5.4.0"),
            },
        )
        out = io.StringIO()
        rc = run_upgrade(
            old_version="5.3.0", which=fake.which, stream=fake.stream,
            capture=fake.capture, out=out,
        )
        self.assertEqual(rc, 0)
        self.assertIn("Upgraded plan-keeper: 5.3.0 → 5.4.0", out.getvalue())
        self.assertIn("Groundcrew wiring re-validated.", out.getvalue())
        # brew update → brew upgrade → crew install (via resolved binary).
        self.assertEqual(
            fake.calls,
            [
                ("brew", "list", "--versions", "plan-keeper"),
                ("brew", "update"),
                ("brew", "upgrade", "plan-keeper"),
                ("/opt/homebrew/bin/plan-keeper", "crew", "install"),
                ("/opt/homebrew/bin/plan-keeper", "--version"),
            ],
        )

    def test_already_current_is_success(self):
        fake = FakeProc(
            which_table=HEALTHY_WHICH,
            capture_table={
                ("brew", "list", "--versions", "plan-keeper"): (0, "plan-keeper 5.3.0"),
                ("/opt/homebrew/bin/plan-keeper", "--version"): (0, "plan-keeper 5.3.0"),
            },
        )
        out = io.StringIO()
        rc = run_upgrade(
            old_version="5.3.0", which=fake.which, stream=fake.stream,
            capture=fake.capture, out=out,
        )
        self.assertEqual(rc, 0)
        self.assertIn("already up to date (5.3.0)", out.getvalue())


class TestUpgradeFailures(unittest.TestCase):
    def test_brew_update_failure_aborts_before_upgrade(self):
        fake = FakeProc(
            which_table=HEALTHY_WHICH,
            capture_table={
                ("brew", "list", "--versions", "plan-keeper"): (0, "plan-keeper 5.3.0"),
            },
            stream_rc={("brew", "update"): 1},
        )
        out = io.StringIO()
        rc = run_upgrade(
            old_version="5.3.0", which=fake.which, stream=fake.stream,
            capture=fake.capture, out=out,
        )
        self.assertEqual(rc, 1)
        self.assertIn("`brew update` failed", out.getvalue())
        # Never reached `brew upgrade`.
        self.assertNotIn(("brew", "upgrade", "plan-keeper"), fake.calls)

    def test_brew_upgrade_failure_returns_its_code(self):
        fake = FakeProc(
            which_table=HEALTHY_WHICH,
            capture_table={
                ("brew", "list", "--versions", "plan-keeper"): (0, "plan-keeper 5.3.0"),
            },
            stream_rc={("brew", "upgrade", "plan-keeper"): 17},
        )
        out = io.StringIO()
        rc = run_upgrade(
            old_version="5.3.0", which=fake.which, stream=fake.stream,
            capture=fake.capture, out=out,
        )
        self.assertEqual(rc, 17)
        self.assertIn("`brew upgrade plan-keeper` failed", out.getvalue())

    def test_crew_install_failure_surfaces_after_upgrade(self):
        fake = FakeProc(
            which_table=HEALTHY_WHICH,
            capture_table={
                ("brew", "list", "--versions", "plan-keeper"): (0, "plan-keeper 5.3.0"),
                ("/opt/homebrew/bin/plan-keeper", "--version"): (0, "plan-keeper 5.4.0"),
            },
            stream_rc={("/opt/homebrew/bin/plan-keeper", "crew", "install"): 2},
        )
        out = io.StringIO()
        rc = run_upgrade(
            old_version="5.3.0", which=fake.which, stream=fake.stream,
            capture=fake.capture, out=out,
        )
        # Upgrade succeeded, but the crew-install failure is not swallowed:
        # the exit code reflects it and the version delta is still reported.
        self.assertEqual(rc, 2)
        self.assertIn("Upgraded plan-keeper: 5.3.0 → 5.4.0", out.getvalue())
        self.assertIn("groundcrew wiring may be stale", out.getvalue())


if __name__ == "__main__":
    unittest.main()
