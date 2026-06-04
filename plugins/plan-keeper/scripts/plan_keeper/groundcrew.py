"""groundcrew shell-adapter glue: synthesize a stable ``plan-<n>`` id for each
plan, convert plans to the adapter's issue-dict shape, and mirror the
synthesized id into the plan's ``Ticket`` frontmatter for human traceability.
"""
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from plan_keeper import storage
from plan_keeper.dates import _iso_utc_now
from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.frontmatter import parse_frontmatter, serialize_frontmatter
from plan_keeper.storage import write_atomic

# Translates plan-keeper's on-disk Status: vocabulary to the groundcrew shell
# adapter's enum. `backlog` is fetched but never dispatched (confirm one via
# `crew status <id>`; the aggregate `crew status` Queue lists only `todo`).
# Anything else (typos, future values) falls through to "other".
_GROUNDCREW_STATUS_MAP = {
    "backlog": "other",
    "todo": "todo",
    "in-progress": "in-progress",
    "in-review": "in-review",
    "done": "done",
}

GROUNDCREW_TICKET_SYSTEM = "groundcrew"


def groundcrew_id(repo: str, stem: str) -> str:
    """Synthesize a groundcrew ticket id for a plan: ``plan-<digits>``.

    groundcrew requires every ticket id to match ``/^[a-z][\\da-z]*-\\d+$/``
    and reuses the bare id as a permanent key — the worktree dir
    (``<repo>-<id>``), the git branch (``<user>-<id>``), and the run-state
    filename all derive from it. Plan filenames (e.g.
    ``2026-04-30-notification-service-typed-models``) don't fit that shape,
    so we hash a stable identity into a conforming id.

    Stateless by design: the id is a pure function of ``(repo, stem)``, so
    ``fetch`` and ``resolve-one`` agree with no stored mapping, and the id
    stays stable across a plan's lifecycle (status flip, move to ``done/``,
    which change neither the repo nor the stem). The repo is part of the key
    because the id carries no repo qualifier downstream — two same-named
    plans in different repos must not collide. Uses a 48-bit BLAKE2 digest:
    plenty of headroom for a personal plan set, and ``cmd_groundcrew_fetch``
    fails loudly on the astronomically-unlikely collision rather than
    silently merging two plans onto one worktree.
    """
    digest = hashlib.blake2b(f"{repo}/{stem}".encode("utf-8"), digest_size=6).digest()
    return f"plan-{int.from_bytes(digest, 'big')}"


def _assert_no_groundcrew_id_collisions(issues: list[dict]) -> None:
    """Raise if two plans synthesized the same groundcrew id.

    A collision would make groundcrew treat two distinct plans as one ticket
    (shared worktree/branch/run-state) — a silent state-corrupting outcome.
    The hash space makes this practically impossible, but if it ever happens
    the user can break the tie by renaming one plan file.
    """
    seen: dict[str, str] = {}
    for issue in issues:
        ticket = issue["id"]
        path = issue["sourceRef"]["path"]
        if ticket in seen:
            raise PlanKeeperCliError(
                f"groundcrew id collision: {seen[ticket]!r} and {path!r} "
                f"both map to {ticket!r}; rename one plan file to break the tie",
                code=2,
            )
        seen[ticket] = path


def _repo_for_plan(path: Path) -> str:
    """The repo a plan belongs to: its parent dir name, or the grandparent
    when the plan lives under `done/` or `deferred/`. Single source of truth
    so the synthesized id is stable across a plan's move into those subdirs."""
    parent = path.parent
    if parent.name in {"done", "deferred"}:
        return parent.parent.name
    return parent.name


def _apply_groundcrew_ticket(meta: dict[str, str], ticket: str) -> bool:
    """Claim the `Ticket` / `Ticket System` pair for groundcrew on an in-hand
    meta dict, returning True iff it changed.

    Claims the pair only when it's empty or already ``groundcrew``; a
    ``linear``/``jira`` reference (written by plan-linear/plan-jira) — or an orphan
    ``Ticket`` under no system — is left untouched, so a tracked plan keeps
    its real reference and still dispatches via the recomputed id.
    """
    system = (meta.get("Ticket System") or "").strip().lower()
    if system == GROUNDCREW_TICKET_SYSTEM:
        if meta.get("Ticket") == ticket:
            return False  # already current
    elif system or meta.get("Ticket"):
        return False  # another tracker (or an orphan Ticket) owns these fields
    meta["Ticket"] = ticket
    meta["Ticket System"] = GROUNDCREW_TICKET_SYSTEM
    return True


def _stamp_groundcrew_ticket(path: Path, ticket: str) -> None:
    """Mirror the synthesized id into the plan's `Ticket` / `Ticket System`
    frontmatter (the same pair plan-linear/plan-jira use), so a human can see which plan
    a ``plan-<n>`` id maps to.

    Display-only and self-healing: ``groundcrew_id()`` stays the canonical id,
    so ``resolve-one`` never trusts these fields — it recomputes the hash.
    Rewrites only when absent or stale (see _apply_groundcrew_ticket), so
    steady-state fetches don't churn the file. Best-effort: a read/parse error
    is swallowed so one unwritable file can't abort the whole fetch.
    """
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, PlanKeeperCliError):
        return
    if _apply_groundcrew_ticket(meta, ticket):
        write_atomic(path, serialize_frontmatter(meta, body))


def _plan_to_issue(path: Path) -> Optional[dict]:
    """Convert one plan file to a shell-adapter issue dict. None if unparseable.

    Skips files that don't start with frontmatter (they're not plan-keeper
    plans even if they live under ~/plans/<repo>/ — e.g., a stray README).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return None
    try:
        meta, body = parse_frontmatter(text)
    except PlanKeeperCliError:
        return None
    raw_status = meta.get("Status", "").strip()
    adapter_status = _GROUNDCREW_STATUS_MAP.get(raw_status, "other")
    title = _extract_h1_safe(body) or path.stem
    # repo is the grandparent for archived/paused plans (done/, deferred/), so
    # `crew get` reports the real repo, not "done"/"deferred".
    repo_name = _repo_for_plan(path)
    return {
        "id": groundcrew_id(repo_name, path.stem),
        "title": title,
        "description": body.rstrip(),
        "status": adapter_status,
        "repository": repo_name,
        "model": meta.get("Agent", "") or "claude",
        "assignee": "",
        "updatedAt": _iso_mtime(path),
        "blockers": [],
        "hasMoreBlockers": False,
        "sourceRef": {"path": str(path.resolve())},
    }


def _extract_h1_safe(body: str) -> str:
    """Like _extract_h1 but returns '' instead of raising on missing heading.

    The push-to-Linear flow requires an H1 (titles are mandatory); the fetch
    flow is best-effort and falls back to the filename stem.
    """
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
        if s.startswith("## "):
            return s[3:].strip()
    return ""


def _iso_mtime(path: Path) -> str:
    """File mtime as ISO-8601 UTC, used as the issue's updatedAt."""
    try:
        ts = path.stat().st_mtime
    except OSError:
        return _iso_utc_now()
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _collect_crew_issues() -> list[dict]:
    """Every active plan under ``~/plans/<repo>/*.md`` as shell-adapter issues.

    One level deep — ``done/`` and ``deferred/`` are excluded (they're not
    dispatchable). Shared by ``crew fetch`` (which then asserts no id
    collisions and stamps the Ticket frontmatter) and ``crew install``'s
    post-patch data-path check, so both see the exact same plan set.
    """
    issues: list[dict] = []
    if not storage.PLAN_ROOT.exists():
        return issues
    for repo_entry in sorted(storage.PLAN_ROOT.iterdir()):
        if not repo_entry.is_dir() or repo_entry.name.startswith("."):
            continue
        for plan in sorted(repo_entry.iterdir()):
            if not plan.is_file() or not plan.name.endswith(".md"):
                continue
            issue = _plan_to_issue(plan)
            if issue is not None:
                issues.append(issue)
    return issues


def _discover_plan_repos() -> list[str]:
    """The repo directory names one level under ``~/plans/`` (sorted).

    These seed the ``knownRepositories`` allow-list that ``crew install``
    writes into the groundcrew config. Hidden dirs and non-directories are
    skipped; ``done``/``deferred`` are *not* special here — they only ever
    appear nested inside a repo dir, never as a top-level repo.
    """
    if not storage.PLAN_ROOT.exists():
        return []
    return sorted(
        entry.name
        for entry in storage.PLAN_ROOT.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    )


def _resolve_crew_id(plan_id: str) -> Optional[dict]:
    """The issue dict whose synthesized id == ``plan_id``, or None if none.

    Recomputes each plan's id across active, then ``done/``, then
    ``deferred/`` (a live plan wins over an archived one sharing its stem).
    The single resolver behind both ``crew get`` and ``crew start``: because
    they share this lookup, "if get can find it, start can mark it" holds by
    construction — the two can never diverge on which id maps to which file.
    """
    if not storage.PLAN_ROOT.exists():
        return None
    for repo_entry in sorted(storage.PLAN_ROOT.iterdir()):
        if not repo_entry.is_dir() or repo_entry.name.startswith("."):
            continue
        for subdir in (repo_entry, repo_entry / "done", repo_entry / "deferred"):
            if not subdir.exists():
                continue
            for plan in sorted(subdir.iterdir()):
                if not plan.is_file() or not plan.name.endswith(".md"):
                    continue
                issue = _plan_to_issue(plan)
                if issue is not None and issue["id"] == plan_id:
                    return issue
    return None
