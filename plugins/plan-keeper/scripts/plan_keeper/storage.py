"""Filesystem layout for ~/plans/<repo>/: the shared path constants, atomic
write, per-repo listing, and the newest-first sort order.

Holds the single source of truth for PLAN_ROOT and the size limits; every
other module imports them from here rather than redefining.
"""
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Literal

from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.frontmatter import parse_frontmatter

# The closed lifecycle-Status vocabulary. Members mirror ``LIFECYCLE_STATES``
# (``ACTIVE_STATES`` + the ``TERMINAL_DIRS`` keys) exactly; that tuple stays the
# runtime source of truth and this Literal is the static mirror so signatures
# can name the closed set.
Status = Literal[
    "backlog", "todo", "in-progress", "in-review", "done", "deferred"
]

# Runtime/test override point: always reference as ``storage.PLAN_ROOT`` so the
# attribute is read live off this module. Never import it by value
# (``from plan_keeper.storage import PLAN_ROOT``) — a value import binds at
# import time and silently breaks test isolation and any runtime override, since
# rebinding this attribute afterwards won't reach the already-captured copy.
PLAN_ROOT = Path.home() / "plans"
MAX_SLUG_LEN = 50
MAX_SUFFIX = 99
CONFIG_FILE_NAME = ".plankeeper.json"


def write_atomic(path: Path, content: str) -> None:
    """Write text to a sibling tmp file, fsync, then os.replace.

    POSIX-atomic. The original file is untouched until the rename, so
    no half-written intermediate state is observable. Lifted from
    plugins/harness/skills/task-list-runner/task_list_cli.py.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def repo_dir(repo: str) -> Path:
    return PLAN_ROOT / repo


def find_unused_suffix(target: Path) -> Path:
    """Return the first non-existing variant of `target` with `-N` suffix."""
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    for n in range(2, MAX_SUFFIX + 1):
        candidate = parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise PlanKeeperCliError(
        f"all -N variants of {target.name} up to -{MAX_SUFFIX} are taken",
        code=4,
    )


# Lifecycle states and their on-disk home. Active states (backlog/todo/
# in-progress/in-review) share the repo's top-level directory and are
# distinguished only by their `Status:` frontmatter; done/deferred are
# physical subdirectories. The boundary is load-bearing: crew fetch, queue
# list, and find_plans_by_ticket scan only the active dir (one level deep) to
# exclude terminal plans without parsing every file's frontmatter.
ACTIVE_STATES = ("backlog", "todo", "in-progress", "in-review")
TERMINAL_DIRS = {"done": "done", "deferred": "deferred"}
LIFECYCLE_STATES = (*ACTIVE_STATES, *TERMINAL_DIRS)


def state_subdir(repo_root: Path, state: str) -> Path:
    """Map a lifecycle state to its directory under `repo_root`.

    Active states (backlog/todo/in-progress/in-review) resolve to `repo_root`
    itself — they live at the top level and differ only by `Status:`. Terminal
    states resolve to their named subdirectory (`done/`, `deferred/`). Raises
    on an unknown state so a typo can't silently land a plan at the root.
    """
    if state in ACTIVE_STATES:
        return repo_root
    subdir = TERMINAL_DIRS.get(state)
    if subdir is None:
        raise PlanKeeperCliError(f"unknown state: {state}", code=2)
    return repo_root / subdir


def list_plans(repo: str, state: str) -> list[Path]:
    """Return sorted plans for a repo in a given state, newest-first.

    Includes any non-dotfile in the directory regardless of extension —
    plan-save accepts arbitrary extensions (e.g. paired .json + .md from
    task-list-builder), so list must surface them. Dotfiles are excluded
    to keep the per-repo `.plankeeper.json` config out of the listing.
    """
    base = repo_dir(repo)
    if state == "active":
        d = base
    elif state in TERMINAL_DIRS:
        d = base / TERMINAL_DIRS[state]
    else:
        raise PlanKeeperCliError(f"unknown state: {state}", code=2)
    if not d.exists():
        return []
    files = [p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")]
    files.sort(key=_plan_sort_key, reverse=True)
    return files


def find_plans_by_ticket(ticket_id: str) -> list[Path]:
    """Return active (top-level) plans across all repos that carry `ticket_id`
    in any of their id fields.

    Global and system-agnostic: a literal match against the stored
    `Plan-keeper Ticket` (`plan-<n>`), `Linear Ticket` (`ENG-123`), and
    `Jira Ticket` values, so an id from any tracker resolves through one path.
    `done/` and `deferred/` are excluded because every operation that resolves
    by ticket (archive, status flip, push) acts on an active plan.
    """
    matches: list[Path] = []
    if not PLAN_ROOT.exists():
        return matches
    for repo_entry in sorted(PLAN_ROOT.iterdir()):
        if not repo_entry.is_dir() or repo_entry.name.startswith("."):
            continue
        for path in sorted(repo_entry.iterdir()):
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
    listing every candidate, so the caller can disambiguate with --file.
    """
    matches = find_plans_by_ticket(ticket_id)
    if not matches:
        raise PlanKeeperCliError(
            f"no active plan with Ticket {ticket_id!r} found under {PLAN_ROOT}",
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


# Leading YYYY-MM-DD on a plan filename (e.g. "2026-06-02-foo.md").
_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# The exact shape plan-save writes for `Created:` (see _iso_utc_now). A
# `Created` value is trusted as the sort key only if it matches this in full —
# a half-valid value like "2026-06-02 junk" would otherwise sort-compare as a
# raw string (the space sorts before 'T', so it would wrongly lead well-formed
# same-day stamps) instead of falling back to the filename date.
_CREATED_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def plan_recency_key(meta: dict, filename: str) -> tuple[str, str]:
    """Return (timestamp, filename) for a newest-first ordering, from
    already-parsed frontmatter.

    The primary key is the plan's `Created:` frontmatter — an ISO-8601 UTC
    stamp written once at save time (e.g. "2026-06-02T14:30:00Z"). Because
    those strings are fixed-width and lexically chronological, a plain string
    compare orders them correctly, and the time component gives precise
    *intra-day* ordering. This is the signal filename-date can't provide:
    every plan saved on the same day shares one `YYYY-MM-DD-` prefix, so the
    old filename sort fell back to slug-alphabetical within a day.

    `Created` (not file mtime/birthtime) is the source of truth because plan
    mutations — a `plan-do` status flip, a `plan-update`, a bulk `queue` set —
    rewrite the file via write_atomic/os.replace, which swaps in a fresh inode
    and resets both mtime and birthtime to the last-write time. A persisted
    frontmatter stamp survives those rewrites untouched.

    Fallback for plans that predate the field (or can't carry it): the
    filename's leading YYYY-MM-DD, padded to midnight UTC, so day-level
    ordering still holds and same-day ties break on the filename (the prior
    behavior). Covers byte-verbatim --from-path saves and non-.md siblings,
    which never get frontmatter.

    Takes the parsed `meta` so callers that already parsed the frontmatter
    (e.g. `crew queue list`) don't re-read the file; `_plan_sort_key` is the
    path-reading wrapper for callers that only hold a Path. Both share this
    one definition of "newest-first" so the queue and per-repo listings can't
    drift apart.
    """
    candidate = (meta.get("Created") or "").strip()
    created = candidate if _CREATED_STAMP_RE.match(candidate) else ""
    if not created:
        m = _DATE_PREFIX_RE.match(filename)
        created = f"{m.group(1)}T00:00:00Z" if m else ""
    return (created, filename)


def _plan_sort_key(path: Path) -> tuple[str, str]:
    """Path-reading wrapper around `plan_recency_key`: parse the file's
    frontmatter (empty on any read/parse failure, so the filename-date
    fallback still applies) and key off it."""
    try:
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (PlanKeeperCliError, OSError, UnicodeDecodeError):
        meta = {}
    return plan_recency_key(meta, path.name)


def plan_status(path: Path) -> str:
    """Return a plan's `Status:` frontmatter, lowercased; 'backlog' if absent.

    Blank/missing Status maps to 'backlog' to match plan-save's default, so a
    file with no frontmatter never silently vanishes from a status-filtered
    listing. A file that fails to parse (malformed frontmatter, unreadable
    bytes) is also treated as 'backlog' — one bad file must not break the whole
    listing, and 'backlog' keeps it visible in plan-do where it would be noticed.
    """
    try:
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (PlanKeeperCliError, OSError, UnicodeDecodeError):
        return "backlog"
    return (meta.get("Status") or "").strip().lower() or "backlog"


def emit_collision(target: Path) -> None:
    """Print structured collision diagnostic to stderr."""
    suggestion = find_unused_suffix(target)
    print("ERROR: collision", file=sys.stderr)
    print(f"existing: {target}", file=sys.stderr)
    print(f"suggestion: {suggestion}", file=sys.stderr)
