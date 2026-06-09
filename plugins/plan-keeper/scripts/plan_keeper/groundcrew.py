"""groundcrew shell-adapter glue: synthesize a stable ``plan-<n>`` id for each
plan, convert plans to the adapter's issue-dict shape, and mirror the
synthesized id into the plan's ``Ticket`` frontmatter for human traceability.
"""
import hashlib
import sys
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

def plankeeper_id(repo: str, stem: str) -> str:
    """Mint a plan-keeper ticket id for a plan: ``plan-<digits>``.

    Used as a **one-time seed generator**: plan-save (and, for legacy plans,
    the first ``crew fetch``) calls this once, stores the result in the plan's
    ``Plan-keeper Ticket`` frontmatter, and thereafter the id is only ever read
    back — never recomputed. groundcrew requires every ticket id to match
    ``/^[a-z][\\da-z]*-\\d+$/`` and reuses the bare id as a permanent key — the
    worktree dir (``<repo>-<id>``), the git branch (``<user>-<id>``), and the
    run-state filename all derive from it — so the minted value must conform;
    the 48-bit BLAKE2 digest of ``<repo>/<stem>`` does. The repo is part of the
    seed because the id carries no repo qualifier downstream — two same-named
    plans in different repos must mint distinct ids on first save. A mint-time
    collision is astronomically unlikely (and ``crew fetch`` fails loudly on a
    duplicate stored id rather than silently merging two plans onto one
    worktree).
    """
    digest = hashlib.blake2b(f"{repo}/{stem}".encode("utf-8"), digest_size=6).digest()
    return f"plan-{int.from_bytes(digest, 'big')}"


def _assert_no_plankeeper_id_collisions(issues: list[dict]) -> None:
    """Raise if two plans carry the same stored ``Plan-keeper Ticket``.

    A collision would make groundcrew treat two distinct plans as one ticket
    (shared worktree/branch/run-state) — a silent state-corrupting outcome.
    A mint-time collision is practically impossible given the hash space; this
    also catches a hand-copied plan file that duplicated another's stored id.
    The user breaks the tie by changing one plan's ``Plan-keeper Ticket``. Empty
    ids (an unminted plan that fetch is about to mint) are skipped.
    """
    seen: dict[str, str] = {}
    for issue in issues:
        ticket = issue["id"]
        if not ticket:
            continue
        path = issue["sourceRef"]["path"]
        if ticket in seen:
            raise PlanKeeperCliError(
                f"plan-keeper id collision: {seen[ticket]!r} and {path!r} "
                f"both carry {ticket!r}; change one plan's Plan-keeper Ticket "
                f"to break the tie",
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


def _mint_plankeeper_ticket_if_absent(path: Path) -> Optional[str]:
    """Return the plan's stored ``Plan-keeper Ticket``, minting and persisting
    one if absent.

    Mint-once: a present value is authoritative and is never recomputed or
    overwritten, so a renamed plan keeps its id (the whole point of the frozen
    id). Only the first call for a plan writes; steady-state fetches don't churn
    the file. Best-effort: a read/parse/write error is swallowed and returns
    None, so one unwritable file can't abort the whole fetch.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    # Only mint for real plan files (those that open with frontmatter), mirroring
    # _plan_to_issue's skip — never grow frontmatter onto a bare .md (e.g. a stray
    # README), which would make it look dispatchable.
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return None
    try:
        meta, body = parse_frontmatter(text)
    except PlanKeeperCliError:
        return None
    existing = (meta.get("Plan-keeper Ticket") or "").strip()
    if existing:
        return existing
    minted = plankeeper_id(_repo_for_plan(path), path.stem)
    meta["Plan-keeper Ticket"] = minted
    try:
        write_atomic(path, serialize_frontmatter(meta, body))
    except OSError:
        return None
    return minted


def _plan_to_issue(
    path: Path,
    index: Optional[dict[str, dict]] = None,
    warn_label: Optional[str] = None,
) -> Optional[dict]:
    """Convert one plan file to a shell-adapter issue dict. None if unparseable.

    Skips files that don't start with frontmatter (they're not plan-keeper
    plans even if they live under ~/plans/<repo>/ — e.g., a stray README).

    When ``index`` is given (a per-repo plan index from ``_build_repo_index``),
    the plan's ``Blocked-by`` refs are resolved into the issue's ``blockers``
    snapshot so groundcrew can gate dispatch; without it, ``blockers`` is empty.
    ``warn_label`` (fetch only) routes bad/deferred-ref notes to stderr.
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
    if index is not None:
        blockers, _ = _blockers_for_plan(meta, index, warn_label=warn_label)
    else:
        blockers = []
    # Read-only: the id is the stored, frozen Plan-keeper Ticket (minted at save
    # or by _mint_plankeeper_ticket_if_absent during fetch). May be "" for an
    # unminted plan; callers that need a guaranteed id mint first.
    return {
        "id": (meta.get("Plan-keeper Ticket") or "").strip(),
        "title": title,
        "description": body.rstrip(),
        "status": adapter_status,
        "repository": repo_name,
        "model": meta.get("Agent", "") or "claude",
        "assignee": "",
        "updatedAt": _iso_mtime(path),
        "blockers": blockers,
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


def _parse_blocked_by(value: str) -> list[str]:
    """Split a ``Blocked-by:`` value into prerequisite ticket IDs, in order.

    The value is a comma-separated scalar line; each item may carry an optional
    ``(filename)`` hint that is ignored. Empties are dropped.
    e.g. ``"plan-1 (auth), ENG-2"`` -> ``["plan-1", "ENG-2"]``.
    """
    ids: list[str] = []
    for item in value.split(","):
        ref = item.split("(", 1)[0].strip()
        if ref:
            ids.append(ref)
    return ids


def _build_repo_index(repo: str) -> dict[str, dict]:
    """Index every plan in ``repo`` (active + done/ + deferred/) by identity.

    Returns a dict mapping each plan's identity strings to a shared entry
    ``{id, title, status, location, key, blocked_by}``. A plan is keyed under
    every id it carries — its frozen ``Plan-keeper Ticket``, its ``Linear
    Ticket``, its ``Jira Ticket`` — plus the computed ``plankeeper_id(repo,
    stem)`` as a fallback so an unminted plan still resolves by the id it would
    be minted to. ``status`` is canonical (via ``_GROUNDCREW_STATUS_MAP``); a
    plan physically in ``done/`` reports ``done``. The canonical ``key`` (used by
    cycle detection and as the snapshot id) is the plan's stored plan-keeper id,
    or the computed one when unminted. Best-effort: unreadable/unparseable files
    are skipped.
    """
    index: dict[str, dict] = {}
    repo_root = storage.PLAN_ROOT / repo
    for location, base in (
        ("active", repo_root),
        ("done", repo_root / "done"),
        ("deferred", repo_root / "deferred"),
    ):
        # `is_dir()` (not `exists()`): a stray plain file literally named `done`
        # or `deferred` in the repo dir would pass exists() and then crash
        # `iterdir()` with NotADirectoryError, aborting the whole fetch.
        if not base.is_dir():
            continue
        for plan in sorted(base.iterdir()):
            if not plan.is_file() or not plan.name.endswith(".md"):
                continue
            try:
                meta, body = parse_frontmatter(plan.read_text(encoding="utf-8"))
            except (OSError, PlanKeeperCliError):
                continue
            computed = plankeeper_id(repo, plan.stem)
            pk = (meta.get("Plan-keeper Ticket") or "").strip()
            primary = pk or computed
            if location == "done":
                status = "done"
            else:
                status = _GROUNDCREW_STATUS_MAP.get(
                    meta.get("Status", "").strip(), "other"
                )
            entry = {
                "id": primary,
                "title": _extract_h1_safe(body) or plan.stem,
                "status": status,
                "location": location,
                "key": primary,
                "blocked_by": _parse_blocked_by((meta.get("Blocked-by") or "").strip()),
            }
            ids = {
                computed,
                pk,
                (meta.get("Linear Ticket") or "").strip(),
                (meta.get("Jira Ticket") or "").strip(),
            }
            # First-writer wins so a key shared across locations keeps the
            # highest-priority plan: active is iterated before done/ then
            # deferred/, matching `_resolve_crew_id`'s active-wins-over-archived
            # rule. This matters because an active plan and a same-stem archived
            # one compute the *same* plankeeper_id — without this guard the
            # archived (done) entry would overwrite the active one and a
            # Blocked-by ref would resolve to the wrong plan/status, flipping the
            # dispatch gate.
            for key in ids:
                if key and key not in index:
                    index[key] = entry
    return index


def _blockers_for_plan(
    meta: dict,
    index: dict[str, dict],
    warn_label: Optional[str] = None,
) -> tuple[list[dict], list[str]]:
    """Resolve a plan's ``Blocked-by`` refs into groundcrew blocker snapshots.

    Returns ``(blockers, unsatisfied_ids)`` where ``blockers`` is the list of
    ``{id, title, status}`` dicts to embed in the issue (status is always one of
    groundcrew's canonical values, so the shell-source schema accepts it), and
    ``unsatisfied_ids`` is the subset whose status is not ``done`` (what
    ``crew queue list`` reports as ``blockedBy``). A reference that resolves to
    no plan, or to a ``deferred/`` plan, is treated as **unsatisfied** and
    embedded with status ``other`` so groundcrew holds the dependent; when
    ``warn_label`` is set (fetch only), a ``note:`` is printed to stderr.
    """
    raw = (meta.get("Blocked-by") or "").strip()
    if not raw:
        return [], []
    blockers: list[dict] = []
    unsatisfied: list[str] = []
    for ref in _parse_blocked_by(raw):
        entry = index.get(ref)
        if entry is None:
            blockers.append({"id": ref, "title": "(unresolved)", "status": "other"})
            unsatisfied.append(ref)
            if warn_label is not None:
                print(
                    f"note: {warn_label}: Blocked-by {ref!r} matches no plan "
                    f"in this repo (holding)",
                    file=sys.stderr,
                )
            continue
        if entry["location"] == "deferred":
            blockers.append(
                {"id": entry["id"], "title": entry["title"], "status": "other"}
            )
            unsatisfied.append(entry["id"])
            if warn_label is not None:
                print(
                    f"note: {warn_label}: Blocked-by {ref!r} is a deferred plan "
                    f"(holding)",
                    file=sys.stderr,
                )
            continue
        blockers.append(
            {"id": entry["id"], "title": entry["title"], "status": entry["status"]}
        )
        if entry["status"] != "done":
            unsatisfied.append(entry["id"])
    return blockers, unsatisfied


def _detect_dependency_cycles(index: dict[str, dict]) -> list[list[str]]:
    """Return cyclic chains of canonical plan ids in a repo's Blocked-by graph.

    groundcrew can't see cycles (blockers are denormalized snapshots, not a
    traversable graph), so plan-keeper walks the in-repo graph itself. Nodes are
    the distinct plans (keyed by their canonical ``key``); edges follow each
    plan's ``blocked_by`` refs, mapped back to a node via the index. A cycle
    means mutual deadlock — every member stays held forever — so we surface it
    as a warning. Returns one list of node keys per detected back-edge.
    """
    nodes = {e["key"]: e for e in index.values()}
    ref_to_key = {ref: e["key"] for ref, e in index.items()}
    # adjacency: each node's successor node-keys (its resolvable prerequisites).
    adj: dict[str, list[str]] = {
        key: [
            nxt
            for ref in entry["blocked_by"]
            if (nxt := ref_to_key.get(ref)) is not None
        ]
        for key, entry in nodes.items()
    }
    cycles: list[list[str]] = []
    state: dict[str, int] = {}  # 0/unset = unvisited, 1 = on-stack, 2 = done

    # Iterative DFS (an explicit path + iterator stack) so a pathologically long
    # dependency chain can't blow Python's recursion limit and abort the fetch.
    for root in nodes:
        if state.get(root, 0) != 0:
            continue
        path: list[str] = [root]
        state[root] = 1
        iters: list = [iter(adj[root])]
        while iters:
            advanced = False
            for nxt in iters[-1]:
                seen = state.get(nxt, 0)
                if seen == 1:
                    cycles.append(path[path.index(nxt):] + [nxt])
                elif seen == 0:
                    state[nxt] = 1
                    path.append(nxt)
                    iters.append(iter(adj[nxt]))
                    advanced = True
                    break
            if not advanced:
                state[path[-1]] = 2
                path.pop()
                iters.pop()
    return cycles


# Active states a human owns once they pick a plan up outside the crew:
# in-progress (work) and in-review (review). backlog/todo are never
# locally-driven — they're either parked or queued-for-dispatch — so they
# stay visible regardless of Agent.
_LOCALLY_DRIVEN_STATES = frozenset({"in-progress", "in-review"})


def _is_locally_driven(path: Path) -> bool:
    """True for a plan a human is driving outside groundcrew: an active state
    (``in-progress`` or ``in-review``) with no ``Agent``.

    groundcrew claims the ``Agent`` at queue time (``crew queue`` / the
    plan-crew skill), so an active plan that still has no Agent was never
    queued — a human picked it up in their own session (e.g. via ``plan-do``).
    Once they own it, every active state it passes through is theirs, so it
    must stay invisible to ``crew fetch``: groundcrew would otherwise count an
    in-progress plan against its slot cap (surfacing it under "In progress (no
    local worktree)") or act on an in-review one, even though no crew worktree
    will ever exist for it. Read from the raw frontmatter, not the issue dict,
    because ``_plan_to_issue`` coerces an empty Agent to the ``claude`` default
    and so can no longer tell the two apart. Best-effort: an
    unreadable/unparseable file is not treated as locally driven (it's filtered
    elsewhere anyway).
    """
    try:
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, PlanKeeperCliError):
        return False
    return (
        meta.get("Status", "").strip() in _LOCALLY_DRIVEN_STATES
        and not meta.get("Agent", "").strip()
    )


def _collect_crew_issues() -> list[dict]:
    """Every active plan under ``~/plans/<repo>/*.md`` as shell-adapter issues.

    One level deep — ``done/`` and ``deferred/`` are excluded (they're not
    dispatchable). Plans being driven locally (in-progress with no Agent — see
    ``_is_locally_driven``) are excluded too, so groundcrew never tracks work a
    human picked up in their own session. Mints a frozen ``Plan-keeper Ticket``
    for any plan that lacks one (mint-once, no overwrite) so every emitted issue
    has a stable id. Shared by ``crew fetch`` (which then asserts no id
    collisions) and ``crew install``'s post-patch data-path check, so both see
    the exact same plan set.
    """
    issues: list[dict] = []
    if not storage.PLAN_ROOT.exists():
        return issues
    for repo_entry in sorted(storage.PLAN_ROOT.iterdir()):
        if not repo_entry.is_dir() or repo_entry.name.startswith("."):
            continue
        # Build the repo's plan index once so each plan's Blocked-by refs
        # resolve to a live status snapshot; warn on any dependency cycle
        # (groundcrew can't see cycles — denormalized blockers, no graph). The
        # index keys unminted plans by their computed id, which is exactly what
        # the mint below assigns, so build order doesn't affect resolution.
        index = _build_repo_index(repo_entry.name)
        for cycle in _detect_dependency_cycles(index):
            print(
                f"note: dependency cycle in repo {repo_entry.name!r}: "
                + " -> ".join(cycle),
                file=sys.stderr,
            )
        for plan in sorted(repo_entry.iterdir()):
            if not plan.is_file() or not plan.name.endswith(".md"):
                continue
            if _is_locally_driven(plan):
                continue
            _mint_plankeeper_ticket_if_absent(plan)
            issue = _plan_to_issue(plan, index=index, warn_label=plan.stem)
            # Skip a plan whose id couldn't be minted/persisted (empty id) — an
            # empty id is groundcrew's worktree/branch/run-state key and would
            # corrupt the adapter. It'll be retried on the next fetch.
            if issue is not None and issue["id"]:
                issues.append(issue)
    return issues


def _resolve_crew_id(plan_id: str) -> Optional[dict]:
    """The issue dict whose stored ``Plan-keeper Ticket`` == ``plan_id``, or None.

    Reads each plan's stored, frozen id across active, then ``done/``, then
    ``deferred/`` (a live plan wins over an archived one sharing its stem).
    The single resolver behind both ``crew get`` and ``crew start``: because
    they share this lookup, "if get can find it, start can mark it" holds by
    construction. Read-only — it never mints; an id only reaches groundcrew via
    a prior ``fetch`` (which mints), so every resolvable plan already has one.
    An unminted plan (empty id) can never match a real ``plan_id``.
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
                if issue is not None and issue["id"] and issue["id"] == plan_id:
                    # Rebuild with the repo index so `crew get` carries the same
                    # blocker snapshot as `crew fetch` (or a held plan could slip
                    # through the resolveOne path). No warn label — get is quiet.
                    index = _build_repo_index(_repo_for_plan(plan))
                    return _plan_to_issue(plan, index=index)
    return None
