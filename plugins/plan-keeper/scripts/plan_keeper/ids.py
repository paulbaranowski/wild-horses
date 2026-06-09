"""Plan identity: the single home for how a plan-keeper id is computed, derived
from a path, and minted-once into frontmatter.

Every id-creation site funnels through here so the three concerns each live in
exactly one place: the **algorithm** (``plankeeper_id`` + ``ID_DIGEST_SIZE``),
the **seed derivation** (``repo_for_plan`` + ``id_for_path``), and the
**mint-once store** (``ensure_id`` / ``mint_into_path_if_absent``). Keeping the
algorithm behind a single constant is deliberate: changing the id length is a
one-line edit of ``ID_DIGEST_SIZE`` rather than a hunt across call sites.

Dependency direction: ``ids -> storage, frontmatter`` (both leaf-ward); the
adapter (``groundcrew``) and the CLI import from here, never the reverse.
"""
import hashlib
from pathlib import Path
from typing import Optional

from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.frontmatter import parse_frontmatter, serialize_frontmatter
from plan_keeper.storage import write_atomic

# The one knob for id length. A `digest_size` of N bytes yields an N*8-bit
# integer, so the `plan-<digits>` id is at most ceil(N*8 * log10(2)) digits.
# 6 bytes (48-bit) -> up to ~15 digits. This is the seam a future "shorter ids"
# change flips; nothing else needs to move.
ID_DIGEST_SIZE = 6


def plankeeper_id(repo: str, stem: str) -> str:
    """Mint a plan-keeper ticket id for a plan: ``plan-<digits>``.

    Used as a **one-time seed generator**: plan-save (and, for legacy plans,
    the first ``crew fetch``) calls this once, stores the result in the plan's
    ``Plan-keeper Ticket`` frontmatter, and thereafter the id is only ever read
    back — never recomputed. groundcrew requires every ticket id to match
    ``/^[a-z][\\da-z]*-\\d+$/`` and reuses the bare id as a permanent key — the
    worktree dir (``<repo>-<id>``), the git branch (``<user>-<id>``), and the
    run-state filename all derive from it — so the minted value must conform;
    the ``ID_DIGEST_SIZE``-byte BLAKE2 digest of ``<repo>/<stem>`` does. The repo
    is part of the seed because the id carries no repo qualifier downstream — two
    same-named plans in different repos must mint distinct ids on first save. A
    mint-time collision is astronomically unlikely (and ``crew fetch`` fails
    loudly on a duplicate stored id rather than silently merging two plans onto
    one worktree).
    """
    digest = hashlib.blake2b(
        f"{repo}/{stem}".encode("utf-8"), digest_size=ID_DIGEST_SIZE
    ).digest()
    return f"plan-{int.from_bytes(digest, 'big')}"


def repo_for_plan(path: Path) -> str:
    """The repo a plan belongs to: its parent dir name, or the grandparent
    when the plan lives under `done/` or `deferred/`. Single source of truth
    so the synthesized id is stable across a plan's move into those subdirs."""
    parent = path.parent
    if parent.name in {"done", "deferred"}:
        return parent.parent.name
    return parent.name


def id_for_path(path: Path) -> str:
    """The deterministic plan-keeper id a plan at ``path`` mints to.

    The single seed-derivation chokepoint: every site that needs a plan's id
    from its path goes through here, so the ``(repo, stem)`` assembly lives in
    one place. Pure — computes, never stores.
    """
    return plankeeper_id(repo_for_plan(path), path.stem)


def ensure_id(meta: dict, path: Path) -> str:
    """Mint-once into a parsed ``meta`` dict: return the plan's stored
    ``Plan-keeper Ticket``, setting it to ``id_for_path(path)`` if absent.

    The caller owns persistence — this only mutates the in-memory ``meta`` so it
    composes with whatever write the caller was already doing (a queue-set
    rewrite, a fetch-time stamp). A present value is authoritative and never
    overwritten, so a renamed plan keeps its frozen id.
    """
    existing = (meta.get("Plan-keeper Ticket") or "").strip()
    if existing:
        return existing
    minted = id_for_path(path)
    meta["Plan-keeper Ticket"] = minted
    return minted


def mint_into_path_if_absent(path: Path) -> Optional[str]:
    """Return the plan's stored ``Plan-keeper Ticket``, minting and persisting
    one if absent.

    The file-reading wrapper around ``ensure_id`` for callers that hold only a
    path (``crew fetch``). Mint-once: a present value is never recomputed or
    overwritten, and only the first call for a plan writes — steady-state
    fetches don't churn the file. Best-effort: a read/parse/write error is
    swallowed and returns None, so one unwritable file can't abort the whole
    fetch.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
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
    before = (meta.get("Plan-keeper Ticket") or "").strip()
    minted = ensure_id(meta, path)
    if before:
        return minted  # already minted — no write needed
    try:
        write_atomic(path, serialize_frontmatter(meta, body))
    except OSError:
        return None
    return minted
