"""Plan-file frontmatter: the canonical field set, parse/serialize round-trip,
Kind validation, and default-field injection on save.
"""
from typing import Optional

from plan_keeper.dates import _iso_utc_now
from plan_keeper.errors import PlanKeeperCliError

# Order matters in the output — keep this canonical so callers see a stable shape.
# Each tracker gets its own id field: a plan can carry a plan-keeper id (always,
# minted once and frozen), a Linear id, and a Jira id simultaneously. This
# replaced the single `Ticket` / `Ticket System` pair (see _migrate_legacy_ticket_fields).
_FRONTMATTER_FIELDS = (
    "Plan-keeper Ticket", "Linear Ticket", "Jira Ticket",
    "Completed on", "Agent", "Status", "Kind", "Created",
)

# Historical single-tracker schema: one `Ticket` value qualified by a
# `Ticket System` ("groundcrew"/"linear"/"jira"/empty). The multi-tracker schema
# gives each system its own field; _migrate_legacy_ticket_fields maps a legacy
# pair to the matching field on read (and the next write persists it — lazy
# migration). An unrecognized system is left untouched (no data loss).
_LEGACY_SYSTEM_TO_FIELD = {
    "": "Plan-keeper Ticket",
    "groundcrew": "Plan-keeper Ticket",
    "linear": "Linear Ticket",
    "jira": "Jira Ticket",
}

# `Kind` classifies the *document type* (orthogonal to Status, which is the
# lifecycle). The values are ordered by pipeline position, idea → ready-to-build.
# plan-save infers and writes it; plan-do reads it as its primary routing signal.
# Canonical definitions + the plan-do routing map live in plan-kinds.md.
VALID_KINDS = ("idea", "prd", "design", "spec", "exec-plan")


def validate_kind(value: str) -> str:
    """Return a normalized (lowercased) Kind, or raise if not in VALID_KINDS."""
    normalized = value.strip().lower()
    if normalized not in VALID_KINDS:
        raise PlanKeeperCliError(
            f"invalid Kind {value!r}: must be one of "
            + ", ".join(VALID_KINDS),
            code=2,
        )
    return normalized


def _migrate_legacy_ticket_fields(meta: dict[str, str]) -> None:
    """In-place: translate a legacy ``Ticket`` / ``Ticket System`` pair into the
    matching per-system field, then drop the legacy keys.

    Mutates ``meta`` so every reader transparently sees the multi-tracker schema;
    the next ``serialize_frontmatter`` persists it (lazy migration on next write).
    Fill-if-absent: a new field already present wins (a half-migrated file keeps
    its new value). An unrecognized system is left wholly untouched — no data
    loss, no misfiling into an id field.
    """
    if "Ticket" not in meta and "Ticket System" not in meta:
        return
    ticket = (meta.get("Ticket") or "").strip()
    system = (meta.get("Ticket System") or "").strip().lower()
    field = _LEGACY_SYSTEM_TO_FIELD.get(system)
    if field is None:
        return  # unrecognized system: preserve the legacy pair as foreign fields
    meta.pop("Ticket", None)
    meta.pop("Ticket System", None)
    if ticket and not meta.get(field):
        meta[field] = ticket


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a plan file into (frontmatter_dict, body_text).

    Frontmatter is the optional top block delimited by `---` lines. Each
    inner line is "Key: value" (whitespace around the colon ignored).

    Returns:
        (meta, body) where meta ALWAYS contains the fields in
        _FRONTMATTER_FIELDS (empty string when absent, or when the file has
        no frontmatter at all), PLUS any other fields present in the file,
        preserved verbatim. Foreign fields (e.g. Obsidian `tags:`) are kept
        so a round-trip through serialize_frontmatter doesn't silently drop
        them. body is the text after the closing `---` (or all of `text` if
        no frontmatter).

    Raises:
        PlanKeeperCliError(code=5) on malformed frontmatter (no closing `---`
        or a line missing its `:`). Unknown field *names* are no longer an
        error — they pass through. The trade-off is that a typo in a managed
        field (e.g. `Staus:`) is preserved as a foreign field rather than
        flagged; callers that care validate values at set time.
    """
    meta = {k: "" for k in _FRONTMATTER_FIELDS}
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return meta, text
    lines = text.split("\n")
    # First line is "---". Find the closing "---".
    closing_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_idx = i
            break
    if closing_idx is None:
        raise PlanKeeperCliError("malformed frontmatter: no closing '---'", code=5)
    for line in lines[1:closing_idx]:
        if not line.strip():
            continue
        if ":" not in line:
            raise PlanKeeperCliError(
                f"malformed frontmatter: missing ':' on line {line!r}", code=5
            )
        key, _, value = line.partition(":")
        # Preserve every field — known ones overwrite their seeded default,
        # foreign ones are appended so serialize_frontmatter can round-trip
        # them instead of silently dropping them on the next rewrite.
        meta[key.strip()] = value.strip()
    body = "\n".join(lines[closing_idx + 1 :])
    # Drop a single leading blank line if present (cosmetic — frontmatter
    # is usually followed by a blank line before the H1). Handle both
    # LF and CRLF forms so a CRLF-flavoured file round-trips cleanly.
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    _migrate_legacy_ticket_fields(meta)
    return meta, body


def serialize_frontmatter(meta: dict[str, str], body: str) -> str:
    """Compose a plan-file text with frontmatter on top, then body.

    Fields with empty-string value are omitted (so a "Completed on" that
    was never set stays out of the file entirely). Managed fields
    (_FRONTMATTER_FIELDS) are emitted first in canonical order, then any
    foreign fields in the order they appear in `meta` (i.e. file order, since
    parse_frontmatter appends them) — so plan-keeper round-trips fields it
    doesn't manage rather than dropping them.

    If every field (managed and foreign) is empty, returns body unchanged (no
    frontmatter block written). This preserves the "bare plan has no `---`"
    invariant.
    """
    managed = [(k, meta.get(k, "")) for k in _FRONTMATTER_FIELDS]
    foreign = [(k, v) for k, v in meta.items() if k not in _FRONTMATTER_FIELDS]
    non_empty = [(k, v) for k, v in (*managed, *foreign) if v]
    if not non_empty:
        return body
    lines = ["---"]
    for k, v in non_empty:
        lines.append(f"{k}: {v}")
    lines.append("---")
    # Preserve the convention: one blank line between frontmatter and body.
    if body and not body.startswith("\n"):
        return "\n".join(lines) + "\n\n" + body
    return "\n".join(lines) + "\n" + body


def _inject_default_frontmatter(
    body_text: str,
    kind: Optional[str] = None,
    created: Optional[str] = None,
    plankeeper_ticket: Optional[str] = None,
) -> str:
    """Ensure body_text starts with frontmatter containing Status and Created
    (and Kind, when a kind is supplied).

    Three cases:
      1. body has no frontmatter → prepend a fresh '---\\nStatus: backlog\\nCreated: <iso>\\n---\\n\\n' block.
      2. body has frontmatter with the fields already set → return unchanged
         (user-supplied values win over defaults).
      3. body has frontmatter missing some → fill in the missing fields,
         re-serialize, return.

    Note: save does NOT inject an `Agent` field. The `Agent: <name>` tag is the
    groundcrew dispatch signal, and plan-crew (`queue set --default-agent`) is
    the sole writer of it — a plan is born with no Agent and only acquires one
    when promoted to the groundcrew queue. A body that hand-declares `Agent` is
    still preserved verbatim (parse_frontmatter round-trips foreign/managed
    fields); save just never adds one on its own.

    Why status/created/kind are 'fill if absent' rather than 'overwrite':
    a user who hand-wrote `Status: todo` (or `Kind: prd`) in the body shouldn't
    have it stomped by the save invocation. The CLI default is a floor, not an
    override. `kind` is only written when the caller passed one — there is no
    default Kind, because an absent Kind is a valid state (plan-do then infers
    it from the content instead).

    `created` overrides the `Created` source: `None` (the heredoc path) stamps
    `_iso_utc_now()` because the plan is being authored now; a caller that passes
    a value (the `--from-path` move path) supplies the source file's birthtime,
    since a relocated plan pre-existed the move. Either way `Created` is
    fill-if-absent — a body that already carries a valid `Created` keeps it.
    """
    meta, body = parse_frontmatter(body_text)
    # Mint-once: a plan is born with a Plan-keeper Ticket and never has it
    # overwritten. Fill-if-absent (a body already carrying one keeps it), like
    # Status/Kind/Created below.
    if plankeeper_ticket and not meta.get("Plan-keeper Ticket"):
        meta["Plan-keeper Ticket"] = plankeeper_ticket
    if not meta.get("Status"):
        meta["Status"] = "backlog"
    if kind and not meta.get("Kind"):
        meta["Kind"] = kind
    # Save-time stamp that powers list's newest-first sort with intra-day
    # precision. Fill-if-absent (a hand-written Created in the body wins),
    # matching Status/Kind. See _plan_sort_key for why it lives in
    # frontmatter rather than relying on file timestamps.
    if not meta.get("Created"):
        meta["Created"] = created if created is not None else _iso_utc_now()
    out = serialize_frontmatter(meta, body)
    if not out.endswith("\n"):
        out += "\n"
    return out
