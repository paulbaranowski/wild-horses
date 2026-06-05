"""`plan-keeper upgrade`: update the Homebrew binary in place.

The CLI ships to end users as a version-stable Homebrew binary (formula
``plan-keeper`` from the ``paulbaranowski/tap`` tap). ``upgrade`` runs
``brew update && brew upgrade plan-keeper`` against that formula, then re-runs
``plan-keeper crew install`` so any groundcrew wiring shipped by the new version
takes effect. It refuses to act when plan-keeper isn't a Homebrew install
(e.g. a dev checkout run from source), pointing the user at the right fix
instead of silently no-op'ing.

The orchestration (:func:`run_upgrade`) takes injected process callables so it
is unit-testable without shelling out to a real ``brew``; the thin
``cmd_upgrade`` wrapper in ``cli`` supplies the real ones.
"""
from __future__ import annotations

import subprocess
from typing import Callable, Optional, Sequence, TextIO

# Homebrew formula name and the one-liner that installs it fresh. The install
# string is echoed in the "not a brew install" guidance so the user can
# copy-paste a fix.
FORMULA = "plan-keeper"
TAP_INSTALL = "brew install paulbaranowski/tap/plan-keeper"

# A streaming runner inherits this process's stdio (so `brew`'s progress shows
# live) and returns just the exit code.
StreamRunner = Callable[[Sequence[str]], int]
# A capture runner swallows stdout+stderr and returns (exit_code, combined).
CaptureRunner = Callable[[Sequence[str]], "tuple[int, str]"]
# Resolves an executable to its absolute path (shutil.which), or None if absent.
Which = Callable[[str], Optional[str]]


def default_stream(cmd: Sequence[str]) -> int:
    """Run ``cmd`` inheriting this process's stdio so its output streams live.

    A missing executable surfaces as exit 127 rather than an uncaught
    ``FileNotFoundError``, so the caller can report it like any other failure.
    """
    try:
        return subprocess.run(list(cmd)).returncode
    except FileNotFoundError:
        return 127


def default_capture(cmd: Sequence[str]) -> "tuple[int, str]":
    """Run ``cmd`` capturing stdout+stderr; returns ``(exit_code, combined)``.

    A missing executable surfaces as ``(127, "")`` for the same reason as
    :func:`default_stream`.
    """
    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True)
    except FileNotFoundError:
        return 127, ""
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _parse_version(version_output: str) -> Optional[str]:
    """Pull the version token out of ``plan-keeper --version`` output
    (``plan-keeper X.Y.Z`` → ``X.Y.Z``). Returns None on empty/garbage input."""
    parts = version_output.split()
    return parts[-1] if parts else None


def run_upgrade(
    *,
    old_version: str,
    which: Which,
    stream: StreamRunner,
    capture: CaptureRunner,
    out: TextIO,
) -> int:
    """Upgrade the brew binary, re-wire groundcrew, report old → new version.

    Returns 0 on a successful (or already-current) upgrade; 1 when the install
    isn't a Homebrew one; or the failing step's exit code when a ``brew`` or
    ``crew install`` step fails. Every non-zero path prints a line naming the
    step that failed and what to do about it.
    """
    # Guard: a self-update only makes sense for the brew binary. If brew is
    # absent or doesn't own `plan-keeper`, say how it was likely installed and
    # how to switch — never silently no-op.
    if which("brew") is None:
        out.write(
            "plan-keeper is not a Homebrew install (Homebrew isn't on this "
            "machine).\nInstall it with:\n\n    " + TAP_INSTALL + "\n"
        )
        return 1
    rc, _ = capture(["brew", "list", "--versions", FORMULA])
    if rc != 0:
        out.write(
            "plan-keeper is not a Homebrew install. If you have it via a dev "
            "checkout, update that checkout with `git pull` instead.\n"
            "To switch to the brew binary:\n\n    " + TAP_INSTALL + "\n"
        )
        return 1

    # Refresh formula metadata, then upgrade. `brew update` is what teaches brew
    # about a newer plan-keeper, so a stale tap can't upgrade without it.
    rc = stream(["brew", "update"])
    if rc != 0:
        out.write(
            "plan-keeper: `brew update` failed (exit %d) — see output above.\n" % rc
        )
        return rc
    rc = stream(["brew", "upgrade", FORMULA])
    if rc != 0:
        out.write(
            "plan-keeper: `brew upgrade %s` failed (exit %d) — see output "
            "above.\n" % (FORMULA, rc)
        )
        return rc

    # Re-wire groundcrew via the freshly-installed binary (not this still-old
    # process), so any wiring the new version ships actually takes effect.
    pk = which(FORMULA) or FORMULA
    crew_rc = stream([pk, "crew", "install"])

    # Report the version delta by asking the new on-disk binary — this process
    # is still the pre-upgrade one, so its in-memory __version__ is the old one.
    _, version_output = capture([pk, "--version"])
    new_version = _parse_version(version_output) or "unknown"
    if new_version == old_version:
        out.write("plan-keeper is already up to date (%s).\n" % old_version)
    else:
        out.write("Upgraded plan-keeper: %s → %s\n" % (old_version, new_version))

    if crew_rc != 0:
        out.write(
            "Warning: `plan-keeper crew install` exited %d — groundcrew wiring "
            "may be stale. Re-run it to retry.\n" % crew_rc
        )
        return crew_rc
    out.write("Groundcrew wiring re-validated.\n")
    return 0
