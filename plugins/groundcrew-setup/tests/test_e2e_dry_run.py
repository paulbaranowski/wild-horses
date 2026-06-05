#!/usr/bin/env python3
"""End-to-end sandboxed dry-run of the v2.0.0 wizard flow.

Drives every script in the order the `setup` command orchestrates them,
under an isolated HOME + XDG_CONFIG_HOME + PATH so the user's real
~/.config, ~/.zshrc, /opt/homebrew/bin/safehouse, and the global
@clipboard-health/groundcrew install are NEVER observed.

Stubs npm and brew so install_groundcrew.py and install_safehouse.py
execute their install branches without hitting the real registries.

Verifies:
  - Every Phase-0 discovery script emits well-formed JSON.
  - Both install scripts succeed via stubs and report installed/version.
  - Clearance + safehouse renderers write the expected files at the
    expected paths with the expected shapes.
  - render_config.py produces a parseable config.ts with a `satisfies Config`
    annotation and references the workspace.projectDir we asked for.
  - The wizard's one-liner rc snippet sources cleanly under bash with
    both sidecars in place.

Run from anywhere:

    python3 plugins/groundcrew-setup/tests/test_e2e_dry_run.py -v
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
_PYTHON3 = sys.executable

# The scripts the config skill orchestrates, in invocation order.
DISCOVERY = (
    "discover_existing_config.py",
    "discover_repos.py",
    "detect_installed_skills.py",
    "discover_clearance_setup.py",
    "discover_safehouse_setup.py",
)
INSTALL_CHECKS = (
    "install_groundcrew.py",
    "install_safehouse.py",
)


def _write_stub(bin_dir: Path, name: str, body: str) -> Path:
    p = bin_dir / name
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _stub_npm(state_file: Path) -> str:
    return textwrap.dedent(f"""\
        #!/bin/sh
        STATE_FILE='{state_file}'
        STATE=$(cat "$STATE_FILE" 2>/dev/null || echo missing)
        if [ "$1" = "ls" ]; then
          if [ "$STATE" = "installed" ]; then
            printf '%s' '{{"dependencies":{{"@clipboard-health/groundcrew":{{"version":"4.9.0"}}}}}}'
            exit 0
          fi
          printf '%s' '{{"dependencies":{{}}}}'
          exit 1
        fi
        if [ "$1" = "install" ]; then
          echo installed > "$STATE_FILE"
          exit 0
        fi
        if [ "$1" = "root" ]; then
          echo /opt/homebrew/lib/node_modules
          exit 0
        fi
        exit 99
    """)


def _stub_brew(state_file: Path) -> str:
    return textwrap.dedent(f"""\
        #!/bin/sh
        STATE_FILE='{state_file}'
        STATE=$(cat "$STATE_FILE" 2>/dev/null || echo missing)
        if [ "$1" = "list" ] && [ "$2" = "--versions" ]; then
          if [ "$STATE" = "installed" ]; then
            echo "agent-safehouse 0.9.0"
            exit 0
          fi
          exit 1
        fi
        if [ "$1" = "install" ]; then
          echo installed > "$STATE_FILE"
          exit 0
        fi
        if [ "$1" = "list" ] && [ "$2" = "agent-safehouse" ] && [ "$3" = "--formula" ]; then
          if [ "$STATE" = "installed" ]; then exit 0; fi
          exit 1
        fi
        exit 99
    """)


def _run(args: list[str], env: dict[str, str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=30, env=env, **kwargs,
    )


class TestEndToEndDryRun(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.home = self.tmpdir / "home"
        self.xdg = self.home / ".config"
        self.xdg.mkdir(parents=True)
        self.bin_dir = self.tmpdir / "bin"
        self.bin_dir.mkdir()
        # Place a stub `gh` so discover_repos.py doesn't try the real one.
        _write_stub(self.bin_dir, "gh", "#!/bin/sh\nprintf '[]'\nexit 0\n")
        # Stub npm + brew tied to per-test state files.
        self.npm_state = self.tmpdir / "npm-state"
        self.brew_state = self.tmpdir / "brew-state"
        _write_stub(self.bin_dir, "npm", _stub_npm(self.npm_state))
        _write_stub(self.bin_dir, "brew", _stub_brew(self.brew_state))
        # Path includes our stubs first, then standard system utils for cat/printf.
        self.env = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "PATH": f"{self.bin_dir}:/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
        }

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        return _run([_PYTHON3, str(SCRIPTS_DIR / name), *args], env=self.env)

    # ==================================================================
    # Phase 0 — every discovery script emits well-formed JSON or path
    # ==================================================================
    def test_phase_0_discovery_scripts_emit_expected_shapes(self) -> None:
        # discover_existing_config: empty stdout when no config present.
        r = self._run_script("discover_existing_config.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "", "no config should yield empty stdout")

        # discover_repos: JSON array (possibly empty).
        r = self._run_script("discover_repos.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertIsInstance(data, list)

        # detect_installed_skills: JSON with two boolean keys.
        r = self._run_script("detect_installed_skills.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(set(data.keys()), {"superpowers", "babysitPr"})
        self.assertIsInstance(data["superpowers"], bool)
        self.assertIsInstance(data["babysitPr"], bool)

        # discover_clearance_setup: JSON with 5 keys.
        r = self._run_script("discover_clearance_setup.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(
            set(data.keys()),
            {"personalFileExists", "personalFileHasClaudeHosts", "envExported",
             "daemonPid", "daemonAgeSeconds"},
        )

        # discover_safehouse_setup: JSON with 6 keys.
        r = self._run_script("discover_safehouse_setup.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(
            set(data.keys()),
            {"binaryAvailable", "binaryPath", "brewFormulaInstalled",
             "envExported", "sidecarPresent", "sidecarHasFunctions"},
        )

    # ==================================================================
    # Phase 1 — install_groundcrew and install_safehouse succeed via stubs
    # ==================================================================
    def test_phase_1_install_groundcrew_succeeds(self) -> None:
        r = self._run_script("install_groundcrew.py", "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["action"], "missing")

        r = self._run_script("install_groundcrew.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "installed")
        self.assertEqual(report["version"], "4.9.0")

        # Re-running is a no-op (already-installed).
        r = self._run_script("install_groundcrew.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "already-installed")

    def test_phase_1_install_safehouse_succeeds(self) -> None:
        r = self._run_script("install_safehouse.py", "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["action"], "missing")

        r = self._run_script("install_safehouse.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["action"], "installed")
        self.assertEqual(report["version"], "0.9.0")

    # ==================================================================
    # Phase 6 — clearance renderers
    # ==================================================================
    def test_phase_6_clearance_writes_allowlist_and_sidecar(self) -> None:
        # personal-allow-hosts is missing → create mode.
        r = self._run_script("render_clearance_hosts.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        allowlist = self.home / ".config" / "clearance" / "personal-allow-hosts"
        self.assertTrue(allowlist.exists())
        text = allowlist.read_text()
        self.assertIn("downloads.claude.ai", text)
        self.assertIn("mcp-proxy.anthropic.com", text)

        # Env sidecar.
        r = self._run_script("render_clearance_env.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        sidecar = Path(report["target"])
        self.assertEqual(sidecar, self.home / ".config" / "clearance" / "env.sh")
        self.assertTrue(sidecar.exists())
        self.assertEqual(report["rcConflicts"], [])
        text = sidecar.read_text()
        # Ordering check: PERSONAL_HOSTS=1 export must precede ALLOW_HOSTS export.
        idx_personal = text.index("export CLEARANCE_PERSONAL_HOSTS=1")
        idx_allow = text.index("export CLEARANCE_ALLOW_HOSTS_FILES=")
        self.assertLess(idx_personal, idx_allow,
                        "PERSONAL_HOSTS must come first; reverse order silently drops the personal-file branch")

    # ==================================================================
    # Phase 7 — safehouse sidecar + overrides stub
    # ==================================================================
    def test_phase_7_safehouse_sidecar_and_stub(self) -> None:
        r = self._run_script("render_safehouse_env.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        sidecar = Path(report["target"])
        self.assertEqual(sidecar, self.home / ".config" / "agent-safehouse" / "env.sh")
        self.assertTrue(sidecar.exists())
        overrides = Path(report["overridesStub"])
        self.assertEqual(overrides, self.home / ".config" / "agent-safehouse" / "local-overrides.sb")
        self.assertTrue(overrides.exists())

        text = sidecar.read_text()
        # Both wrapper functions defined.
        self.assertIn("safe() {", text)
        self.assertIn("safe-claude() {", text)
        self.assertIn("export SAFEHOUSE_APPEND_PROFILE=", text)

    # ==================================================================
    # Phase 9 — compose_initial_prompt + render_config
    # ==================================================================
    def test_phase_9_render_config_with_features_and_repos(self) -> None:
        # Compose initial prompt.
        r = self._run_script(
            "compose_initial_prompt.py", "--features", "superpowers,babysitPr",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        initial_prompt = r.stdout
        self.assertIn("superpowers", initial_prompt.lower())

        # Render config.
        target = self.home / ".config" / "groundcrew" / "config.ts"
        answers = {
            "workspaceProjectDir": "~/work",
            "knownRepositories": ["foo/bar", "baz/qux"],
            "promptFeatures": ["superpowers", "babysitPr"],
            "claudeBypassPermissions": True,
        }
        r = subprocess.run(
            [_PYTHON3, str(SCRIPTS_DIR / "render_config.py"), "--target", str(target)],
            input=json.dumps(answers),
            capture_output=True, text=True, timeout=15, env=self.env,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(target.exists())
        text = target.read_text()
        # Functional config (not a template-with-comments).
        self.assertIn("satisfies Config", text)
        self.assertIn("foo/bar", text)
        self.assertIn("baz/qux", text)
        # Workspace.projectDir is set to ~/work.
        self.assertIn("~/work", text)

    # ==================================================================
    # Integration: both sidecars source cleanly under bash with stubs
    # ==================================================================
    def test_integration_one_liner_sources_both_sidecars(self) -> None:
        # Pre-render both sidecars + npm stub (state="installed" so $(npm root -g) works).
        self.npm_state.write_text("installed\n", encoding="utf-8")
        self._run_script("render_clearance_env.py")
        self._run_script("render_safehouse_env.py")

        # The one-liner snippet the wizard's Phase 9 summary prints.
        rc_snippet = textwrap.dedent("""\
            for f in $HOME/.config/clearance/env.sh $HOME/.config/agent-safehouse/env.sh; do
              [ -f "$f" ] && . "$f"
            done
            echo SAP=$SAFEHOUSE_APPEND_PROFILE
            echo CAH=$CLEARANCE_ALLOW_HOSTS_FILES
            type safe
            type safe-claude
        """)
        r = subprocess.run(
            ["/bin/bash", "-c", rc_snippet],
            capture_output=True, text=True, env=self.env, timeout=10,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # Both sidecars contributed their exports + functions.
        self.assertIn("SAP=" + str(self.home), r.stdout)
        self.assertIn("CAH=/opt/homebrew/lib/node_modules/@clipboard-health/groundcrew/clearance-allow-hosts:"
                      + str(self.home) + "/.config/clearance/personal-allow-hosts", r.stdout)
        self.assertIn("safe is a function", r.stdout)
        self.assertIn("safe-claude is a function", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
