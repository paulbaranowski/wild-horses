"""Global ~/plans/.plankeeper-global.json: load/save (atomic).

Parallel to plan_keeper.config (per-repo .plankeeper.json) but global in scope:
one file at the plans-tree root for state that isn't repo-specific. The current
inhabitant is the monorepo-subpath -> groundcrew-alias mapping consumed by
``derive_repo``; future inhabitants land at the same top level (``defaults``,
``hooks``, ...). The loader treats unknown top-level keys as inert so newer
clients can write keys this version doesn't understand without an older client
silently erasing them on a load/save round trip.

No chmod 0o600 (unlike per-repo config) — this file carries no secrets.
"""
import json

from pathlib import Path

from plan_keeper import storage
from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.storage import write_atomic
from plan_keeper.types import PlanKeeperGlobalConfig

GLOBAL_CONFIG_FILE_NAME = ".plankeeper-global.json"


def global_config_path() -> Path:
    # Resolved live off ``storage.PLAN_ROOT`` so tests that relocate the plans
    # root (and any runtime override) reach the right file. Importing the value
    # directly would bind at import time and miss the override.
    return storage.PLAN_ROOT / GLOBAL_CONFIG_FILE_NAME


def load_global_config() -> PlanKeeperGlobalConfig:
    """Read the global config JSON. Returns ``{"aliases": []}`` if missing.

    Raises PlanKeeperCliError(5) on malformed JSON.
    """
    path = global_config_path()
    if not path.exists():
        return {"aliases": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PlanKeeperCliError(f"malformed global config at {path}: {e}", code=5)


def save_global_config(data: PlanKeeperGlobalConfig) -> Path:
    """Atomically write the global config JSON.

    Creates the parent ``~/plans/`` directory if it doesn't exist yet (the
    first-ever ``alias add`` runs against a fresh $HOME with no plans tree).
    No chmod — this file carries no secrets, so it stays at the user's umask
    default.
    """
    path = global_config_path()
    write_atomic(path, json.dumps(data, indent=2) + "\n")
    return path
