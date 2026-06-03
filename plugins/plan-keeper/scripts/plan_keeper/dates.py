"""Tiny date/time helpers.

Kept in their own leaf module so both ``storage`` (sort-key fallback) and
``frontmatter`` (default ``Created`` stamp) can depend on them without forming
a ``storage`` ↔ ``frontmatter`` import cycle.
"""
import os
from datetime import date, datetime, timezone

from plan_keeper.errors import PlanKeeperCliError


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_from_stat(st: os.stat_result) -> str:
    """Convert a file's stat to an ISO-8601 UTC stamp from its birthtime.

    Prefers `st_birthtime` (macOS/BSD); falls back to `st_mtime` where birthtime
    is unavailable (many Linux filesystems). The single home for the
    birthtime→stamp format + fallback, shared by `backfill-created` and the `.md`
    `--from-path` move path so both stamp a pre-existing file's age identically.
    """
    ts = getattr(st, "st_birthtime", None)
    if ts is None:
        ts = st.st_mtime
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date_arg(s: str) -> str:
    """Validate a YYYY-MM-DD argument and return it as an ISO string."""
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError as e:
        raise PlanKeeperCliError(f"invalid date {s!r}: {e}", code=2)
