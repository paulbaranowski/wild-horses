#!/usr/bin/env python3
"""Stdlib unittest suite for load_existing.py.

Tests invoke the script as a subprocess so exit codes, stdout/stderr
separation, and argument-handling are exercised exactly as a dispatched
agent would see them.

Tests 1–4 (always-runnable) exercise failure-handling paths that do not
require @clipboard-health/groundcrew to be installed: missing args, node
absent from PATH, groundcrew not installed, and a nonexistent config
directory.

Tests 5–6 (conditional) require @clipboard-health/groundcrew to be
importable by node. They are SKIPPED on machines where only a source clone
exists (no built dist/ or global npm install). To run them:

    npm install -g @clipboard-health/groundcrew

Then re-run this suite. Expected result after install: 6 PASS, 0 SKIPPED.

Run from anywhere:

    python3 plugins/groundcrew-setup/tests/test_load_existing.py -v

Or via unittest discovery:

    python3 -m unittest discover -s plugins/groundcrew-setup/tests -p 'test_load_existing.py'
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "load_existing.py"
RENDER_CONFIG = Path(__file__).parent.parent / "scripts" / "render_config.py"

# Use sys.executable so we can call the script even when the test's
# subprocess env strips PATH (e.g. the node-missing test).
_PYTHON3 = sys.executable


def _run_script(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke load_existing.py with the given arguments."""
    cmd = [_PYTHON3, str(SCRIPT)] + list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )


def _groundcrew_available() -> bool:
    """Probe whether @clipboard-health/groundcrew can be loaded by node."""
    try:
        result = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import('@clipboard-health/groundcrew').then(() => process.exit(0)).catch(() => process.exit(1))",
            ],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


class TestLoadExistingAlwaysRunnable(unittest.TestCase):
    """Tests 1–4: always runnable regardless of groundcrew install state."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # Test 1: missing argument → exit 2 (argparse default for required pos arg)
    # ------------------------------------------------------------------
    def test_missing_argument_exits_2(self) -> None:
        """Invoking with no arguments must exit 2 and mention the missing arg on stderr."""
        r = _run_script()
        self.assertEqual(r.returncode, 2, f"expected exit 2, got {r.returncode}; stderr={r.stderr!r}")
        self.assertTrue(
            r.stderr.strip(),
            "expected a non-empty stderr message for missing argument",
        )
        # argparse output: "the following arguments are required: config_path"
        self.assertTrue(
            "argument" in r.stderr.lower() or "usage" in r.stderr.lower() or "required" in r.stderr.lower(),
            f"stderr should mention missing argument; got: {r.stderr!r}",
        )

    # ------------------------------------------------------------------
    # Test 2: node absent from PATH → non-zero, stderr mentions "node"
    # ------------------------------------------------------------------
    def test_node_missing_exits_nonzero_with_message(self) -> None:
        """A PATH with no node binary must cause non-zero exit and stderr mentioning 'node'."""
        empty_dir = str(self.tmpdir / "empty_bin")
        os.makedirs(empty_dir, exist_ok=True)

        # Stripped PATH so shutil.which("node") inside the script returns None.
        # python3 is invoked via absolute sys.executable, so it doesn't need PATH.
        minimal_env = {
            "PATH": empty_dir,
            "HOME": str(Path.home()),
            "TMPDIR": tempfile.gettempdir(),
        }

        r = _run_script("/tmp/any.ts", env=minimal_env)
        self.assertNotEqual(r.returncode, 0, f"expected non-zero exit; stderr={r.stderr!r}")
        self.assertTrue(
            "node" in r.stderr.lower() or "not found" in r.stderr.lower(),
            f"stderr should mention 'node' or 'not found'; got: {r.stderr!r}",
        )

    # ------------------------------------------------------------------
    # Test 3: groundcrew not installed → non-zero, stderr mentions ERR_MODULE_NOT_FOUND
    # ------------------------------------------------------------------
    def test_groundcrew_not_installed_exits_nonzero(self) -> None:
        """When @clipboard-health/groundcrew is not installed, must exit non-zero."""
        if _groundcrew_available():
            self.skipTest(
                "@clipboard-health/groundcrew is installed; "
                "this test covers the not-installed case — skipping."
            )

        config_path = self.tmpdir / "groundcrew.config.ts"
        config_path.write_text("// placeholder\n")

        r = _run_script(str(config_path))
        self.assertNotEqual(r.returncode, 0, f"expected non-zero exit; stderr={r.stderr!r}")
        self.assertTrue(
            r.stderr.strip(),
            "expected a non-empty stderr message",
        )
        stderr_lower = r.stderr.lower()
        self.assertTrue(
            "ERR_MODULE_NOT_FOUND" in r.stderr
            or "cannot find package" in stderr_lower
            or "not found" in stderr_lower,
            f"stderr should indicate the package is missing; got: {r.stderr!r}",
        )

    # ------------------------------------------------------------------
    # Test 4: nonexistent config dir → non-zero
    # ------------------------------------------------------------------
    def test_nonexistent_config_path_exits_nonzero(self) -> None:
        """Invoking with a path whose parent directory does not exist must exit non-zero."""
        nonexistent = "/tmp/does-not-exist-groundcrew-dir-xyz/groundcrew.config.ts"
        r = _run_script(nonexistent)
        self.assertNotEqual(r.returncode, 0, f"expected non-zero exit; stderr={r.stderr!r}")
        self.assertTrue(
            r.stderr.strip(),
            "expected a non-empty stderr message",
        )


class TestLoadExistingConditional(unittest.TestCase):
    """Tests 5–6: only run if @clipboard-health/groundcrew is importable."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _skip_if_unavailable(self) -> None:
        if not _groundcrew_available():
            self.skipTest(
                "@clipboard-health/groundcrew not installed; "
                "run `npm install -g @clipboard-health/groundcrew` to enable this test."
            )

    def _render_config(self, answers: dict, target: Path) -> Path:
        """Use render_config.py to write a valid groundcrew.config.ts."""
        result = subprocess.run(
            [_PYTHON3, str(RENDER_CONFIG), "--target", str(target)],
            input=json.dumps(answers),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            self.fail(f"render_config.py failed: {result.stderr}")
        return target

    # ------------------------------------------------------------------
    # Test 5: round-trip with real groundcrew
    # ------------------------------------------------------------------
    def test_round_trip_with_real_groundcrew(self) -> None:
        """Load a rendered config via groundcrew and verify the JSON round-trips."""
        self._skip_if_unavailable()

        answers = {
            "workspaceProjectDir": "~/dev/myproject",
            "knownRepositories": ["owner/repo-a", "owner/repo-b"],
        }
        config_path = self.tmpdir / "groundcrew.config.ts"
        self._render_config(answers, config_path)

        r = _run_script(str(config_path))
        self.assertEqual(r.returncode, 0, f"expected exit 0; stderr={r.stderr!r}")

        try:
            loaded = json.loads(r.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout is not valid JSON: {exc}; stdout={r.stdout!r}")

        workspace = loaded.get("workspace", {})
        self.assertIn(
            "projectDir",
            workspace,
            f"loaded JSON missing workspace.projectDir; got: {loaded!r}",
        )
        self.assertIn(
            "knownRepositories",
            workspace,
            f"loaded JSON missing workspace.knownRepositories; got: {loaded!r}",
        )

        loaded_repos = workspace["knownRepositories"]
        self.assertEqual(
            sorted(loaded_repos),
            sorted(answers["knownRepositories"]),
            f"knownRepositories mismatch; expected {answers['knownRepositories']!r}, got {loaded_repos!r}",
        )

    # ------------------------------------------------------------------
    # Test 6: malformed config → non-zero exit
    # ------------------------------------------------------------------
    def test_malformed_config_exits_nonzero(self) -> None:
        """A syntactically invalid config.ts must cause non-zero exit and a stderr message."""
        self._skip_if_unavailable()

        config_path = self.tmpdir / "groundcrew.config.ts"
        config_path.write_text(
            "export default { broken syntax }; satisfies Config;\n"
        )

        r = _run_script(str(config_path))
        self.assertNotEqual(r.returncode, 0, f"expected non-zero exit; stderr={r.stderr!r}")
        self.assertTrue(
            r.stderr.strip(),
            "expected a non-empty stderr message for malformed config",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
