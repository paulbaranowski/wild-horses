"""Per-repo ticket-system config in ~/plans/<repo>/.plankeeper.json:
load/save (atomic, 0600) plus credential redaction for display.
"""
import json
import os
import sys
from pathlib import Path

from plan_keeper import roots
from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.storage import CONFIG_FILE_NAME, repo_dir, write_atomic
from plan_keeper.types import PlanKeeperConfig

_SECRET_CONFIG_FIELDS = ("apiKey", "apiToken")


def _redact_section(section: dict) -> dict:
    """Return a copy of `section` with credential fields masked.

    Credentials live in the section root (e.g., `apiKey` for linear,
    `apiToken` for jira). The picker UI in the setup wizard reads
    everything ELSE from the section (`defaults`, `cache`) — those are
    not sensitive. Masking only the secret-named fields preserves the
    structure callers expect.
    """
    out = dict(section)
    for key in _SECRET_CONFIG_FIELDS:
        if key in out and out[key]:
            out[key] = "***redacted***"
    return out


def config_path(repo: str) -> Path:
    """The per-repo ``.plankeeper.json`` *write* path, in the repo's routed root.

    A repo's ticket-system config lives beside its plans, so writes follow the
    same root-routing rule save does (``roots.route_root``): the one root the
    repo lives in, else the default. Single-root installs resolve to
    ``PLAN_ROOT/<repo>/`` exactly as before. Reads go through ``load_config``,
    which falls back to the other roots - routing is time-dependent (a repo can
    start straddling after its config was written), so a read pinned to the
    routed root alone could strand credentials in the tree they were saved to.
    """
    return repo_dir(repo, roots.route_root(repo).path) / CONFIG_FILE_NAME


def _find_config_path(repo: str) -> Path:
    """The config path to *read*: the routed root's file, else the first root
    (registry order) whose ``<root>/<repo>/.plankeeper.json`` exists.

    The union fallback mirrors the multi-root read rule (reads union, writes
    route): credentials written while the repo lived in one root stay findable
    after the repo's routing changes (a new straddle, a moved plan set). When
    no root has the file, the routed path is returned so the caller's
    missing-file handling reports the canonical location.
    """
    routed = config_path(repo)
    if routed.exists():
        return routed
    for root in roots.load_roots():
        candidate = repo_dir(repo, root.path) / CONFIG_FILE_NAME
        if candidate.exists():
            return candidate
    return routed


def load_config(repo: str) -> PlanKeeperConfig:
    """Read the per-repo config JSON, unioning across roots (see
    ``_find_config_path``). Returns {} if no root has the file.

    Raises PlanKeeperCliError(5) on malformed JSON.
    """
    path = _find_config_path(repo)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PlanKeeperCliError(f"malformed config at {path}: {e}", code=5)


def save_config(repo: str, data: PlanKeeperConfig) -> Path:
    """Atomically write the per-repo config JSON, then chmod 600.

    The chmod is best-effort — if it fails the write itself still
    succeeds (just with default permissions). A warning is printed
    to stderr.
    """
    path = config_path(repo)
    write_atomic(path, json.dumps(data, indent=2) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        print(
            f"warning: couldn't chmod 600 {path}: {e}",
            file=sys.stderr,
        )
    return path
