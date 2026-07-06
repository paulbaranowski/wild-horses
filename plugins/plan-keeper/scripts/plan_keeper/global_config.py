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

    Raises PlanKeeperCliError(5) on malformed JSON or out-of-shape contents.
    Shape validation (top-level dict, ``aliases`` is a list of dicts with the
    required string keys) happens here rather than at every read site —
    TypedDicts give no runtime guarantee, so this is the boundary that
    converts untrusted JSON into something the rest of the package can trust.
    """
    path = global_config_path()
    if not path.exists():
        return {"aliases": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        # OSError covers IsADirectoryError, PermissionError, and other
        # filesystem-side failures on read_text; UnicodeDecodeError catches
        # non-UTF-8 bytes. Both classes raise the same
        # PlanKeeperCliError(code=5) that JSON errors do so `_maybe_alias`
        # can recover with its warn-then-fallback path instead of the
        # command crashing with an opaque traceback. `from e` preserves
        # the original cause.
        raise PlanKeeperCliError(
            f"malformed global config at {path}: {e}", code=5
        ) from e
    _validate_shape(data, path)
    return data


def _validate_shape(data: object, path: Path) -> None:
    """Reject a global-config payload that can't be a ``PlanKeeperGlobalConfig``.

    Cheap, single-pass structural check: top-level dict, ``aliases`` (if
    present) is a list of dicts where ``remote``/``subpath``/``name`` are all
    strings. Anything that fails this would crash downstream code with an
    opaque ``AttributeError`` / ``TypeError``; raising at the boundary
    converts that into the standard ``PlanKeeperCliError(code=5)`` malformed
    contract that ``load_config`` already uses for the per-repo file.
    """
    if not isinstance(data, dict):
        raise PlanKeeperCliError(
            f"malformed global config at {path}: top level must be a JSON "
            f"object, got {type(data).__name__}",
            code=5,
        )
    if "aliases" not in data:
        return
    aliases = data["aliases"]
    if not isinstance(aliases, list):
        raise PlanKeeperCliError(
            f"malformed global config at {path}: 'aliases' must be a list, "
            f"got {type(aliases).__name__}",
            code=5,
        )
    for i, entry in enumerate(aliases):
        if not isinstance(entry, dict):
            raise PlanKeeperCliError(
                f"malformed global config at {path}: aliases[{i}] must be an "
                f"object, got {type(entry).__name__}",
                code=5,
            )
        for key in ("remote", "subpath", "name"):
            if key not in entry:
                raise PlanKeeperCliError(
                    f"malformed global config at {path}: aliases[{i}] is "
                    f"missing required key {key!r}",
                    code=5,
                )
            if not isinstance(entry[key], str):
                raise PlanKeeperCliError(
                    f"malformed global config at {path}: aliases[{i}][{key!r}] "
                    f"must be a string, got {type(entry[key]).__name__}",
                    code=5,
                )


def save_global_config(data: PlanKeeperGlobalConfig) -> Path:
    """Atomically write the global config JSON.

    Creates the parent ``~/plans/`` directory if it doesn't exist yet (the
    first-ever ``alias add`` runs against a fresh $HOME with no plans tree).
    No explicit chmod here — this file carries no secrets, so the per-repo
    ``.plankeeper.json``'s 0600 hardening is unnecessary. ``write_atomic``
    uses ``tempfile.mkstemp`` internally, which lands the file at 0600 anyway
    (incidentally more restrictive than umask); intentional or not, it costs
    nothing.
    """
    path = global_config_path()
    write_atomic(path, json.dumps(data, indent=2) + "\n")
    return path
