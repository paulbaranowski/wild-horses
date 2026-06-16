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
import re
from pathlib import Path
from typing import Optional

from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.frontmatter import parse_frontmatter, serialize_frontmatter
from plan_keeper.storage import write_atomic

# The one knob for id length. A `digest_size` of N bytes yields an N*8-bit
# integer, so the `plan-<digits>` id is at most ceil(N*8 * log10(2)) digits.
# 4 bytes (32-bit) -> up to 10 digits (e.g. `plan-2950118472`); the prior
# 6 bytes (48-bit) ran to ~15 (`plan-195296912509085`), which made groundcrew's
# worktree dirs, branches, and run-state filenames hard to read.
#
# Collisions follow the birthday bound over the 2**(8*N) space, caught loudly
# (never silently merged) by `_assert_no_plankeeper_id_collisions` at crew fetch.
# At 32-bit that risk is ~0.0001% at 100 lifetime plans, ~0.01% at 1000, and
# still only ~1% at 10k. This constant is the dial for more or less margin:
# 3 -> 24-bit / 8 digits (tighter, ~3% at 1000), 5 -> 40-bit / 13 digits (more
# headroom). This is the seam the "shorter ids" change flips; nothing else moves.
ID_DIGEST_SIZE = 4

# The exact shape `plankeeper_id` produces and the only shape downstream
# (groundcrew worktree dirs, branches, run-state filenames) is built around.
# Used as the "is this field already a real minted id?" test in `ensure_id` so
# placeholders like the legacy `Ticket: TBD` sentinel — which the lazy
# frontmatter migration carries forward into `Plan-keeper Ticket` — are treated
# as absent and re-minted rather than honored as authoritative. Anything not
# matching this (empty, `TBD`, a Linear `ENG-123` pasted into the wrong field,
# a typo) loses to a fresh mint; a properly-minted value stays frozen.
_MINTED_ID_RE = re.compile(r"plan-\d+")


def _is_minted_plankeeper_id(value: str) -> bool:
    """True iff ``value`` is the canonical shape ``plankeeper_id`` mints to.

    Used by ``ensure_id`` to gate the "already minted" branch — see
    ``_MINTED_ID_RE`` for the rationale (TBD and other non-conforming values
    must lose to a fresh mint, not be treated as authoritative).
    """
    return bool(_MINTED_ID_RE.fullmatch(value))


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
    mint-time collision is unlikely but not impossible at this digest size (see
    ``ID_DIGEST_SIZE`` for the birthday-bound characteristics); ``crew fetch``
    catches any duplicate stored id loudly rather than silently merging two plans
    onto one worktree.
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
    ``Plan-keeper Ticket``, minting one (``id_for_path(path)``) if the field
    isn't already a real ``plan-<digits>`` id.

    The caller owns persistence — this only mutates the in-memory ``meta`` so it
    composes with whatever write the caller was already doing (a queue-set
    rewrite, a fetch-time stamp). A canonically-shaped value is authoritative
    and never overwritten, so a renamed plan keeps its frozen id. Anything else
    (empty, the legacy ``TBD`` placeholder, a Linear id pasted into the wrong
    field, a typo) loses to a fresh mint — see ``_is_minted_plankeeper_id``.
    """
    existing = (meta.get("Plan-keeper Ticket") or "").strip()
    if _is_minted_plankeeper_id(existing):
        return existing
    minted = id_for_path(path)
    meta["Plan-keeper Ticket"] = minted
    return minted


def mint_into_path_if_absent(path: Path) -> Optional[str]:
    """Return the plan's stored ``Plan-keeper Ticket``, minting and persisting
    one if it isn't already a real ``plan-<digits>`` id.

    The file-reading wrapper around ``ensure_id`` for callers that hold only a
    path (``crew fetch``). A canonically-shaped id is never recomputed or
    overwritten, and only the call that changes the value writes — steady-state
    fetches don't churn the file. A placeholder like the legacy ``TBD`` sentinel
    is rewritten in place with a fresh mint (see ``ensure_id``). Best-effort: a
    read/parse/write error is swallowed and returns None, so one unwritable file
    can't abort the whole fetch.
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
    if before == minted:
        return minted  # field already held the canonical id — no write needed
    try:
        write_atomic(path, serialize_frontmatter(meta, body))
    except OSError:
        return None
    return minted
