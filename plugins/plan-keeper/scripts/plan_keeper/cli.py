"""Top of the CLI tree: every ``cmd_*`` subcommand handler, the argparse
wiring, and ``main()``.

This is the only module that imports from every domain module; the domain
modules import only from leaf modules, never from ``cli``, so there is no
import cycle. Direct ``PLAN_ROOT`` reads go through the ``storage`` module
object (``storage.PLAN_ROOT``) so the constant has a single source of truth
and a single patch point.
"""
import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Mapping, Optional, cast

from plan_keeper import __version__, storage
from plan_keeper.config import (
    load_config,
    save_config,
    _redact_section,
)
from plan_keeper.global_config import load_global_config, save_global_config
from plan_keeper.dates import _iso_from_stat, parse_date_arg
from plan_keeper.errors import HelpfulArgumentParser, PlanKeeperCliError
from plan_keeper.frontmatter import (
    VALID_KINDS,
    _inject_default_frontmatter,
    parse_frontmatter,
    serialize_frontmatter,
    validate_kind,
)
from plan_keeper.crew_install import (
    default_run_doctor,
    resolve_config_path,
    run_crew_install,
)
from plan_keeper.groundcrew import (
    _assert_no_plankeeper_id_collisions,
    _blockers_for_plan,
    _build_repo_index,
    _collect_and_mint_crew_issues,
    _resolve_crew_id,
)
from plan_keeper.ids import (
    ensure_id,
    id_for_path,
)
from plan_keeper.types import Kind, QueueRow, RepoAlias

# Re-export so existing tests can read `cli.plankeeper_id`. The `as plankeeper_id`
# form marks this an intentional re-export; the single definition lives in `ids`.
from plan_keeper.ids import plankeeper_id as plankeeper_id
from plan_keeper.jira import (
    _resolve_jira_project_id,
    _validate_jira_site,
    jira_components,
    jira_issuetypes,
    jira_projects,
    jira_users,
    jira_viewer,
    refresh_jira_cache,
)
from plan_keeper.linear import (
    linear_labels,
    linear_projects,
    linear_teams,
    linear_users,
    linear_viewer,
    refresh_linear_cache,
)
from plan_keeper.naming import (
    _repo_from_git,
    derive_repo,
    derive_repo_full,
    normalize_override,
    plan_filename,
    plan_group_key,
    rename_for_kind,
    slugify_topic,
    validate_extension,
    validate_repo_name,
)
from plan_keeper.push import push_subcommand
from plan_keeper.upgrade import default_capture, default_stream, run_upgrade
from plan_keeper.storage import (
    LIFECYCLE_STATES,
    Status,
    TERMINAL_DIRS,
    emit_collision,
    find_unused_suffix,
    list_plans,
    plan_recency_key,
    plan_status,
    repo_dir,
    resolve_ticket_to_path,
    state_subdir,
    write_atomic,
)

# Program name shown in --help/usage and error prefixes. Derived from the
# invoked binary so the tool brands correctly in both of its homes: it reads
# `plan_keeper_cli` when run in-plugin as `python3 .../plan_keeper_cli.py`, and
# `plan-keeper` when installed as the standalone Homebrew console script.
PROG = Path(sys.argv[0]).stem or "plan_keeper_cli"


# --- Typed arg boundaries ---------------------------------------------------
# Each ``cmd_*`` handler builds the matching dataclass from the argparse
# Namespace at its top, then reads typed fields instead of implicit-Any
# ``args.X`` attributes. Fields mirror exactly the attributes that subcommand's
# parser configures (dest names, store_true defaults, set_defaults values).
# Provider-shared handlers (linear/jira) carry jira-only fields that the linear
# subtree never sets — those are read with ``getattr(..., None)`` so the same
# dataclass serves both subtrees.


@dataclass
class RepoNameArgs:
    override: Optional[str]
    cwd: Optional[str]
    full: bool

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "RepoNameArgs":
        return cls(override=args.override, cwd=args.cwd, full=args.full)


@dataclass
class ListArgs:
    override: Optional[str]
    all_repos: bool
    state: str
    status: Optional[str]
    group: bool

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ListArgs":
        return cls(
            override=args.override,
            all_repos=args.all_repos,
            state=args.state,
            status=getattr(args, "status", None),
            group=getattr(args, "group", False),
        )


@dataclass
class SaveArgs:
    override: Optional[str]
    topic: Optional[str]
    date: Optional[str]
    extension: Optional[str]
    from_path: Optional[str]
    kind: Optional[str]
    on_collision: str

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "SaveArgs":
        return cls(
            override=args.override,
            topic=args.topic,
            date=args.date,
            extension=args.extension,
            from_path=args.from_path,
            kind=args.kind,
            on_collision=args.on_collision,
        )


@dataclass
class ProviderConfigGetArgs:
    name: str
    show_secrets: bool

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ProviderConfigGetArgs":
        return cls(name=args.name, show_secrets=args.show_secrets)


@dataclass
class ProviderConfigSaveArgs:
    name: str

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ProviderConfigSaveArgs":
        return cls(name=args.name)


@dataclass
class ProviderConfigRefreshArgs:
    name: str
    api_key: Optional[str]
    email: Optional[str]
    site: Optional[str]

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ProviderConfigRefreshArgs":
        return cls(
            name=args.name,
            api_key=args.api_key,
            email=getattr(args, "email", None),
            site=getattr(args, "site", None),
        )


@dataclass
class FileMetaLocatorArgs:
    file: Optional[str]
    ticket: Optional[str]

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "FileMetaLocatorArgs":
        return cls(file=args.file, ticket=args.ticket)


@dataclass
class FileMetaSetArgs:
    file: Optional[str]
    ticket: Optional[str]
    agent: Optional[str]
    status: Optional[str]
    on_collision: str
    kind: Optional[str]
    completed_on: Optional[str]
    plankeeper_ticket: Optional[str]
    linear_ticket: Optional[str]
    jira_ticket: Optional[str]
    blocked_by: Optional[str]

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "FileMetaSetArgs":
        return cls(
            file=args.file,
            ticket=args.ticket,
            agent=args.agent,
            status=args.status,
            on_collision=args.on_collision,
            kind=args.kind,
            completed_on=args.completed_on,
            plankeeper_ticket=args.plankeeper_ticket,
            linear_ticket=args.linear_ticket,
            jira_ticket=args.jira_ticket,
            blocked_by=args.blocked_by,
        )


@dataclass
class PushArgs:
    name: str
    file: Optional[str]
    ticket: Optional[str]
    force_new: bool

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "PushArgs":
        return cls(
            name=args.name,
            file=args.file,
            ticket=args.ticket,
            force_new=args.force_new,
        )


@dataclass
class TicketApiArgs:
    name: str
    api_kind: str
    api_key: Optional[str]
    email: Optional[str]
    site: Optional[str]
    project_key: Optional[str]

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "TicketApiArgs":
        return cls(
            name=args.name,
            api_kind=args.api_kind,
            api_key=args.api_key,
            email=getattr(args, "email", None),
            site=getattr(args, "site", None),
            project_key=getattr(args, "project_key", None),
        )


@dataclass
class CrewIdArgs:
    id: str

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "CrewIdArgs":
        return cls(id=args.id)


@dataclass
class CrewInstallArgs:
    config: Optional[str]
    dry_run: bool

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "CrewInstallArgs":
        return cls(config=args.config, dry_run=args.dry_run)


@dataclass
class QueueAddArgs:
    files: list[str]
    repo: Optional[str]
    agent: str

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "QueueAddArgs":
        return cls(files=args.files, repo=args.repo, agent=args.agent)


@dataclass
class QueueDropArgs:
    files: list[str]
    repo: Optional[str]

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "QueueDropArgs":
        return cls(files=args.files, repo=args.repo)


@dataclass
class QueueListArgs:
    all: bool
    repo: Optional[str]

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "QueueListArgs":
        return cls(all=args.all, repo=args.repo)


@dataclass
class RepoAliasAddArgs:
    target: str
    name: str

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "RepoAliasAddArgs":
        return cls(target=args.target, name=args.name)


@dataclass
class RepoAliasRemoveArgs:
    name: str

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "RepoAliasRemoveArgs":
        return cls(name=args.name)


# --- Subcommands ------------------------------------------------------------


def cmd_repo_name(args) -> int:
    a = RepoNameArgs.from_args(args)
    if a.full:
        print(derive_repo_full(a.cwd))
    else:
        print(derive_repo(a.override, a.cwd))
    return 0


def _kind_of(path: Path) -> str:
    """Return a plan's lowercased `Kind` frontmatter, or '' if absent/unreadable.

    The filename's `--<kind>` segment is a display/sort convenience; frontmatter
    is the source of truth for the label, so a hand-renamed file still labels
    correctly. A parse/read failure degrades to '' (unclassified) rather than
    breaking the whole listing — same resilience contract as plan_status.
    """
    try:
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (PlanKeeperCliError, OSError, UnicodeDecodeError):
        return ""
    return (meta.get("Kind") or "").strip().lower()


def _pipeline_index(kind: str) -> int:
    """Sort rank for within-group ordering: idea->exec-plan, unclassified last."""
    try:
        return VALID_KINDS.index(kind)
    except ValueError:
        return len(VALID_KINDS)


def _render_grouped(items: list[tuple[str, Path]]) -> int:
    """Render (display_name, path) items clustered by project slug.

    Group order follows `items`' incoming order via first-encounter: a group is
    placed where its first (newest) member appears. So single-repo input
    (list_plans, newest-first) yields newest-project-first; cross-repo input
    (_all_repos_items, alphabetical-by-repo then newest-within) yields
    repo-alphabetical then newest-within — matching the flat `--all-repos`
    listing's own ordering, not a global recency sort. Within a group, members
    sort along the Kind pipeline, then by filename. The stage column is the
    frontmatter Kind ('-' when unclassified); Kind is read once per member up
    front (a stable snapshot — no re-read mid-sort or at print time). Groups
    print separated by a blank line.

    Grouping key is repo-aware: in cross-repo mode the display name is
    'repo/filename', so the heading is 'repo/slug' and two repos that happen to
    share a slug stay distinct groups rather than merging. In single-repo mode
    the display name is the bare filename, so the heading is the bare slug.
    """
    # (display_name, path, kind) — Kind snapshotted once here so the sort key and
    # the print loop never re-read the file (and can't see a mid-list mutation).
    groups: dict[str, list[tuple[str, Path, str]]] = {}
    order: list[str] = []
    for name, path in items:
        # display name is 'repo/filename' (cross-repo) or 'filename' (single);
        # the leading 'repo/' prefix, if any, namespaces the slug.
        prefix = name[: len(name) - len(path.name)]
        key = f"{prefix}{plan_group_key(path.name)}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((name, path, _kind_of(path)))

    blocks: list[str] = []
    for key in order:
        members = sorted(groups[key], key=lambda npk: (_pipeline_index(npk[2]), npk[1].name))
        lines = [key]
        for name, _path, kind in members:
            lines.append(f"  {(kind or '-'):<10} {name}")
        blocks.append("\n".join(lines))
    if blocks:
        print("\n\n".join(blocks))
    return 0


def _render_listing(items: list[tuple[str, Path]], raw_filter: Optional[str]) -> int:
    """Render a listing of (display_name, path) pairs already in display order.

    `display_name` is what prints in the filename column — a bare filename in
    single-repo mode, `repo/filename` in cross-repo mode. The scope resolution
    (which repos) lives in cmd_list; this only formats, so both modes share one
    code path for the --status tiering and the hidden-plans stderr note.

    Without a filter, prints one display_name per line. With a filter, groups by
    the requested Status values in the order given (the filter doubles as tier
    order), preserving the incoming order within each tier (stable sort), emits
    `status<TAB>display_name`, and summarizes excluded active plans on stderr.
    """
    if not raw_filter:
        for name, _ in items:
            print(name)
        return 0

    tiers = [s.strip().lower() for s in raw_filter.split(",") if s.strip()]
    tier_rank = {s: i for i, s in enumerate(tiers)}
    annotated = [(name, plan_status(p)) for name, p in items]

    shown = [(name, s) for (name, s) in annotated if s in tier_rank]
    # `items` is already in display order; a stable sort by tier preserves that
    # within each group.
    shown.sort(key=lambda ns: tier_rank[ns[1]])
    for name, s in shown:
        print(f"{s}\t{name}")

    # Transparency: never silently drop active plans the filter excluded.
    hidden = [s for (_, s) in annotated if s not in tier_rank]
    if hidden:
        counts: dict[str, int] = {}
        for s in hidden:
            counts[s] = counts.get(s, 0) + 1
        summary = ", ".join(f"{st}×{n}" for st, n in sorted(counts.items()))
        print(
            f"note: {len(hidden)} other active plan(s) hidden ({summary})",
            file=sys.stderr,
        )
    return 0


def _all_repos_items(state: str) -> list[tuple[str, Path]]:
    """Build the (display_name, path) list across every repo under ~/plans/.

    Repos are iterated alphabetically (same enumeration as cmd_repo_list —
    sorted, dotfiles/non-dirs skipped); plans within a repo stay newest-first
    (list_plans order). Display name is `repo/filename` so every line is
    self-labeling. Repos with no plans in `state` contribute nothing.
    """
    items: list[tuple[str, Path]] = []
    if not storage.PLAN_ROOT.exists():
        return items
    for entry in sorted(storage.PLAN_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        for p in list_plans(entry.name, state):
            items.append((f"{entry.name}/{p.name}", p))
    return items


def cmd_list(args) -> int:
    # Scope resolution: --all-repos forces cross-repo; --override and a git
    # origin each pin a single repo; with neither (no repo context) we fall back
    # to cross-repo rather than the dir-name guess that used to point at a
    # nonexistent ~/plans/<cwd-basename>/ and print nothing. --all-repos and
    # --override are mutually exclusive — argparse rejects the combination
    # (exit 2) before we get here.
    a = ListArgs.from_args(args)
    raw_filter = a.status

    if a.override:
        explicit: Optional[str] = validate_repo_name(normalize_override(a.override))
    else:
        explicit = _repo_from_git()

    if a.all_repos or explicit is None:
        items = _all_repos_items(a.state)
    else:
        items = [(p.name, p) for p in list_plans(explicit, a.state)]
    if a.group:
        return _render_grouped(items)
    return _render_listing(items, raw_filter)


def cmd_repo_list(args) -> int:
    del args
    if not storage.PLAN_ROOT.exists():
        return 0
    def _count(d: Path) -> int:
        if not d.exists():
            return 0
        return sum(
            1 for p in d.iterdir() if p.is_file() and not p.name.startswith(".")
        )

    for entry in sorted(storage.PLAN_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        active = _count(entry)
        done = _count(entry / "done")
        deferred = _count(entry / "deferred")
        if active == 0 and done == 0 and deferred == 0:
            continue
        parts = []
        if active:
            parts.append(f"active={active}")
        if done:
            parts.append(f"done={done}")
        if deferred:
            parts.append(f"deferred={deferred}")
        print(f"{entry.name}: {' '.join(parts)}")
    return 0


def _split_alias_target(target: str) -> tuple[str, str]:
    """Split `<remote>[/<subpath>]` into (remote, subpath).

    First segment is the remote; everything after the first slash is the
    subpath (kept as one slash-joined string for direct comparison with
    `_subpath_from_toplevel`'s output). A bare `<remote>` registers a
    repo-root alias (subpath="").
    """
    if "/" in target:
        head, _, rest = target.partition("/")
        return head, rest
    return target, ""


def _validate_alias_subpath(subpath: str) -> str:
    """Reject subpaths that can never match `_subpath_from_toplevel`'s output.

    Valid: "" (repo-root alias) or POSIX-style `a/b/c` with no empty / `.` /
    `..` segments and no backslashes. The fence catches typos at write time
    (`carrot//catalog`, `carrot/catalog/`, `carrot/../etc`) which would
    otherwise sit dead in the config forever — `_subpath_from_toplevel` never
    produces a trailing slash or an empty segment, so a dead entry has no
    way to fix itself on the resolve side.
    """
    if subpath == "":
        return subpath
    if "\\" in subpath:
        raise PlanKeeperCliError(
            f"invalid subpath {subpath!r}: backslashes are not allowed",
            code=2,
        )
    for segment in subpath.split("/"):
        if segment == "":
            raise PlanKeeperCliError(
                f"invalid subpath {subpath!r}: empty path segment "
                "(leading/trailing/double slash)",
                code=2,
            )
        if segment in {".", ".."}:
            raise PlanKeeperCliError(
                f"invalid subpath {subpath!r}: '.' / '..' segments are not allowed",
                code=2,
            )
    return subpath


def cmd_repo_alias_add(args) -> int:
    a = RepoAliasAddArgs.from_args(args)
    remote, subpath = _split_alias_target(a.target)
    if not remote:
        raise PlanKeeperCliError(
            f"empty remote in {a.target!r} (expected <remote>[/<subpath>])",
            code=2,
        )
    # Reject `"<remote>/"` (target with a trailing slash but no subpath):
    # the trailing slash signals a typo, not an explicit repo-root request.
    # A user who wants a repo-root alias types just `"<remote>"`.
    if a.target.endswith("/") and subpath == "":
        raise PlanKeeperCliError(
            f"invalid subpath in {a.target!r}: trailing slash with no subpath "
            "(use bare `<remote>` for a repo-root alias)",
            code=2,
        )
    # `name` is the user-supplied alias that will become a `~/plans/<name>/`
    # bucket — apply the same fence the rest of the CLI uses on every other
    # path that lands a repo name. Catches "", ".", "..", "foo/bar", and any
    # backslash form before they get baked into the config.
    validate_repo_name(a.name)
    _validate_alias_subpath(subpath)
    config = load_global_config()
    aliases: list[RepoAlias] = list(config.get("aliases") or [])
    # Warn (but allow) when another (remote, subpath) is already mapped to
    # this name — two subpaths routed to the same bucket is a legitimate
    # configuration, just usually a typo.
    for existing in aliases:
        if (existing.get("name") == a.name
                and (existing.get("remote"), existing.get("subpath"))
                != (remote, subpath)):
            existing_id = _format_alias_target(
                existing.get("remote", ""), existing.get("subpath", ""),
            )
            print(
                f"warning: alias name {a.name!r} is already used by "
                f"{existing_id}",
                file=sys.stderr,
            )
            break
    # Replace in place on (remote, subpath) match so a re-run with the same
    # target is idempotent rather than appending a duplicate entry.
    new_entry: RepoAlias = {"remote": remote, "subpath": subpath, "name": a.name}
    for idx, existing in enumerate(aliases):
        if (existing.get("remote"), existing.get("subpath")) == (remote, subpath):
            aliases[idx] = new_entry
            break
    else:
        aliases.append(new_entry)
    config["aliases"] = aliases
    save_global_config(config)
    return 0


def _format_alias_target(remote: str, subpath: str) -> str:
    """Render `(remote, subpath)` in the same `<remote>[/<subpath>]` form the
    user typed at `alias add` time. A repo-root alias (subpath=="") prints as
    the bare remote — NOT `<remote>/` with a trailing slash, which would
    confuse the user looking at the warning back at their original input.
    """
    return f"{remote}/{subpath}" if subpath else remote


def cmd_repo_alias_list(args) -> int:
    del args
    config = load_global_config()
    aliases = config.get("aliases") or []
    rows = sorted(
        aliases,
        key=lambda e: (e.get("remote") or "", e.get("subpath") or ""),
    )
    for entry in rows:
        print(
            f"{entry.get('remote', '')}\t"
            f"{entry.get('subpath', '')}\t"
            f"{entry.get('name', '')}"
        )
    return 0


def cmd_repo_alias_remove(args) -> int:
    a = RepoAliasRemoveArgs.from_args(args)
    config = load_global_config()
    aliases = list(config.get("aliases") or [])
    kept = [e for e in aliases if e.get("name") != a.name]
    if len(kept) == len(aliases):
        raise PlanKeeperCliError(f"no alias named {a.name!r}", code=3)
    config["aliases"] = kept
    save_global_config(config)
    return 0


def cmd_save(args) -> int:
    a = SaveArgs.from_args(args)
    repo = derive_repo(a.override)

    # Two distinct shapes, picked by whether --from-path is given:
    #
    #   1. Heredoc shape (no --from-path): body comes from stdin, name is
    #      constructed as <date>-<slug>.<ext> from --topic + --extension (+ --date).
    #      --topic is required; --extension defaults to 'md'.
    #
    #   2. Disk shape (--from-path is given): file is moved byte-for-byte and
    #      keeps its source basename verbatim. --topic / --extension / --date
    #      have no meaning here and are rejected — the source filename already
    #      encodes everything the target needs (e.g. task-list-builder's
    #      <date>-<runid>-<short>.<slug>.{json,md}). Always a move (not a copy):
    #      the realistic workflow is relocating an already-on-disk artifact,
    #      and leaving stale duplicates behind is just confusion.
    kind = validate_kind(a.kind) if a.kind is not None else None

    if a.from_path:
        for flag, value in (
            ("--topic", a.topic),
            ("--extension", a.extension),
            ("--date", a.date),
            ("--kind", a.kind),
        ):
            if value is not None:
                raise PlanKeeperCliError(
                    f"{flag} is incompatible with --from-path "
                    "(--from-path preserves the source bytes verbatim); "
                    f"drop {flag} or drop --from-path "
                    "(set Kind afterward via `file-meta set --kind ...`)",
                    code=2,
                )
        source = Path(a.from_path)
        if not source.exists():
            raise PlanKeeperCliError(f"source not found: {source}", code=3)
        if not source.is_file():
            raise PlanKeeperCliError(f"source is not a file: {source}", code=3)
        target = repo_dir(repo) / source.name
        ext = None  # --from-path never reaches the frontmatter-injection branch
    else:
        source = None
        if a.topic is None:
            raise PlanKeeperCliError(
                "--topic is required (unless --from-path is given, in which "
                "case the source basename is used and --topic is rejected)",
                code=2,
            )
        slug = slugify_topic(a.topic)
        if not slug:
            raise PlanKeeperCliError(
                f"topic {a.topic!r} slugified to empty string", code=2
            )
        ext = validate_extension(a.extension) if a.extension is not None else "md"
        if kind and ext != "md":
            raise PlanKeeperCliError(
                f"--kind only applies to .md saves (frontmatter lives in markdown); "
                f"got --extension {ext}. Drop --kind, or save the paired .md with it.",
                code=2,
            )
        date_str = (
            parse_date_arg(a.date) if a.date else date.today().isoformat()
        )
        target = repo_dir(repo) / plan_filename(date_str, slug, ext, kind)

    if target.exists():
        if a.on_collision == "fail":
            # Critical: emit BEFORE any source mutation, so a `--move-source`
            # caller can safely retry without losing the source file.
            emit_collision(target)
            return 2
        if a.on_collision == "suffix":
            target = find_unused_suffix(target)
        # "overwrite" → fall through

    if source is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() == ".md":
            # A moved-in .md becomes a fully managed plan: fill the same
            # Status/Created block a heredoc .md save gets (fill-if-absent —
            # a .md already carrying them keeps its own values), so
            # plan-do/plan-done see it by Status and list orders it with intra-day
            # precision. Created comes from the source file's birthtime (via
            # _iso_from_stat), not _iso_utc_now() — the plan pre-existed the move.
            # No Kind is injected (--from-path rejects --kind; set it later via
            # file-meta). This rewrites bytes, so .md moves are NOT byte-verbatim.
            #
            # Order matters: compute the injected content from the SOURCE and
            # write the target, THEN unlink the source. A malformed-frontmatter
            # .md raises here while the source still exists (retry-safe, no
            # stranded half-move), and the source is removed only once the target
            # write succeeded — the same "delete only on success" contract the
            # byte-verbatim path gets from shutil.move.
            injected = _inject_default_frontmatter(
                source.read_text(encoding="utf-8"),
                created=_iso_from_stat(source.stat()),
                plankeeper_ticket=id_for_path(target),
            )
            write_atomic(target, injected)
            # Skip the unlink when --from-path already points AT the target
            # (e.g. `--on-collision overwrite` on a file already in the plans
            # dir): write_atomic has just replaced target in place, so unlinking
            # the (same) path would delete the freshly stamped plan. Different
            # paths → this is a real move, so remove the source.
            if source.resolve() != target.resolve():
                source.unlink()
        else:
            # Non-.md: relocate byte-for-byte — no stat, no rewrite, no
            # trailing-newline normalization. shutil.move uses os.rename on the
            # same filesystem (one atomic syscall) and falls back to copy2 +
            # unlink across filesystems. This is the verbatim guarantee the
            # paired .json sibling relies on, and it preserves the source mtime.
            shutil.move(str(source), str(target))
    else:
        # Heredoc / piped input — write_atomic normalizes a missing trailing
        # newline because shell heredocs and pasted text can end mid-line.
        content = sys.stdin.read()
        if not content.endswith("\n"):
            content += "\n"
        # Frontmatter injection: only for .md saves (the spec gates on this
        # so JSON/YAML siblings of paired saves remain byte-exact). Merges
        # into user-supplied frontmatter rather than duplicating.
        if ext == "md":
            content = _inject_default_frontmatter(
                content, kind,
                plankeeper_ticket=id_for_path(target),
            )
        write_atomic(target, content)
    print(target)
    return 0


def cmd_ticket_system_config_get(args) -> int:
    a = ProviderConfigGetArgs.from_args(args)
    repo = derive_repo(None)
    config = load_config(repo)
    section = config.get(a.name)
    if section is None:
        raise PlanKeeperCliError(
            f"no config for ticket system {a.name!r} in repo {repo!r}",
            code=3,
        )
    output = section if a.show_secrets else _redact_section(section)
    print(json.dumps(output))
    return 0


def cmd_ticket_system_config_save(args) -> int:
    a = ProviderConfigSaveArgs.from_args(args)
    raw = sys.stdin.read()
    try:
        new_section = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PlanKeeperCliError(f"stdin is not valid JSON: {e}", code=2)
    if not isinstance(new_section, dict):
        raise PlanKeeperCliError("stdin must be a JSON object", code=2)
    repo = derive_repo(None)
    config = load_config(repo)
    config[a.name] = new_section
    path = save_config(repo, config)
    print(path)
    return 0


def cmd_ticket_system_config_refresh(args) -> int:
    a = ProviderConfigRefreshArgs.from_args(args)
    if a.name == "linear":
        if not a.api_key:
            raise PlanKeeperCliError(
                "linear refresh requires --api-key", code=2,
            )
        refresh_linear_cache(a.api_key)
    elif a.name == "jira":
        if not a.site or not a.email or not a.api_key:
            raise PlanKeeperCliError(
                "jira refresh requires --site, --email, --api-key", code=2,
            )
        _validate_jira_site(a.site)
        refresh_jira_cache(a.site, a.email, a.api_key)
    return 0


def _resolve_file_meta_path(a: "FileMetaLocatorArgs | FileMetaSetArgs") -> Path:
    """Resolve and validate the target for a file-meta subcommand.

    Locate by --ticket (cross-repo) or --file, then assert it's a regular
    file *before* any read — a directory passes exists() but would crash
    read_text() with IsADirectoryError. The "not a file" contract (exit 3)
    keeps every path-taking command failing the same clean way.
    """
    path = resolve_ticket_to_path(a.ticket) if a.ticket else Path(a.file or "")
    if not path.exists():
        raise PlanKeeperCliError(f"plan file not found: {path}", code=3)
    if not path.is_file():
        raise PlanKeeperCliError(f"not a file: {path}", code=3)
    return path


def cmd_file_meta_get(args) -> int:
    a = FileMetaLocatorArgs.from_args(args)
    path = _resolve_file_meta_path(a)
    text = path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(text)
    print(json.dumps(meta))
    return 0


def cmd_file_meta_strip(args) -> int:
    a = FileMetaLocatorArgs.from_args(args)
    path = _resolve_file_meta_path(a)
    text = path.read_text(encoding="utf-8")
    _, body = parse_frontmatter(text)
    sys.stdout.write(body)
    return 0


def cmd_file_meta_set(args) -> int:
    """Edit plan frontmatter via one self-documenting flag per field.

    The plan is located by `--file` or `--ticket` (the cross-repo locator,
    consistent with push). `--ticket` is *never* a value — an id is written into
    its own field with `--plankeeper-ticket` / `--linear-ticket` /
    `--jira-ticket`, so the word "ticket" means the same thing (locate) on every
    subcommand.

    Inputs are validated (Kind enum, Completed-on date) BEFORE the file is
    located or read, so a typo fails with exit 2 and never touches the file.
    An unmanaged file is adopted rather than rejected: a bare file (no
    frontmatter) or a partial block is run through plan-save's
    _inject_default_frontmatter to stamp the missing Status/Created/Plan-keeper
    Ticket defaults before the requested mutation, so the plans tree can be
    normalized in place. Only genuinely malformed frontmatter (a ``---`` opener
    with no closing ``---``) is still rejected (exit 5).

    MUTATES DISK — RELOCATES AND STAMPS: a terminal ``--status`` (done /
    deferred) does not rewrite in place. It writes the plan to the matching
    terminal subdir (done/ or deferred/) and unlinks the source, so the plan's
    on-disk path moves. A ``done`` status additionally stamps ``Completed on``
    with today's date (unless the caller already supplied ``--completed-on``).
    Active statuses are a pure in-place rewrite.
    """
    a = FileMetaSetArgs.from_args(args)
    # Map each value flag to its frontmatter key, in canonical order. Building
    # this first means validate_kind/parse_date_arg run before any file I/O.
    updates: list[tuple[str, str]] = []
    if a.plankeeper_ticket is not None:
        updates.append(("Plan-keeper Ticket", a.plankeeper_ticket))
    if a.linear_ticket is not None:
        updates.append(("Linear Ticket", a.linear_ticket))
    if a.jira_ticket is not None:
        updates.append(("Jira Ticket", a.jira_ticket))
    if a.completed_on is not None:
        updates.append(("Completed on", parse_date_arg(a.completed_on)))
    if a.agent is not None:
        updates.append(("Agent", a.agent))
    if a.status is not None:
        updates.append(("Status", a.status))
    normalized_kind: Optional[str] = None
    if a.kind is not None:
        normalized_kind = validate_kind(a.kind)
        updates.append(("Kind", normalized_kind))
    if a.blocked_by is not None:
        updates.append(("Blocked-by", a.blocked_by))
    if not updates:
        raise PlanKeeperCliError(
            "file-meta set requires at least one of --plankeeper-ticket, "
            "--linear-ticket, --jira-ticket, --completed-on, --agent, --status, "
            "--kind, --blocked-by",
            code=2,
        )
    path = _resolve_file_meta_path(a)
    original = path.read_text(encoding="utf-8")
    # Parse first: this is where genuinely malformed frontmatter (a `---` opener
    # with no closing `---`) raises PlanKeeperCliError(5), before any write —
    # silently rewriting corrupt YAML is the failure mode the atomic-write
    # discipline exists to prevent, so adoption never touches such a file.
    meta, body = parse_frontmatter(original)  # may raise PlanKeeperCliError(5)
    # Adopt unmanaged files instead of rejecting them: the plans tree is
    # plan-keeper's domain, so a file dropped in by hand (or by a tool that
    # bypassed plan-save) is normalized on first mutation. _inject_default_-
    # frontmatter fills exactly Status/Created/Plan-keeper Ticket when absent
    # (fill-if-absent — existing values always win), so "any of those three is
    # missing" is precisely "this file is unmanaged". Gate on that, not on text
    # inequality: a managed file in non-canonical field order would re-serialize
    # to different bytes while backfilling nothing, which must NOT be reported as
    # an adoption. Created is sourced from the file's birthtime, not now() — like
    # plan-save's --from-path move, the plan pre-existed this mutation. Agent is
    # never injected (it stays the groundcrew dispatch signal).
    if not (meta["Status"] and meta["Created"] and meta["Plan-keeper Ticket"]):
        adopted = _inject_default_frontmatter(
            original,
            created=_iso_from_stat(path.stat()),
            plankeeper_ticket=id_for_path(path),
        )
        meta, body = parse_frontmatter(adopted)
        print(
            f"adopted unmanaged plan {path.name}: stamped plan-keeper defaults",
            file=sys.stderr,
        )
    for key, value in updates:
        meta[key] = value

    # The target path can move on two independent axes, which compose:
    #   1. Directory: a terminal status (done/deferred) relocates the plan into
    #      the matching subdir so Status and directory never disagree; active
    #      statuses and pure-metadata edits stay in the current dir.
    #   2. Basename: a Kind change re-stamps the filename's `--<kind>` segment
    #      (.md only) so the name tracks the new `Kind:` frontmatter. Frontmatter
    #      stays the source of truth; this just keeps the display/sort segment
    #      honest.
    # `done` also stamps Completed on (today, unless the caller supplied one).
    if a.status in TERMINAL_DIRS:
        if a.status == "done" and a.completed_on is None:
            meta["Completed on"] = date.today().isoformat()
        target_dir = _terminal_target(path, cast(Status, a.status)).parent
    elif a.status is not None and path.parent.name in TERMINAL_DIRS:
        # Reactivating a terminal plan (moving it back to the active root) is
        # out of scope. Refuse loudly rather than rewrite in place and leave an
        # active-status plan parked in done/ or deferred/, where active list,
        # push --ticket, and ticket resolution would never find it.
        raise PlanKeeperCliError(
            f"cannot set active status {a.status!r} on a plan in "
            f"{path.parent.name}/ — reactivating a {path.parent.name} plan is "
            f"not supported; move the file back to the active dir first",
            code=2,
        )
    else:
        target_dir = path.parent

    target_name = path.name
    if normalized_kind is not None and path.suffix.lower() == ".md":
        target_name = rename_for_kind(path.name, cast(Kind, normalized_kind))

    target = target_dir / target_name
    if target != path and target.exists():
        if a.on_collision == "fail":
            emit_collision(target)
            return 2
        if a.on_collision == "suffix":
            target = find_unused_suffix(target)
        # "overwrite" → write_atomic replaces it below

    new_text = serialize_frontmatter(meta, body)
    if not new_text.endswith("\n"):
        new_text += "\n"
    write_atomic(target, new_text)
    if target.resolve() != path.resolve():
        path.unlink()  # relocation: drop the source only after the dest write
    print(target)
    return 0


def _terminal_target(source: Path, status: Status) -> Path:
    """Destination path for relocating `source` to a terminal `status`.

    The repo root is `source`'s parent, unless `source` already sits in a
    terminal subdir (a caller can pass an explicit `done/x.md` path) — then
    strip that component so done→deferred relocates to a sibling, never a
    nested `done/done/`.
    """
    repo_root = source.parent
    if repo_root.name in TERMINAL_DIRS:
        repo_root = repo_root.parent
    return state_subdir(repo_root, status) / source.name


def cmd_push(args) -> int:
    a = PushArgs.from_args(args)
    path = resolve_ticket_to_path(a.ticket) if a.ticket else Path(a.file or "")
    result = push_subcommand(a.name, str(path), force_new=a.force_new)
    print(json.dumps(result))
    return 0


_PROVIDER_API_KINDS = {
    "linear": ["viewer", "teams", "projects", "labels", "users"],
    "jira": ["viewer", "projects", "components", "issuetypes", "users"],
}
_JIRA_KINDS_NEED_PROJECT_KEY = {"components", "users", "issuetypes"}


def _validate_ticket_api_args(a: "TicketApiArgs") -> None:
    """Verify required flags are present for the requested (name, kind).

    Per-provider argparse `choices` already guarantee the kind is valid for
    this provider, so this only checks the credential/flag preconditions each
    kind needs before a network call. Up-front validation gives the user a
    clear CLI message instead of a downstream `jira_viewer(None, None, None)`
    network error.
    """
    kind = a.api_kind
    if a.name == "linear":
        if not a.api_key:
            raise PlanKeeperCliError(
                f"linear api {kind} requires --api-key", code=2,
            )
    else:  # jira
        for flag, value in (
            ("--site", a.site),
            ("--email", a.email),
            ("--api-key", a.api_key),
        ):
            if not value:
                raise PlanKeeperCliError(
                    f"jira api {kind} requires {flag}", code=2,
                )
        # The loop above raised if --site was missing, so it is non-None here.
        _validate_jira_site(cast(str, a.site))
        if kind in _JIRA_KINDS_NEED_PROJECT_KEY and not a.project_key:
            raise PlanKeeperCliError(
                f"jira api {kind} requires --project-key", code=2,
            )


def cmd_ticket_api(args) -> int:
    """Dispatch `<provider> api <kind>`.

    Each kind is implemented by a per-system function. Output is always JSON
    to stdout. `name` arrives from the provider subparser's set_defaults,
    and the kind is constrained to this provider's valid set by argparse
    `choices`, so the `impl` lookup below always hits.
    """
    a = TicketApiArgs.from_args(args)
    # Asserts the credential/flag preconditions each (provider, kind) needs;
    # raises on any missing one before we reach the impl table. The casts below
    # encode that post-validation invariant for the type checker — the required
    # credentials are non-None here by construction.
    _validate_ticket_api_args(a)
    if a.name == "linear":
        api_key = cast(str, a.api_key)
        impl = {
            "viewer": lambda: linear_viewer(api_key),
            "teams": lambda: linear_teams(api_key),
            "projects": lambda: linear_projects(api_key),
            "labels": lambda: linear_labels(api_key),
            "users": lambda: linear_users(api_key),
        }
    else:  # jira
        site = cast(str, a.site)
        email = cast(str, a.email)
        token = cast(str, a.api_key)
        pkey = cast(str, a.project_key)
        impl = {
            "viewer":     lambda: jira_viewer(site, email, token),
            "projects":   lambda: jira_projects(site, email, token),
            "components": lambda: jira_components(site, email, token, pkey),
            "users":      lambda: jira_users(site, email, token, pkey),
            "issuetypes": lambda: jira_issuetypes(
                site, email, token,
                _resolve_jira_project_id(site, email, token, pkey),
            ),
        }
    print(json.dumps(impl[a.api_kind]()))
    return 0


# --- groundcrew shell adapter ----------------------------------------------


def cmd_crew_fetch(args) -> int:
    """Emit a JSON array of issues for groundcrew's shell adapter to consume.

    Scans ~/plans/*/*.md (one level deep — skips done/ and deferred/). For each
    plan, ``_collect_and_mint_crew_issues`` mints a frozen ``Plan-keeper
    Ticket`` if one is absent (mint-once, never overwritten); this asserts no
    two plans carry the same id.

    MUTATES DISK: despite the read-flavoured name, fetch is not pure — for any
    scanned plan that lacks a ``Plan-keeper Ticket``, it mints one and persists
    it back into that plan file. A plain ``crew fetch`` can therefore rewrite
    plans on disk.
    """
    del args
    issues = _collect_and_mint_crew_issues()
    _assert_no_plankeeper_id_collisions(issues)
    print(json.dumps(issues))
    return 0


# Constrains a crew ${id} to the same charset minted ids use, rejecting
# malformed input early with a clear error. `_resolve_crew_id` matches a plan by
# parsed-id equality (`issue["id"] == plan_id`) and never joins the id into a
# filesystem path, so this is input hygiene, not a path-traversal fence.
_GROUNDCREW_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def cmd_crew_get(args) -> int:
    """Print one issue JSON for `${id}`, exit 3 if not found."""
    a = CrewIdArgs.from_args(args)
    if not _GROUNDCREW_ID_RE.match(a.id):
        raise PlanKeeperCliError(
            f"invalid id {a.id!r}: must match [A-Za-z0-9._-]+ (no path separators)",
            code=2,
        )
    issue = _resolve_crew_id(a.id)
    if issue is None:
        return 3
    print(json.dumps(issue))
    return 0


def _crew_set_status(plan_id: str, status: Status) -> int:
    """Flip the plan named by `${id}` to `Status: <status>`, exit 3 if no plan
    maps to that id. Shared body of `crew start` (in-progress) and `crew review`
    (in-review) — the two markIn* legs of the groundcrew TicketSource adapter.

    Resolution reuses `crew get`'s resolver (recompute each plan's synthesized
    id and match), so the two agree by construction. The id can only ever name
    a plan inside PLAN_ROOT — resolution globs only PLAN_ROOT — so the path
    guard the old stdin-`{path}` interface needed is gone by construction: an
    id has no way to express a path outside the plan tree.
    """
    if not _GROUNDCREW_ID_RE.match(plan_id):
        raise PlanKeeperCliError(
            f"invalid id {plan_id!r}: must match [A-Za-z0-9._-]+ (no path separators)",
            code=2,
        )
    issue = _resolve_crew_id(plan_id)
    if issue is None:
        raise PlanKeeperCliError(f"no plan maps to id {plan_id!r}", code=3)
    path = Path(issue["sourceRef"]["path"])
    # _resolve_crew_id only returns plans that parsed as frontmatter, so the
    # read+parse below is safe without re-checking for a frontmatter header.
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    meta["Status"] = status
    new_text = serialize_frontmatter(meta, body)
    if not new_text.endswith("\n"):
        new_text += "\n"
    write_atomic(path, new_text)
    print(path)
    return 0


def cmd_crew_start(args) -> int:
    """Flip the plan named by `${id}` to Status: in-progress (groundcrew's
    markInProgress leg). See `_crew_set_status` for the resolve-and-write body."""
    a = CrewIdArgs.from_args(args)
    return _crew_set_status(a.id, "in-progress")


def cmd_crew_review(args) -> int:
    """Flip the plan named by `${id}` to Status: in-review (groundcrew's
    markInReview leg — auto-advance an in-progress ticket once its PR opens).
    See `_crew_set_status` for the resolve-and-write body."""
    a = CrewIdArgs.from_args(args)
    return _crew_set_status(a.id, "in-review")


def cmd_crew_install(args) -> int:
    """Patch the groundcrew config so it dispatches plans from ~/plans/*.

    Composition root: resolves the config path and our own ``pk`` binary, then
    hands the real ``crew doctor`` runner to the orchestration in
    ``crew_install`` (kept separate so its patch logic is unit-testable).

    Prefers the ``pk`` binary over the ``plan-keeper`` alias so re-running
    ``crew install`` repoints existing wiring to the primary name; the
    ``plan-keeper`` fallback covers installs that predate the ``pk`` rename.
    """
    a = CrewInstallArgs.from_args(args)
    config_path = resolve_config_path(a.config, dict(os.environ), Path.home())
    pk = shutil.which("pk") or shutil.which("plan-keeper") or "pk"
    return run_crew_install(
        config_path,
        dry_run=a.dry_run,
        pk=pk,
        run_doctor=default_run_doctor,
        out=sys.stdout,
    )


def cmd_upgrade(args) -> int:
    """Update the plan-keeper Homebrew binary in place (brew upgrade +
    groundcrew re-wire).

    Composition root: hands the running binary's ``__version__`` and the real
    process runners to the testable orchestration in ``upgrade``.
    """
    return run_upgrade(
        old_version=__version__,
        which=shutil.which,
        stream=default_stream,
        capture=default_capture,
        out=sys.stdout,
    )


def _resolve_repo_plan_names(
    files: list[str], repo_override: Optional[str]
) -> list[Path]:
    """Resolve bare plan filenames against one repo's ~/plans/<repo>/ dir.

    Shared front half of `queue add` and `queue drop`: turns the user's bare
    filenames into validated absolute paths, all-or-nothing. `repo_override`
    (the `--repo` flag) names the repo; when None the repo is derived from the
    cwd exactly like `queue list`'s default scope. `.md` is appended when
    omitted.

    Every name must land *directly* inside the repo dir (so a `../other-repo/
    x.md` name can't cross repos), point at an existing file, and carry
    frontmatter — else a PlanKeeperCliError is raised. The whole batch is
    validated before any caller writes, so a typo can't half-mutate the queue.
    """
    repo = derive_repo(repo_override)
    repo_root = repo_dir(repo).resolve()
    resolved_paths: list[Path] = []
    for raw_name in files:
        name = raw_name if raw_name.endswith(".md") else raw_name + ".md"
        resolved = (repo_root / name).resolve()
        if resolved.parent != repo_root:
            raise PlanKeeperCliError(
                f"plan must be a bare filename in repo {repo!r}: {raw_name}",
                code=2,
            )
        if not resolved.exists():
            raise PlanKeeperCliError(
                f"plan not found in repo {repo!r}: {name}", code=3
            )
        text = resolved.read_text(encoding="utf-8")
        if not (text.startswith("---\n") or text.startswith("---\r\n")):
            raise PlanKeeperCliError(
                f"{resolved} has no frontmatter (cannot set Status)", code=2
            )
        resolved_paths.append(resolved)
    return resolved_paths


def _apply_queue_status(
    resolved_paths: list[Path], status: str, default_agent: Optional[str]
) -> int:
    """Write `status` to each already-validated plan, atomically.

    Shared write body of `queue add` (promote → todo) and `queue drop`
    (dequeue → backlog) — the two commands differ only in the status they pass
    and whether they fill an Agent, so the mutation lives here once instead of
    drifting between two call sites.

    Callers validate the whole batch FIRST (all-or-nothing), so by the time we
    write, every path is known-good. On `status == "todo"` (promote) each plan's
    id is minted (mint-once) and a plan with no Agent gets `default_agent`; a
    plan that already names an Agent keeps it. `status == "backlog"` (dequeue)
    never touches Agent or mints. Each written path is printed.
    """
    for resolved in resolved_paths:
        text = resolved.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        meta["Status"] = status
        if status == "todo":
            # Promote = "ready for groundcrew": ensure the plan-keeper id exists
            # (mint-once) so the mapping is visible the moment a plan is queued,
            # not only after the first dispatch tick.
            if default_agent and not meta.get("Agent", "").strip():
                meta["Agent"] = default_agent
            ensure_id(meta, resolved)  # mint-once into meta (no-op if present)
        new_text = serialize_frontmatter(meta, body)
        if not new_text.endswith("\n"):
            new_text += "\n"
        write_atomic(resolved, new_text)
        print(resolved)
    return 0


def cmd_queue_add(args) -> int:
    """Promote one-or-more plans to Status: todo by bare filename.

    The single-step promote: each positional names a plan file in `--repo`
    (default: the current repo, derived from the cwd). No absolute path, no
    stdin. Repo-scoping, `.md`-append, and all-or-nothing validation live in
    `_resolve_repo_plan_names`; the write (mint-once id, default Agent on a plan
    with none, atomic) is `_apply_queue_status`. Re-adding an already-todo plan
    is a harmless re-write.
    """
    a = QueueAddArgs.from_args(args)
    resolved = _resolve_repo_plan_names(a.files, a.repo)
    return _apply_queue_status(resolved, "todo", a.agent)


def cmd_queue_drop(args) -> int:
    """Dequeue one-or-more plans back to Status: backlog by bare filename.

    The inverse of `add`: pulls each named plan out of the groundcrew queue
    (todo → backlog) without touching its Agent or minting an id. Same
    repo-scoping and all-or-nothing validation as `add` via
    `_resolve_repo_plan_names`. Dropping an already-backlog plan is a no-op
    re-write.
    """
    a = QueueDropArgs.from_args(args)
    resolved = _resolve_repo_plan_names(a.files, a.repo)
    return _apply_queue_status(resolved, "backlog", None)


def cmd_queue_list(args) -> int:
    """Emit a JSON array of active plans, for plan-crew.

    Each element is {repo, file, status, agent} where status/agent are the
    raw frontmatter values ("" when unset). Scans ~/plans/<repo>/*.md one
    level deep — skips done/ and deferred/ (those are not dispatchable) and
    skips files without frontmatter (not plan-keeper plans). This is the
    read side of the groundcrew queue the plan-crew skill renders.

    Ordering: repos stay grouped in their outer alphabetical order, and the
    plans *within* each repo are sorted newest-first by `plan_recency_key`
    (the plan's `Created:` stamp, falling back to the filename date). So the
    queue reads most-recent-to-least-recent inside each repo block.

    Scope: by default only the current repo's plans (``derive_repo`` from the
    cwd). ``--all`` lists every repo under ~/plans/; ``--repo NAME`` lists one
    named repo (normalized like any repo override). The two flags are mutually
    exclusive at the parser, so at most one of ``args.all``/``args.repo`` is set.
    """
    a = QueueListArgs.from_args(args)
    if a.all:
        scope: Optional[str] = None
    elif a.repo:
        scope = validate_repo_name(normalize_override(a.repo))
    else:
        scope = derive_repo(None)
    rows: list[QueueRow] = []
    if not storage.PLAN_ROOT.exists():
        print("[]")
        return 0
    for repo_entry in sorted(storage.PLAN_ROOT.iterdir()):
        if not repo_entry.is_dir() or repo_entry.name.startswith("."):
            continue
        if scope is not None and repo_entry.name != scope:
            continue
        # (recency_key, row) within this repo so plans sort newest-first per
        # repo. Repos stay grouped in their outer alphabetical order; only the
        # plans inside each one are ordered most-recent-to-least-recent.
        # The repo index resolves each plan's Blocked-by refs so the row can
        # report dispatch-readiness for the plan-crew UI.
        index = _build_repo_index(repo_entry.name)
        keyed: list[tuple[tuple[str, str], QueueRow]] = []
        for plan in sorted(repo_entry.iterdir()):
            if not plan.is_file() or not plan.name.endswith(".md"):
                continue
            try:
                text = plan.read_text(encoding="utf-8")
            except OSError:
                continue
            if not (text.startswith("---\n") or text.startswith("---\r\n")):
                continue
            try:
                meta, _ = parse_frontmatter(text)
            except PlanKeeperCliError:
                continue
            _, unsatisfied = _blockers_for_plan(meta, index)
            row: QueueRow = {
                "repo": repo_entry.name,
                "file": plan.name,
                "status": meta.get("Status", "").strip(),
                "agent": meta.get("Agent", "").strip(),
                "blocked": bool(unsatisfied),
                "blockedBy": unsatisfied,
            }
            keyed.append((plan_recency_key(meta, plan.name), row))
        keyed.sort(key=lambda kr: kr[0], reverse=True)
        rows.extend(row for _, row in keyed)
    print(json.dumps(rows))
    return 0


# --- Parser -----------------------------------------------------------------


def _add_api_flags(p, provider: str) -> None:
    """Attach the credential flags an `api <kind>` call needs.

    --api-key is shared (Linear key / Jira token). The jira-only flags attach
    only under the jira subtree, so `linear api` never advertises them.
    """
    p.add_argument("--api-key", help="API key (Linear) or token (Jira)")
    if provider == "jira":
        p.add_argument("--email", help="email for Jira Basic auth")
        p.add_argument("--site", help="Jira site URL (e.g., herds.atlassian.net)")
        p.add_argument(
            "--project-key",
            help="project key (required for per-project kinds: "
                 "components/users/issuetypes)",
        )


def _add_push_target(p) -> None:
    """Attach the shared `--file | --ticket` push target (exactly one)."""
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="path to a plan .md file")
    g.add_argument(
        "--ticket",
        help="locate the plan by its Ticket: frontmatter across all repos "
             "(alternative to --file)",
    )


def _add_provider_parser(sub, provider: str) -> None:
    """Attach a `<provider> {api,push,config}` subtree for linear or jira.

    The provider is pinned via set_defaults(name=provider) so the shared
    handlers read it off args.name exactly as they did from the old --name
    flag. api kinds are per-provider argparse `choices`, making an invalid
    (provider, kind) pair unrepresentable at parse time.
    """
    p = sub.add_parser(
        provider,
        help=f"{provider} operations (api / push / config)",
    )
    p.set_defaults(name=provider)
    psub = p.add_subparsers(
        dest="provider_cmd", required=True, metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )

    p_api = psub.add_parser(
        "api", help="low-level API calls (used by setup and refresh)"
    )
    p_api.add_argument("api_kind", choices=_PROVIDER_API_KINDS[provider])
    _add_api_flags(p_api, provider)

    p_push = psub.add_parser(
        "push", help="create or update a ticket from a plan file"
    )
    _add_push_target(p_push)
    p_push.add_argument(
        "--force-new",
        action="store_true",
        help="ignore existing Ticket frontmatter and create a fresh ticket",
    )

    p_cfg = psub.add_parser(
        "config",
        help="CRUD for this provider's section in ~/plans/<repo>/.plankeeper.json",
    )
    cfg_sub = p_cfg.add_subparsers(
        dest="config_cmd", required=True, metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )
    p_cfg_get = cfg_sub.add_parser(
        "get", help="print this provider's config section as JSON"
    )
    p_cfg_get.add_argument(
        "--show-secrets",
        action="store_true",
        help="include credentials in output (default: redact apiKey/apiToken)",
    )
    cfg_sub.add_parser(
        "save", help="write this provider's config section (JSON on stdin)"
    )
    p_cfg_refresh = cfg_sub.add_parser("refresh", help="re-fetch metadata into cache")
    p_cfg_refresh.add_argument("--api-key", help="Linear API key (or Jira token)")
    if provider == "jira":
        p_cfg_refresh.add_argument("--email", help="Jira email")
        p_cfg_refresh.add_argument("--site", help="Jira site URL")


def build_parser() -> argparse.ArgumentParser:
    parser = HelpfulArgumentParser(
        prog=PROG,
        description="I/O backend for the plan-keeper skills.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(
        dest="cmd",
        required=True,
        metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )

    # `repo` is a pure parent (mirrors `crew`): a required subcommand selects
    # between resolving the current repo's folder name (`name`) and listing
    # every repo under ~/plans/ (`list`). Bare `repo` prints a usage error.
    p_repo = sub.add_parser(
        "repo",
        help="resolve repo folder name (name) / list all repos (list)",
    )
    repo_sub = p_repo.add_subparsers(
        dest="repo_cmd", required=True, metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )

    p_repo_name = repo_sub.add_parser(
        "name", help="print the resolved <repo> folder name"
    )
    p_repo_name.add_argument("--override", help="explicit override (normalized)")
    p_repo_name.add_argument("--cwd", help="working dir (defaults to $PWD)")
    p_repo_name.add_argument(
        "--full",
        action="store_true",
        help="emit owner/name (e.g., herds-social/herds) by parsing git remote origin URL",
    )

    repo_sub.add_parser(
        "list", help="list all repos under ~/plans/ with per-state counts"
    )

    # `repo alias` is a pure parent: add / list / remove are the three CRUD
    # subcommands that edit ~/plans/.plankeeper-global.json's `aliases` list.
    p_repo_alias = repo_sub.add_parser(
        "alias",
        help="add/list/remove monorepo-subpath -> alias mappings",
    )
    repo_alias_sub = p_repo_alias.add_subparsers(
        dest="repo_alias_cmd", required=True, metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )
    p_repo_alias_add = repo_alias_sub.add_parser(
        "add",
        help="register a monorepo subpath as a groundcrew alias",
    )
    p_repo_alias_add.add_argument(
        "target",
        help="<remote>[/<subpath>] — first segment is the remote (the bare "
        "repo basename as `repo name` would print it without aliasing, NOT "
        "the GitHub owner/name form), everything after the first slash is the "
        "subpath under the monorepo (e.g., 'carrot/catalog/flawless-inventory')",
    )
    p_repo_alias_add.add_argument(
        "name", help="the alias name plan-keeper routes to (e.g., 'maple')"
    )

    repo_alias_sub.add_parser(
        "list",
        help="print every alias, tab-separated: remote<TAB>subpath<TAB>name",
    )

    p_repo_alias_remove = repo_alias_sub.add_parser(
        "remove", help="remove every alias entry whose name matches"
    )
    p_repo_alias_remove.add_argument("name", help="the alias name to remove")

    p_list = sub.add_parser(
        "list",
        help="list plans for a repo (or every repo) newest-first",
    )
    # Scope is single-repo by default (override or git origin); --all-repos
    # forces cross-repo. The two are mutually exclusive. With neither flag and
    # no git origin, list also falls back to cross-repo (see cmd_list).
    list_scope = p_list.add_mutually_exclusive_group()
    list_scope.add_argument("--override", help="explicit override for <repo>")
    list_scope.add_argument(
        "--all-repos",
        action="store_true",
        help=(
            "list plans across every repo under ~/plans/ (alphabetical by repo, "
            "newest-first within). Output is prefixed 'repo/filename'. This is "
            "also the automatic behavior when there is no repo context (no "
            "--override and no git origin in the cwd)."
        ),
    )
    p_list.add_argument(
        "--state",
        choices=["active", "done", "deferred"],
        default="active",
        help="which subset to list (default: active)",
    )
    list_view = p_list.add_mutually_exclusive_group()
    list_view.add_argument(
        "--status",
        help=(
            "comma-separated Status values to keep (e.g. 'in-progress,todo'). "
            "Doubles as tier order: groups appear in the order given, newest-"
            "first within each. Output becomes 'status<TAB>filename' (or "
            "'status<TAB>repo/filename' in cross-repo mode). Missing/blank "
            "Status counts as 'backlog'. Excluded active plans are summarized "
            "on stderr (aggregated across repos in cross-repo mode). Omit to "
            "list bare filenames as before."
        ),
    )
    list_view.add_argument(
        "--group",
        action="store_true",
        help=(
            "cluster plans by project (shared slug), each stage labelled by its "
            "Kind and ordered along the idea->exec-plan pipeline. Groups appear "
            "most-recently-touched first. Human-readable view; mutually "
            "exclusive with --status."
        ),
    )

    p_save = sub.add_parser(
        "save",
        help="write body (stdin) to ~/plans/<repo>/<date>-<slug>.<ext> "
        "(or <date>-<slug>--<kind>.<ext> with --kind)",
    )
    p_save.add_argument("--override", help="explicit override for <repo>")
    p_save.add_argument(
        "--topic",
        help="topic string (will be slugified). Required for the heredoc shape; "
        "rejected when --from-path is given (the source basename is used).",
    )
    p_save.add_argument(
        "--date",
        help="YYYY-MM-DD date prefix for the heredoc shape (default: today). "
        "Rejected when --from-path is given.",
    )
    p_save.add_argument(
        "--extension",
        help="file extension for the heredoc shape (default: 'md'). Accepts "
        "'json', '.json', 'yaml', etc. Must match [a-z0-9]+ after stripping an "
        "optional leading dot. Rejected when --from-path is given.",
    )
    p_save.add_argument(
        "--from-path",
        help="move an existing on-disk file into ~/plans/<repo>/ instead of "
        "reading the body from stdin. The target keeps the source basename "
        "verbatim — --topic/--extension/--date are rejected. The source is "
        "only unlinked if the target write succeeded (collisions leave it in "
        "place, so retrying is safe). Used for task-list-builder output in "
        "docs/exec-plans/active/.",
    )
    p_save.add_argument(
        "--kind",
        help="document kind to inject as 'Kind: <value>' frontmatter on "
             "markdown saves. One of: " + ", ".join(VALID_KINDS) + ". "
             "Heredoc + .md shape only — rejected for non-md extensions and "
             "for --from-path (set Kind afterward via `file-meta set --kind`). "
             "Fill-if-absent: a Kind already in the body is preserved.",
    )
    p_save.add_argument(
        "--on-collision",
        choices=["fail", "suffix", "overwrite"],
        default="fail",
        help="what to do if the target file exists "
        "(default: fail with exit 2; use suffix for next unused -N)",
    )

    p_file_meta = sub.add_parser("file-meta", help="read/write/strip plan-file frontmatter")
    file_meta_sub = p_file_meta.add_subparsers(
        dest="file_meta_cmd",
        required=True,
        metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )

    def _add_locator(p) -> None:
        """Add the shared `--file | --ticket` locator group (exactly one).

        `--ticket` locates a plan by any of its id fields (Plan-keeper/Linear/
        Jira Ticket) across all repos — the same meaning it has on push, so the
        flag never doubles as a value setter (set writes an id into its own
        field via --plankeeper-ticket / --linear-ticket / --jira-ticket).
        """
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--file", help="path to a plan .md file")
        g.add_argument(
            "--ticket",
            help="locate the plan by any of its id fields (Plan-keeper/Linear/"
                 "Jira Ticket) across all repos (alternative to --file)",
        )

    p_fm_get = file_meta_sub.add_parser("get", help="print frontmatter as JSON")
    _add_locator(p_fm_get)

    p_fm_set = file_meta_sub.add_parser(
        "set", help="edit plan frontmatter (one flag per field)"
    )
    _add_locator(p_fm_set)
    p_fm_set.add_argument("--agent", help="set Agent")
    p_fm_set.add_argument(
        "--status",
        choices=list(LIFECYCLE_STATES),
        help="set Status. Active states (backlog/todo/in-progress/in-review) "
             "rewrite in place; 'done'/'deferred' relocate the plan into "
             "done/ or deferred/ ('done' also stamps Completed on).",
    )
    p_fm_set.add_argument(
        "--on-collision",
        choices=["fail", "suffix", "overwrite"],
        default="fail",
        help="when --status relocates to done/ or deferred/ and a same-name "
             "file already exists there: fail (default), suffix (-N), or overwrite",
    )
    p_fm_set.add_argument(
        "--kind",
        help="set Kind; one of: " + ", ".join(VALID_KINDS),
    )
    p_fm_set.add_argument("--completed-on", help="set Completed on (YYYY-MM-DD)")
    p_fm_set.add_argument(
        "--plankeeper-ticket", dest="plankeeper_ticket",
        help="set the Plan-keeper Ticket value (the plan-keeper id; normally "
             "minted automatically at save — set manually only to repair/override)",
    )
    p_fm_set.add_argument(
        "--linear-ticket", dest="linear_ticket",
        help="set the Linear Ticket value (an issue id like ENG-123)",
    )
    p_fm_set.add_argument(
        "--jira-ticket", dest="jira_ticket",
        help="set the Jira Ticket value (an issue key like PROJ-9)",
    )
    p_fm_set.add_argument(
        "--blocked-by", dest="blocked_by",
        help="set Blocked-by: a comma-separated list of prerequisite ticket IDs "
             "in the same repo (each may carry an optional '(filename)' hint that "
             "is ignored). Pass an empty string to clear. No existence check at "
             "set time — unresolved refs are flagged at crew fetch.",
    )

    p_fm_strip = file_meta_sub.add_parser("strip", help="print body without frontmatter")
    _add_locator(p_fm_strip)

    # --- noun-first provider subtrees: `linear …` / `jira …` ----------------
    # Each provider owns api/push/config as subcommands. set_defaults(name=…)
    # supplies the provider to the shared handlers (cmd_ticket_api/cmd_push/
    # cmd_ticket_system_config_*), which still read args.name — so the flip is
    # a parser change, not a handler rewrite. Provider-specific flags
    # (--site/--email/--project-key) and per-provider api `choices` live only
    # on the subtree where they apply.
    _add_provider_parser(sub, "linear")
    _add_provider_parser(sub, "jira")

    # `crew` groups the groundcrew dispatch adapter (fetch/get/start/review —
    # the machine protocol the crew.config.ts source calls directly) with
    # `install` (one-shot config wiring) and the `queue` manager the plan-crew
    # skill drives. The adapter legs deliberately avoid list/get/set naming:
    # fetch/start/review all mutate (fetch stamps the groundcrew Ticket;
    # start/review flip Status), so a read-only `list`/`get` label would
    # mislead. `queue` is the human/skill surface: `list` (read) plus the
    # `add` (promote → todo) / `drop` (dequeue → backlog) write pair, each
    # addressing plans by bare filename within a `--repo` (default: current).
    p_crew = sub.add_parser(
        "crew",
        help="groundcrew dispatch adapter (fetch/get/start/review) + install + queue",
    )
    crew_sub = p_crew.add_subparsers(
        dest="crew_cmd", required=True, metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )

    crew_sub.add_parser(
        "fetch",
        help="emit shell-adapter JSON array of active plans (for crew.config.ts fetch)",
    )

    p_crew_get = crew_sub.add_parser(
        "get",
        help="emit one shell-adapter issue JSON for ${id}, or exit 3 if missing",
    )
    p_crew_get.add_argument("id", help="synthesized plan id (plan-<digits>, from fetch)")

    p_crew_start = crew_sub.add_parser(
        "start",
        help="flip Status to in-progress on the plan named by ${id}",
    )
    p_crew_start.add_argument(
        "id", help="synthesized plan id (plan-<digits>, from fetch)"
    )

    p_crew_review = crew_sub.add_parser(
        "review",
        help="flip Status to in-review on the plan named by ${id}",
    )
    p_crew_review.add_argument(
        "id", help="synthesized plan id (plan-<digits>, from fetch)"
    )

    p_crew_install = crew_sub.add_parser(
        "install",
        help="wire ~/plans/* into a groundcrew config (idempotent, validated)",
    )
    p_crew_install.add_argument(
        "--config",
        help="path to the groundcrew config (.ts/.mjs/.js/.json; optional) "
             "(default: $GROUNDCREW_CONFIG or the first crew.config.* found in "
             "~/.config/groundcrew/)",
    )
    p_crew_install.add_argument(
        "--dry-run",
        action="store_true",
        help="print the diff that would be applied; write nothing",
    )

    p_crew_queue = crew_sub.add_parser(
        "queue",
        help="groundcrew queue: list active plans / add (promote) / drop (dequeue)",
    )
    crew_queue_sub = p_crew_queue.add_subparsers(
        dest="queue_cmd", required=True, metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )

    p_queue_list = crew_queue_sub.add_parser(
        "list",
        help="emit JSON array of active plans (repo/file/status/agent); "
             "current repo by default, --all for every repo",
    )
    queue_list_scope = p_queue_list.add_mutually_exclusive_group()
    queue_list_scope.add_argument(
        "--all", action="store_true",
        help="list plans across every repo under ~/plans/ "
             "(default: only the current repo)",
    )
    queue_list_scope.add_argument(
        "--repo",
        help="list plans for this repo instead of the current one "
             "(normalized like any repo override)",
    )

    p_queue_add = crew_queue_sub.add_parser(
        "add",
        help="promote plans to Status: todo by bare filename",
    )
    p_queue_add.add_argument(
        "files", nargs="+", metavar="<file>",
        help="bare plan filename(s) in the repo's ~/plans/<repo>/ "
             "(.md appended if omitted)",
    )
    p_queue_add.add_argument(
        "--repo",
        help="repo to resolve filenames against (default: the current repo)",
    )
    p_queue_add.add_argument(
        "--agent", default="claude",
        help="Agent to stamp on plans with none (default: claude); a plan "
             "that already names an Agent keeps it",
    )

    p_queue_drop = crew_queue_sub.add_parser(
        "drop",
        help="dequeue plans back to Status: backlog by bare filename",
    )
    p_queue_drop.add_argument(
        "files", nargs="+", metavar="<file>",
        help="bare plan filename(s) in the repo's ~/plans/<repo>/ "
             "(.md appended if omitted)",
    )
    p_queue_drop.add_argument(
        "--repo",
        help="repo to resolve filenames against (default: the current repo)",
    )

    sub.add_parser(
        "upgrade",
        help="update the plan-keeper Homebrew binary in place "
             "(brew upgrade + groundcrew re-wire)",
    )

    return parser


def _dispatch(
    table: Mapping[str, Callable[[argparse.Namespace], int]],
    key: str,
    label: str,
) -> Callable[[argparse.Namespace], int]:
    """Look up `key` in a dispatch table and return the handler, raising a
    PlanKeeperCliError (exit code 2) when the key is absent. Guards every
    dispatch table against an unwired-but-parseable subcommand producing an
    uncaught KeyError; argparse normally rejects unknown subcommands first, so
    this is defense-in-depth for a command that parses but has no handler.

    `table` is typed as a covariant Mapping so each module-level `_*_DISPATCH`
    dict (whose value types pyright infers from its handler literals) is
    assignable without annotating every table; the concrete value type lets
    pyright flag a handler with the wrong call signature or return type."""
    handler = table.get(key)
    if handler is None:
        raise PlanKeeperCliError(f"unknown {label}: {key!r}", code=2)
    return handler


# Sub-dispatch table for `file-meta <get|set|strip>`. Each entry handles one
# `file_meta_cmd`. Kept as a module-level constant so tasks adding `set` and
# `strip` only need to add one line here, not edit a lambda body in main().
_FILE_META_DISPATCH = {
    "get": cmd_file_meta_get,
    "set": cmd_file_meta_set,
    "strip": cmd_file_meta_strip,
}

_PROVIDER_CONFIG_DISPATCH = {
    "get": cmd_ticket_system_config_get,
    "save": cmd_ticket_system_config_save,
    "refresh": cmd_ticket_system_config_refresh,
}

# `<provider> <api|push|config>` — the provider (linear/jira) arrives on
# args.name via set_defaults, so one dispatch serves both subtrees.
_PROVIDER_DISPATCH = {
    "api": cmd_ticket_api,
    "push": cmd_push,
    "config": lambda a: _dispatch(
        _PROVIDER_CONFIG_DISPATCH, a.config_cmd, "config command"
    )(a),
}

_QUEUE_DISPATCH = {
    "list": cmd_queue_list,
    "add": cmd_queue_add,
    "drop": cmd_queue_drop,
}

_CREW_DISPATCH = {
    "fetch": cmd_crew_fetch,
    "get": cmd_crew_get,
    "start": cmd_crew_start,
    "review": cmd_crew_review,
    "install": cmd_crew_install,
    "queue": lambda a: _dispatch(_QUEUE_DISPATCH, a.queue_cmd, "queue command")(a),
}

_REPO_ALIAS_DISPATCH = {
    "add": cmd_repo_alias_add,
    "list": cmd_repo_alias_list,
    "remove": cmd_repo_alias_remove,
}

_REPO_DISPATCH = {
    "name": cmd_repo_name,
    "list": cmd_repo_list,
    "alias": lambda a: _dispatch(
        _REPO_ALIAS_DISPATCH, a.repo_alias_cmd, "alias command"
    )(a),
}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    # Top-level command table. Nested subcommands are not resolved here: each
    # group delegates to its module-level `_*_DISPATCH` table, which may itself
    # delegate again. E.g. `crew queue add` runs `crew` -> `_CREW_DISPATCH` ->
    # `queue` -> `_QUEUE_DISPATCH` -> `add`. Look in those tables for the leaf
    # handlers, not here.
    dispatch = {
        "repo": lambda a: _dispatch(_REPO_DISPATCH, a.repo_cmd, "repo command")(a),
        "list": cmd_list,
        "save": cmd_save,
        "file-meta": lambda a: _dispatch(
            _FILE_META_DISPATCH, a.file_meta_cmd, "file-meta command"
        )(a),
        "linear": lambda a: _dispatch(
            _PROVIDER_DISPATCH, a.provider_cmd, "linear command"
        )(a),
        "jira": lambda a: _dispatch(
            _PROVIDER_DISPATCH, a.provider_cmd, "jira command"
        )(a),
        "crew": lambda a: _dispatch(_CREW_DISPATCH, a.crew_cmd, "crew command")(a),
        "upgrade": cmd_upgrade,
    }
    try:
        return _dispatch(dispatch, args.cmd, "command")(args)
    except PlanKeeperCliError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
