"""groundcrew shell-adapter glue: convert plans to the adapter's issue-dict
shape and mirror each plan's frozen id into its ``Plan-keeper Ticket``
frontmatter for human traceability.

Plan identity itself — how a ``plan-<n>`` id is computed, derived from a path,
and minted-once — lives in ``plan_keeper.ids``; this module imports those
helpers (``plankeeper_id``, ``id_for_path``, ``mint_into_path_if_absent``)
rather than owning the algorithm.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from plan_keeper import storage
from plan_keeper.dates import _iso_utc_now
from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.frontmatter import parse_frontmatter
from plan_keeper.ids import (
    id_for_path,
    mint_into_path_if_absent,
    repo_for_plan,
)
from plan_keeper.types import Blocker, CrewIssue, IndexEntry

# Re-export so existing callers/tests can read `groundcrew.plankeeper_id`. The
# `as plankeeper_id` form marks this an intentional re-export (not a stray
# unused import); the single definition lives in `ids`.
from plan_keeper.ids import plankeeper_id as plankeeper_id

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

def _assert_no_plankeeper_id_collisions(issues: list[CrewIssue]) -> None:
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


def _plan_to_issue(
    path: Path,
    index: Optional[dict[str, IndexEntry]] = None,
    warn_label: Optional[str] = None,
) -> Optional[CrewIssue]:
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
    repo_name = repo_for_plan(path)
    if index is not None:
        blockers, _ = _blockers_for_plan(meta, index, warn_label=warn_label)
    else:
        blockers = []
    # Read-only: the id is the stored, frozen Plan-keeper Ticket (minted at save
    # or by ids.mint_into_path_if_absent during fetch). May be "" for an
    # unminted plan; callers that need a guaranteed id mint first.
    return {
        "id": (meta.get("Plan-keeper Ticket") or "").strip(),
        "title": title,
        "description": body.rstrip(),
        "status": adapter_status,
        "repository": repo_name,
        # groundcrew's shellIssueSchema declares `agent: z.string().nullable()`
        # — a required key with a nullable value. Emit the plan's Agent: tag, or
        # null when it has none. crew fetch never surfaces agent-less plans (see
        # `_is_unassigned`), so a null agent only reaches here via `crew get`,
        # where null honestly says "unassigned" rather than fabricating a
        # "claude" default for a plan no one has claimed.
        "agent": meta.get("Agent", "").strip() or None,
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


def _build_repo_index(repo: str) -> dict[str, IndexEntry]:
    """Index every plan in ``repo`` (active + done/ + deferred/) by identity.

    Returns a dict mapping each plan's identity strings to a shared entry
    ``{id, title, status, location, key, blocked_by}``. A plan is keyed under
    every id it carries — its frozen ``Plan-keeper Ticket``, its ``Linear
    Ticket``, its ``Jira Ticket`` — plus the computed id (``ids.id_for_path``)
    as a fallback so an unminted plan still resolves by the id it would be
    minted to. ``status`` is canonical (via ``_GROUNDCREW_STATUS_MAP``); a
    plan physically in ``done/`` reports ``done``. The canonical ``key`` (used by
    cycle detection and as the snapshot id) is the plan's stored plan-keeper id,
    or the computed one when unminted. Best-effort: unreadable/unparseable files
    are skipped.
    """
    index: dict[str, IndexEntry] = {}
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
            computed = id_for_path(plan)
            pk = (meta.get("Plan-keeper Ticket") or "").strip()
            primary = pk or computed
            if location == "done":
                status = "done"
            else:
                status = _GROUNDCREW_STATUS_MAP.get(
                    meta.get("Status", "").strip(), "other"
                )
            entry: IndexEntry = {
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
            # one compute the *same* id — without this guard the
            # archived (done) entry would overwrite the active one and a
            # Blocked-by ref would resolve to the wrong plan/status, flipping the
            # dispatch gate.
            for key in ids:
                if key and key not in index:
                    index[key] = entry
    return index


def _blockers_for_plan(
    meta: dict,
    index: dict[str, IndexEntry],
    warn_label: Optional[str] = None,
) -> tuple[list[Blocker], list[str]]:
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
    blockers: list[Blocker] = []
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


def _detect_dependency_cycles(index: dict[str, IndexEntry]) -> list[list[str]]:
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


def _is_unassigned(path: Path) -> bool:
    """True for a plan with no ``Agent:`` tag — groundcrew's signal that no one
    has claimed it for dispatch.

    The ``Agent`` tag is the single dispatch gate: groundcrew (via the
    plan-crew skill / ``crew queue``) writes it when a plan is promoted to the
    queue, and a human driving a plan in their own session (e.g. via
    ``plan-do``) leaves it cleared. Either way, an agent-less plan is not
    groundcrew's to run, so ``crew fetch`` skips it in *every* status — a parked
    ``backlog`` plan, a human-picked-up ``in-progress`` one, and an
    ``in-review`` one a human owns are all hidden alike. (This subsumes the
    older active-state-only "locally driven" rule.) Read from raw frontmatter,
    not the issue dict, so the skip happens before ``_collect_crew_issues``
    mints an id into a plan it's about to drop. Best-effort: an
    unreadable/unparseable file is not treated as unassigned (it's filtered
    elsewhere anyway).
    """
    try:
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, PlanKeeperCliError):
        return False
    return not meta.get("Agent", "").strip()


def _collect_crew_issues() -> list[CrewIssue]:
    """Every active plan under ``~/plans/<repo>/*.md`` as shell-adapter issues.

    One level deep — ``done/`` and ``deferred/`` are excluded (they're not
    dispatchable). Agent-less plans (no ``Agent:`` tag — see ``_is_unassigned``)
    are excluded too, in every status: groundcrew only tracks plans explicitly
    assigned for dispatch, never untriaged backlog or work a human picked up in
    their own session. Mints a frozen ``Plan-keeper Ticket`` for any plan that
    lacks one (mint-once, no overwrite) so every emitted issue has a stable id.
    Shared by ``crew fetch`` (which then asserts no id collisions) and
    ``crew install``'s post-patch data-path check, so both see the exact same
    plan set.
    """
    issues: list[CrewIssue] = []
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
            if _is_unassigned(plan):
                continue
            mint_into_path_if_absent(plan)
            issue = _plan_to_issue(plan, index=index, warn_label=plan.stem)
            # Skip a plan whose id couldn't be minted/persisted (empty id) — an
            # empty id is groundcrew's worktree/branch/run-state key and would
            # corrupt the adapter. It'll be retried on the next fetch.
            if issue is not None and issue["id"]:
                issues.append(issue)
    return issues


def _resolve_crew_id(plan_id: str) -> Optional[CrewIssue]:
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
                    index = _build_repo_index(repo_for_plan(plan))
                    return _plan_to_issue(plan, index=index)
    return None
