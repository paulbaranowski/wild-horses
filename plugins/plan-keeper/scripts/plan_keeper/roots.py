"""The plan-root registry: the N named trees under which ``<repo>/`` folders live.

A *root* is a top-level directory that holds per-repo plan folders. The
**default root** is ``storage.PLAN_ROOT`` (``~/plans``); additional named roots
(``work``, ``personal``, ...) let a user keep physically separate trees with
their own backup/sync/git/privacy boundaries. This module is the single source
of truth for "which roots exist" and hosts the resolution helpers every read
and the save-routing rule build on.

Layering: ``roots -> storage`` (leaf-ward, one direction). ``storage`` never
imports this module, so it stays a leaf; the union scans that used to live in
``storage`` (``find_plans_by_ticket`` / ``resolve_ticket_to_path``) live here
instead, because they now iterate every root.

**Registry file.** ``<PLAN_ROOT.parent>/.config/plan-keeper/config.json``. In
production ``PLAN_ROOT`` is ``~/plans``, so this resolves to
``~/.config/plan-keeper/config.json`` (mirroring ``~/.config/groundcrew/``).
Deriving the path from ``storage.PLAN_ROOT.parent`` rather than ``Path.home()``
is deliberate: every test already isolates ``PLAN_ROOT`` (by ``$HOME`` or by
patching the attribute), and anchoring the registry to the same seam isolates
it for free.

Shape::

    {
      "roots": [
        {"name": "work", "path": "~/plans", "default": true},
        {"name": "personal", "path": "~/personal/plans"}
      ]
    }

**Backward compatibility.** With no file present (or an empty ``roots`` list),
exactly one implicit root is returned: the default root at
``storage.PLAN_ROOT``, named ``DEFAULT_ROOT_NAME``. Every read then unions "all
roots" == ``[~/plans]`` and every save routes to that one default, so behavior
is byte-identical to the pre-multi-root single-tree tool. The label a listing
shows per plan (see ``multiple_roots``) only appears once a second root exists.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from plan_keeper import storage
from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.frontmatter import parse_frontmatter
from plan_keeper.naming import normalize_override

# The name of the implicit default root (the one at storage.PLAN_ROOT) when no
# config file has been written yet. Once the user runs `pk root add`, the config
# materializes this entry explicitly and the name is whatever it was stored as.
DEFAULT_ROOT_NAME = "default"

_CONFIG_DIR_REL = Path(".config") / "plan-keeper"
_CONFIG_FILE_NAME = "config.json"


@dataclass(frozen=True)
class Root:
    """One registered plan tree: a display ``name`` and its absolute ``path``.

    ``default`` marks the single root that save routes to when a repo lives in
    zero roots (or straddles more than one). Exactly one ``Root`` in a loaded
    registry carries ``default=True`` - ``load_roots`` enforces that invariant.
    """

    name: str
    path: Path
    default: bool


def config_path() -> Path:
    """The registry file path: ``<PLAN_ROOT.parent>/.config/plan-keeper/config.json``.

    Read live off ``storage.PLAN_ROOT`` (never captured) so a test override of
    that attribute relocates the registry with it. See the module docstring for
    why the anchor is ``PLAN_ROOT.parent`` rather than ``Path.home()``.
    """
    return storage.PLAN_ROOT.parent / _CONFIG_DIR_REL / _CONFIG_FILE_NAME


def _implicit_default() -> list[Root]:
    """The single-root registry used when no config file exists: the default
    root at ``storage.PLAN_ROOT``. This is the backward-compatible fallback."""
    return [Root(DEFAULT_ROOT_NAME, storage.PLAN_ROOT, default=True)]


def load_roots() -> list[Root]:
    """Return every registered root, default first is NOT guaranteed but exactly
    one ``default=True`` is.

    No config file, or a file whose ``roots`` list is empty/missing, yields the
    implicit single default root (see ``_implicit_default``). A present file is
    trusted: each entry must be an object carrying ``name`` and ``path``
    (``~`` is expanded); ``default`` is optional. If no entry is flagged default,
    the first becomes it; if several are, the first flagged wins and the rest are
    demoted, so the "exactly one default" invariant always holds downstream.

    Raises ``PlanKeeperCliError(5)`` on unreadable/malformed JSON or a malformed
    entry - the same loud-fail contract ``config.load_config`` uses, so a
    corrupt registry is fixed by the user rather than silently ignored.
    """
    path = config_path()
    if not path.exists():
        return _implicit_default()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise PlanKeeperCliError(f"malformed roots config at {path}: {e}", code=5)
    raw = data.get("roots") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return _implicit_default()
    roots: list[Root] = []
    default_seen = False
    for entry in raw:
        if not isinstance(entry, dict) or "name" not in entry or "path" not in entry:
            raise PlanKeeperCliError(
                f"malformed root entry in {path}: {entry!r} "
                "(each root needs a 'name' and a 'path')",
                code=5,
            )
        is_default = bool(entry.get("default")) and not default_seen
        default_seen = default_seen or is_default
        roots.append(
            Root(
                name=str(entry["name"]),
                path=Path(str(entry["path"])).expanduser(),
                default=is_default,
            )
        )
    if not default_seen:
        # No entry claimed default: promote the first so the invariant holds.
        roots[0] = Root(roots[0].name, roots[0].path, default=True)
    return roots


def default_root() -> Root:
    """The one root save falls back to (repo in zero or 2+ roots). Guaranteed
    to exist: ``load_roots`` always yields exactly one ``default=True``."""
    for root in load_roots():
        if root.default:
            return root
    # Unreachable given load_roots' invariant; kept as a defensive fallback.
    return _implicit_default()[0]


def multiple_roots() -> bool:
    """True when more than one root is configured. Gates the per-plan ``[root]``
    label in listings: a single-root user sees byte-identical output to today."""
    return len(load_roots()) > 1


def find_root(name: str) -> Optional[Root]:
    """Resolve a root by name (normalized like a repo override), or None.

    Both the stored name and the query are run through ``normalize_override``
    so "Personal" / "personal" / "  personal " all match a root registered as
    ``personal``, matching the light normalization repo overrides already get.
    """
    target = normalize_override(name)
    for root in load_roots():
        if normalize_override(root.name) == target:
            return root
    return None


def is_root_name(name: str) -> bool:
    """True iff ``name`` matches a configured root (see ``find_root``). The
    skills use this to decide whether a bare destination token means a root."""
    return find_root(name) is not None


def resolve_root_arg(name: str) -> Root:
    """Resolve an explicit ``--root`` value to a ``Root`` or raise ``code 2``.

    The loud error names the configured roots so a typo is easy to fix. Used by
    every command that takes an explicit root selector (save, list, move).
    """
    root = find_root(name)
    if root is None:
        available = ", ".join(r.name for r in load_roots())
        raise PlanKeeperCliError(
            f"unknown root {name!r}; configured roots: {available}", code=2
        )
    return root


def route_root(repo: str, override: Optional[str] = None) -> Root:
    """Pick the root a save/config write for ``repo`` lands in.

    The routing rule (design decision): an explicit ``override`` wins
    (resolved via ``resolve_root_arg``); otherwise, if ``repo`` already has a
    folder in exactly one root, route there; in zero roots *or* two-plus roots
    (a straddling repo), fall back to the default root. The straddle case does
    not prompt - the caller can redirect with ``--root`` or move the plan after.
    """
    if override:
        return resolve_root_arg(override)
    containing = roots_for_repo(repo)
    if len(containing) == 1:
        return containing[0]
    return default_root()


def roots_for_repo(repo: str) -> list[Root]:
    """Every root that already has a ``<root>/<repo>/`` directory, in registry
    order. Drives the save-routing rule and answers "where does this repo live"."""
    return [root for root in load_roots() if (root.path / repo).is_dir()]


def iter_repo_dirs() -> Iterator[tuple[Root, Path]]:
    """Yield ``(root, repo_dir)`` for every repo folder across every root.

    Roots are visited in registry order; repo folders within a root are sorted
    alphabetically, and dotfiles / non-directories (e.g. the per-repo
    ``.plankeeper.json`` sibling never appears - configs live inside repo dirs)
    are skipped. This is the single union iterator behind cross-root reads
    (``list --all``, ``repo list``, ``queue list``, ``crew fetch``), so they all
    agree on which repos exist and in what order.
    """
    for root in load_roots():
        if not root.path.exists():
            continue
        for entry in sorted(root.path.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            yield root, entry


# --- ticket resolution (union across roots) ---------------------------------
# These lived in ``storage`` when there was one tree; they move here because
# they now scan every root. ``storage`` stays a leaf (no ``roots`` import).


def find_plans_by_ticket(ticket_id: str) -> list[Path]:
    """Return active (top-level) plans across all roots/repos carrying ``ticket_id``.

    Global and system-agnostic: a literal match against the stored
    ``Plan-keeper Ticket`` (``plan-<n>``), ``Linear Ticket`` (``ENG-123``), and
    ``Jira Ticket`` values, so an id from any tracker resolves through one path.
    ``done/`` and ``deferred/`` are excluded because every operation that
    resolves by ticket (archive, status flip, push) acts on an active plan.

    Because a plan keeps its id across roots (the id seed omits the root), the
    *same* ``(repo, stem)`` in two roots would carry the same id; both are
    returned and ``resolve_ticket_to_path`` degrades to its ">1 match" error so
    the caller disambiguates with ``--file``.
    """
    matches: list[Path] = []
    for _, repo_dir in iter_repo_dirs():
        for path in sorted(repo_dir.iterdir()):
            if (not path.is_file() or path.name.startswith(".")
                    or path.suffix != ".md"):
                continue
            try:
                meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except (PlanKeeperCliError, OSError, UnicodeDecodeError):
                continue
            stored = {
                val for val in (
                    (meta.get("Plan-keeper Ticket") or "").strip(),
                    (meta.get("Linear Ticket") or "").strip(),
                    (meta.get("Jira Ticket") or "").strip(),
                ) if val
            }
            if ticket_id in stored:
                matches.append(path)
    return matches


def resolve_ticket_to_path(ticket_id: str) -> Path:
    """Resolve a ticket id to the single active plan that carries it.

    0 matches -> code 3 (parallels archive's "plan not found"). >1 -> code 2
    listing every candidate, so the caller can disambiguate with --file. The
    listing spans roots, so a same-id straddle is named explicitly.
    """
    matches = find_plans_by_ticket(ticket_id)
    if not matches:
        raise PlanKeeperCliError(
            f"no active plan with Ticket {ticket_id!r} found under any plan root",
            code=3,
        )
    if len(matches) > 1:
        listing = "\n".join(f"  {p}" for p in matches)
        raise PlanKeeperCliError(
            f"ticket {ticket_id!r} matches {len(matches)} plans; "
            f"disambiguate with --file:\n{listing}",
            code=2,
        )
    return matches[0]


# --- registry mutation (`pk root` subcommands) ------------------------------


def _validate_root_name(name: str) -> str:
    """Normalize and validate a root name; raise ``code 2`` on an unusable one.

    Same shape rule as a repo name (no path separators, not ``.``/``..``, not
    empty) so a root name can never smuggle path components - plus the light
    override normalization so "My Work" registers as ``my-work``.
    """
    norm = normalize_override(name)
    if not norm or norm in {".", ".."} or "/" in norm or "\\" in norm:
        raise PlanKeeperCliError(f"invalid root name: {name!r}", code=2)
    return norm


def _nests(a: Path, b: Path) -> bool:
    """True if ``a`` is the same as, or nested inside, ``b`` (path-prefix test)."""
    a = a.expanduser().resolve()
    b = b.expanduser().resolve()
    return a == b or b in a.parents


def _write_roots(roots: list[Root]) -> Path:
    """Serialize ``roots`` to the registry file (atomic) and return its path.

    Paths are stored resolved-absolute so disjointness checks and equality are
    unambiguous; ``~`` is not preserved. The registry holds no secrets, so -
    unlike ``config.save_config`` - no 0600 chmod is applied.
    """
    data = {
        "roots": [
            {
                "name": r.name,
                "path": str(r.path.expanduser().resolve()),
                "default": r.default,
            }
            for r in roots
        ]
    }
    path = config_path()
    storage.write_atomic(path, json.dumps(data, indent=2) + "\n")
    return path


def _materialized_roots() -> list[Root]:
    """Load roots, converting the implicit default into an explicit entry.

    The first ``pk root add`` on a fresh install has only the implicit default
    in memory; before appending a second root we persist the default as a real
    entry (resolved path), so the written registry is self-contained.
    """
    return list(load_roots())


def add_root(name: str, path: str) -> Root:
    """Register a new root ``name`` -> ``path``; return the created ``Root``.

    Enforces the disjoint-subtree invariant: the new path may neither nest
    inside, nor contain, any already-registered root (overlapping roots would
    double-count plans and make ``[root]`` labels incoherent). A duplicate name
    or overlapping path fails with ``code 2``. The target directory is created
    if absent. Adding the first extra root materializes the implicit default
    into the file so the registry lists both.
    """
    norm = _validate_root_name(name)
    new_path = Path(path).expanduser().resolve()
    roots = _materialized_roots()
    for r in roots:
        if normalize_override(r.name) == norm:
            raise PlanKeeperCliError(
                f"root {norm!r} already exists (path {r.path})", code=2
            )
        if _nests(new_path, r.path) or _nests(r.path, new_path):
            raise PlanKeeperCliError(
                f"root path {new_path} overlaps existing root {r.name!r} "
                f"({r.path}); roots must be disjoint subtrees",
                code=2,
            )
    new_path.mkdir(parents=True, exist_ok=True)
    created = Root(norm, new_path, default=False)
    _write_roots(roots + [created])
    return created


def remove_root(name: str) -> Root:
    """Unregister root ``name``; return the removed ``Root``.

    Refuses to remove the default root (``code 2`` - set another default first)
    or the last remaining root, so the registry can never end up empty or
    without a default. An unknown name is ``code 2``.
    """
    norm = normalize_override(name)
    roots = _materialized_roots()
    target = next((r for r in roots if normalize_override(r.name) == norm), None)
    if target is None:
        available = ", ".join(r.name for r in roots)
        raise PlanKeeperCliError(
            f"unknown root {name!r}; configured roots: {available}", code=2
        )
    if target.default:
        raise PlanKeeperCliError(
            f"cannot remove the default root {target.name!r}; "
            "run `root set-default <other>` first",
            code=2,
        )
    remaining = [r for r in roots if r is not target]
    if not remaining:
        raise PlanKeeperCliError("cannot remove the last remaining root", code=2)
    _write_roots(remaining)
    return target


def set_default_root(name: str) -> Root:
    """Make root ``name`` the default; clear the flag on every other. Returns the
    new default. Unknown name -> ``code 2``."""
    norm = normalize_override(name)
    roots = _materialized_roots()
    if not any(normalize_override(r.name) == norm for r in roots):
        available = ", ".join(r.name for r in roots)
        raise PlanKeeperCliError(
            f"unknown root {name!r}; configured roots: {available}", code=2
        )
    updated = [
        Root(r.name, r.path, default=(normalize_override(r.name) == norm))
        for r in roots
    ]
    _write_roots(updated)
    return next(r for r in updated if r.default)
