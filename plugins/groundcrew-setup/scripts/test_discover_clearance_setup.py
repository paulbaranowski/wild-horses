#!/usr/bin/env python3
"""Stdlib unittest tests for discover_clearance_setup.py.

Run from anywhere:

    python3 plugins/groundcrew-setup/scripts/test_discover_clearance_setup.py -v

Or via unittest discovery:

    python3 -m unittest discover -s plugins/groundcrew-setup/scripts \
        -p 'test_discover_clearance_setup.py'

Each test sets HOME to an isolated tmpdir so the script never touches the
user's real ~/.config or ~/.cache directories.

Malformed-pid behaviour (test 9): when the pid file contains non-integer
text, daemonPid is null but daemonAgeSeconds is still computed from the
file's mtime (the file exists, so its age is meaningful even if the content
is garbage).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent / "discover_clearance_setup.py"


def run_script(home: Path) -> subprocess.CompletedProcess:
    """Run discover_clearance_setup.py with an isolated HOME."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": os.environ["PATH"]},
        timeout=15,
    )


class TestDiscoverClearanceSetup(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _personal_file(self) -> Path:
        p = self.home / ".config" / "clearance" / "personal-allow-hosts"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _pid_file(self) -> Path:
        p = self.home / ".cache" / "clearance" / "clearance.pid"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _rc_file(self, name: str) -> Path:
        return self.home / name

    # ------------------------------------------------------------------
    # Test 1 — nothing exists → all false / null
    # ------------------------------------------------------------------

    def test_nothing_exists_all_false_null(self) -> None:
        """Empty HOME → all fields are false or null, exit 0, no stderr."""
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        result = json.loads(proc.stdout)
        self.assertEqual(result, {
            "personalFileExists": False,
            "personalFileHasClaudeHosts": False,
            "envExported": False,
            "daemonPid": None,
            "daemonAgeSeconds": None,
        })

    # ------------------------------------------------------------------
    # Test 2 — personal file with downloads.claude.ai
    # ------------------------------------------------------------------

    def test_personal_file_with_claude_hosts(self) -> None:
        """Personal file contains downloads.claude.ai → both file fields true."""
        pf = self._personal_file()
        pf.write_text("downloads.claude.ai\n")
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertTrue(result["personalFileExists"])
        self.assertTrue(result["personalFileHasClaudeHosts"])

    # ------------------------------------------------------------------
    # Test 3 — personal file with a different host
    # ------------------------------------------------------------------

    def test_personal_file_without_claude_hosts(self) -> None:
        """Personal file exists but lists only example.internal → HasClaudeHosts false."""
        pf = self._personal_file()
        pf.write_text("example.internal\napi.mycompany.com\n")
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertTrue(result["personalFileExists"])
        self.assertFalse(result["personalFileHasClaudeHosts"])

    # ------------------------------------------------------------------
    # Test 4 — claude host in a comment doesn't count
    # ------------------------------------------------------------------

    def test_claude_host_only_in_comment_not_counted(self) -> None:
        """Commented-out downloads.claude.ai must not set HasClaudeHosts."""
        pf = self._personal_file()
        pf.write_text("# downloads.claude.ai\nother.host.com\n")
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertTrue(result["personalFileExists"])
        self.assertFalse(result["personalFileHasClaudeHosts"])

    # ------------------------------------------------------------------
    # Test 5 — env exported in ~/.zshrc
    # ------------------------------------------------------------------

    def test_env_exported_in_zshrc(self) -> None:
        """export CLEARANCE_ALLOW_HOSTS_FILES in .zshrc → envExported true."""
        rc = self._rc_file(".zshrc")
        rc.write_text('export CLEARANCE_ALLOW_HOSTS_FILES="/some/path"\n')
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertTrue(result["envExported"])

    # ------------------------------------------------------------------
    # Test 6 — env exported in ~/.bashrc
    # ------------------------------------------------------------------

    def test_env_exported_in_bashrc(self) -> None:
        """export CLEARANCE_ALLOW_HOSTS_FILES in .bashrc → envExported true."""
        rc = self._rc_file(".bashrc")
        rc.write_text('export CLEARANCE_ALLOW_HOSTS_FILES="/another/path"\n')
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertTrue(result["envExported"])

    # ------------------------------------------------------------------
    # Test 7 — rc files exist but don't mention the variable
    # ------------------------------------------------------------------

    def test_env_not_exported(self) -> None:
        """Rc files present but CLEARANCE_ALLOW_HOSTS_FILES absent → envExported false."""
        for name in (".zshrc", ".bash_profile", ".bashrc", ".profile"):
            rc = self._rc_file(name)
            rc.write_text("export PATH=$PATH:/usr/local/bin\n")
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertFalse(result["envExported"])

    # ------------------------------------------------------------------
    # Test 7a — env in a full-line comment is NOT exported
    # ------------------------------------------------------------------

    def test_env_commented_out_not_exported(self) -> None:
        """A commented-out CLEARANCE_ALLOW_HOSTS_FILES line must NOT set envExported."""
        rc = self._rc_file(".zshrc")
        rc.write_text("# export CLEARANCE_ALLOW_HOSTS_FILES=/path\n")
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertFalse(result["envExported"])

    # ------------------------------------------------------------------
    # Test 7b — env with inline trailing comment IS exported
    # ------------------------------------------------------------------

    def test_env_with_inline_trailing_comment_still_exported(self) -> None:
        """export CLEARANCE_ALLOW_HOSTS_FILES with inline trailing comment → envExported true."""
        rc = self._rc_file(".zshrc")
        rc.write_text('export CLEARANCE_ALLOW_HOSTS_FILES="/path"  # comment\n')
        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertTrue(result["envExported"])

    # ------------------------------------------------------------------
    # Test 8 — pid file with valid pid and known mtime
    # ------------------------------------------------------------------

    def test_pid_file_reports_live_pid_and_age(self) -> None:
        """Pid file with a LIVE pid → daemonPid set; age within ±5 s of mtime delta.

        Uses os.getpid() so the kill(pid, 0) liveness check inside the script
        passes against a process that's guaranteed to exist (the test runner).
        """
        live_pid = os.getpid()
        pf = self._pid_file()
        pf.write_text(f"{live_pid}\n")
        now = time.time()
        offset = 3600  # 1 hour ago
        os.utime(str(pf), (now - offset, now - offset))

        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertEqual(result["daemonPid"], live_pid)
        self.assertIsNotNone(result["daemonAgeSeconds"])
        self.assertAlmostEqual(result["daemonAgeSeconds"], offset, delta=5)

    def test_pid_file_reports_dead_pid_as_null(self) -> None:
        """Pid file with a DEAD pid → daemonPid null; daemonAgeSeconds still set from mtime.

        Regression for the reviewer-flagged issue where a stale pid file from a
        crashed daemon was reported as if the daemon were running, leading the
        skill to offer `kill <dead-pid>` (no-op at best; killing a recycled pid
        at worst).
        """
        # 99999 is high enough that it's unlikely to be in use; if it happens to be,
        # iterate until we find a dead one. PID 0 would be invalid; PIDs >= 99999 are
        # rare on macOS but possible.
        dead_pid = 99999
        while True:
            try:
                os.kill(dead_pid, 0)
                # Process exists; pick another.
                dead_pid += 1
            except ProcessLookupError:
                break
            except PermissionError:
                # Process exists but we can't signal it; still alive.
                dead_pid += 1
        pf = self._pid_file()
        pf.write_text(f"{dead_pid}\n")
        now = time.time()
        offset = 7200  # 2 hours ago
        os.utime(str(pf), (now - offset, now - offset))

        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertIsNone(
            result["daemonPid"],
            f"dead pid {dead_pid} should be reported as null daemonPid",
        )
        self.assertIsNotNone(result["daemonAgeSeconds"])
        self.assertAlmostEqual(result["daemonAgeSeconds"], offset, delta=5)

    # ------------------------------------------------------------------
    # Test 9 — malformed pid → daemonPid null, daemonAgeSeconds still set
    # ------------------------------------------------------------------

    def test_pid_file_malformed_pid_null(self) -> None:
        """Pid file with non-integer content → daemonPid null; daemonAgeSeconds still computed.

        Choice: daemonAgeSeconds is derived from the file's mtime, which is
        independent of the file's content.  Even when the pid is unparseable
        the file clearly exists, so its age is still useful to Phase 5 (it can
        warn about a stale daemon state).  We therefore assert daemonPid is null
        but daemonAgeSeconds is a non-negative integer.
        """
        pf = self._pid_file()
        pf.write_text("not-a-number\n")
        now = time.time()
        os.utime(str(pf), (now - 60, now - 60))

        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertIsNone(result["daemonPid"])
        # daemonAgeSeconds still computed from mtime
        self.assertIsNotNone(result["daemonAgeSeconds"])
        self.assertAlmostEqual(result["daemonAgeSeconds"], 60, delta=5)

    # ------------------------------------------------------------------
    # Test 10 — always exits zero when fully populated
    # ------------------------------------------------------------------

    def test_always_exits_zero_fully_populated(self) -> None:
        """Fully-populated HOME (all files present) → exit 0."""
        # personal file
        pf = self._personal_file()
        pf.write_text("downloads.claude.ai\n")
        # rc file
        rc = self._rc_file(".zshrc")
        rc.write_text('export CLEARANCE_ALLOW_HOSTS_FILES="/x"\n')
        # pid file — use the test runner's pid so the liveness check inside the
        # script reports a live process.
        live_pid = os.getpid()
        pid_p = self._pid_file()
        pid_p.write_text(f"{live_pid}\n")

        proc = run_script(self.home)
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertTrue(result["personalFileExists"])
        self.assertTrue(result["personalFileHasClaudeHosts"])
        self.assertTrue(result["envExported"])
        self.assertEqual(result["daemonPid"], live_pid)
        self.assertIsNotNone(result["daemonAgeSeconds"])


if __name__ == "__main__":
    unittest.main()
