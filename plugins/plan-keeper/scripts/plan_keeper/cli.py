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
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from plan_keeper import __version__, storage
from plan_keeper.config import (
    load_config,
    save_config,
    _redact_section,
)
from plan_keeper.dates import _iso_from_stat, parse_date_arg
from plan_keeper.errors import HelpfulArgumentParser, PlanKeeperCliError
from plan_keeper.frontmatter import (
    VALID_KINDS,
    _inject_default_frontmatter,
    parse_frontmatter,
    serialize_frontmatter,
    validate_kind,
)
from plan_keeper.groundcrew import (
    _apply_groundcrew_ticket,
    _assert_no_groundcrew_id_collisions,
    _plan_to_issue,
    _repo_for_plan,
    _stamp_groundcrew_ticket,
    groundcrew_id,
)
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
    slugify_topic,
    validate_extension,
    validate_repo_name,
)
from plan_keeper.push import push_subcommand
from plan_keeper.storage import (
    LIFECYCLE_STATES,
    TERMINAL_DIRS,
    emit_collision,
    find_unused_suffix,
    list_plans,
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


# --- Subcommands ------------------------------------------------------------


def cmd_repo(args) -> int:
    if args.full:
        print(derive_repo_full(args.cwd))
    else:
        print(derive_repo(args.override, args.cwd))
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

    Repos are iterated alphabetically (same enumeration as cmd_list_repos —
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
    raw_filter = getattr(args, "status", None)

    if args.override:
        explicit: Optional[str] = validate_repo_name(normalize_override(args.override))
    else:
        explicit = _repo_from_git()

    if args.all_repos or explicit is None:
        items = _all_repos_items(args.state)
    else:
        items = [(p.name, p) for p in list_plans(explicit, args.state)]
    return _render_listing(items, raw_filter)


def cmd_list_repos(args) -> int:
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


def cmd_backfill_created(args) -> int:
    """One-time, best-effort: stamp `Created` on plans that lack it.

    Newly saved plans get an exact `Created` at save time; this exists only
    to retrofit plans saved before the field existed, so list's newest-first
    sort orders them by something better than slug-alphabetical within a day.

    Source is each file's current birthtime (st_birthtime; falls back to
    st_mtime where birthtime is unavailable, e.g. some Linux filesystems).
    Best-effort by nature: status mutations rewrite plan files via
    write_atomic/os.replace, which resets birthtime to the last-write time —
    so for a plan that has been promoted or status-flipped since it was saved,
    the stamp reflects that last write, not the original save. The stamp is
    read *before* this command's own write, so backfilling never clobbers the
    value with its own rewrite time.

    Only touches .md files that already have frontmatter and have no `Created`
    yet. Non-.md siblings (paired .json) and bare files without frontmatter are
    skipped — they fall back to filename-date ordering. Covers the repo's
    active dir plus done/ and deferred/.
    """
    repo = derive_repo(args.override)
    base = repo_dir(repo)
    if not base.exists():
        print(f"no plans for repo {repo!r}", file=sys.stderr)
        return 0
    stamped = 0
    skipped = 0
    for d in (base, base / "done", base / "deferred"):
        if not d.exists():
            continue
        for path in sorted(d.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() != ".md":
                skipped += 1
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped += 1
                continue
            if not (text.startswith("---\n") or text.startswith("---\r\n")):
                skipped += 1
                continue
            try:
                meta, body = parse_frontmatter(text)
            except PlanKeeperCliError:
                skipped += 1
                continue
            if (meta.get("Created") or "").strip():
                skipped += 1
                continue
            # Best-effort: a stat/write failure on one file (permissions, I/O
            # error) must not abort the whole backfill — skip it and move on.
            try:
                meta["Created"] = _iso_from_stat(path.stat())
                new_text = serialize_frontmatter(meta, body)
                if not new_text.endswith("\n"):
                    new_text += "\n"
                write_atomic(path, new_text)
            except OSError:
                skipped += 1
                continue
            stamped += 1
    print(f"backfilled Created on {stamped} plan(s); skipped {skipped}")
    return 0


def cmd_save(args) -> int:
    repo = derive_repo(args.override)

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
    kind = validate_kind(args.kind) if args.kind is not None else None

    if args.from_path:
        for flag, value in (
            ("--topic", args.topic),
            ("--extension", args.extension),
            ("--date", args.date),
            ("--kind", args.kind),
        ):
            if value is not None:
                raise PlanKeeperCliError(
                    f"{flag} is incompatible with --from-path "
                    "(--from-path preserves the source bytes verbatim); "
                    f"drop {flag} or drop --from-path "
                    "(set Kind afterward via `file-meta set --kind ...`)",
                    code=2,
                )
        source = Path(args.from_path)
        if not source.exists():
            raise PlanKeeperCliError(f"source not found: {source}", code=3)
        if not source.is_file():
            raise PlanKeeperCliError(f"source is not a file: {source}", code=3)
        target = repo_dir(repo) / source.name
        ext = None  # --from-path never reaches the frontmatter-injection branch
    else:
        source = None
        if args.topic is None:
            raise PlanKeeperCliError(
                "--topic is required (unless --from-path is given, in which "
                "case the source basename is used and --topic is rejected)",
                code=2,
            )
        slug = slugify_topic(args.topic)
        if not slug:
            raise PlanKeeperCliError(
                f"topic {args.topic!r} slugified to empty string", code=2
            )
        ext = validate_extension(args.extension) if args.extension is not None else "md"
        if kind and ext != "md":
            raise PlanKeeperCliError(
                f"--kind only applies to .md saves (frontmatter lives in markdown); "
                f"got --extension {ext}. Drop --kind, or save the paired .md with it.",
                code=2,
            )
        date_str = (
            parse_date_arg(args.date) if args.date else date.today().isoformat()
        )
        target = repo_dir(repo) / f"{date_str}-{slug}.{ext}"

    if target.exists():
        if args.on_collision == "fail":
            # Critical: emit BEFORE any source mutation, so a `--move-source`
            # caller can safely retry without losing the source file.
            emit_collision(target)
            return 2
        if args.on_collision == "suffix":
            target = find_unused_suffix(target)
        # "overwrite" → fall through

    if source is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() == ".md":
            # A moved-in .md becomes a fully managed plan: fill the same
            # Agent/Status/Created block a heredoc .md save gets (fill-if-absent —
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
                args.agent,
                created=_iso_from_stat(source.stat()),
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
            content = _inject_default_frontmatter(content, args.agent, kind)
        write_atomic(target, content)
    print(target)
    return 0


def cmd_ticket_system_config_get(args) -> int:
    repo = derive_repo(None)
    config = load_config(repo)
    section = config.get(args.name)
    if section is None:
        raise PlanKeeperCliError(
            f"no config for ticket system {args.name!r} in repo {repo!r}",
            code=3,
        )
    output = section if args.show_secrets else _redact_section(section)
    print(json.dumps(output))
    return 0


def cmd_ticket_system_config_save(args) -> int:
    raw = sys.stdin.read()
    try:
        new_section = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PlanKeeperCliError(f"stdin is not valid JSON: {e}", code=2)
    if not isinstance(new_section, dict):
        raise PlanKeeperCliError("stdin must be a JSON object", code=2)
    repo = derive_repo(None)
    config = load_config(repo)
    config[args.name] = new_section
    path = save_config(repo, config)
    print(path)
    return 0


def cmd_ticket_system_config_list(args) -> int:
    del args
    repo = derive_repo(None)
    config = load_config(repo)
    # Only return keys that look like ticket-system sections.
    systems = [k for k in sorted(config.keys()) if k in ("linear", "jira")]
    print(json.dumps(systems))
    return 0


def cmd_ticket_system_config_refresh(args) -> int:
    if args.name == "linear":
        if not args.api_key:
            raise PlanKeeperCliError(
                "linear refresh requires --api-key", code=2,
            )
        refresh_linear_cache(args.api_key)
    elif args.name == "jira":
        if not args.site or not args.email or not args.api_key:
            raise PlanKeeperCliError(
                "jira refresh requires --site, --email, --api-key", code=2,
            )
        _validate_jira_site(args.site)
        refresh_jira_cache(args.site, args.email, args.api_key)
    return 0


def _resolve_file_meta_path(args) -> Path:
    """Resolve and validate the target for a file-meta subcommand.

    Locate by --ticket (cross-repo) or --file, then assert it's a regular
    file *before* any read — a directory passes exists() but would crash
    read_text() with IsADirectoryError. The "not a file" contract (exit 3)
    keeps every path-taking command failing the same clean way.
    """
    path = resolve_ticket_to_path(args.ticket) if args.ticket else Path(args.file)
    if not path.exists():
        raise PlanKeeperCliError(f"plan file not found: {path}", code=3)
    if not path.is_file():
        raise PlanKeeperCliError(f"not a file: {path}", code=3)
    return path


def cmd_file_meta_get(args) -> int:
    path = _resolve_file_meta_path(args)
    text = path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(text)
    print(json.dumps(meta))
    return 0


def cmd_file_meta_strip(args) -> int:
    path = _resolve_file_meta_path(args)
    text = path.read_text(encoding="utf-8")
    _, body = parse_frontmatter(text)
    sys.stdout.write(body)
    return 0


def cmd_file_meta_set(args) -> int:
    """Edit plan frontmatter via one self-documenting flag per field.

    The plan is located by `--file` or `--ticket` (the cross-repo locator,
    consistent with push). `--ticket` is *never* a value — the
    `Ticket:` frontmatter value is written with `--ticket-id`, so the word
    "ticket" means the same thing (locate) on every subcommand.

    Inputs are validated (Kind enum, Completed-on date) BEFORE the file is
    located or read, so a typo fails with exit 2 and never touches the file.
    The file must already have frontmatter — a bare file is rejected (exit 2)
    so a half-managed plan can't be created out from under plan-save's
    Agent/Status/Created defaults.
    """
    # Map each value flag to its frontmatter key, in canonical order. Building
    # this first means validate_kind/parse_date_arg run before any file I/O.
    updates: list[tuple[str, str]] = []
    if args.ticket_id is not None:
        updates.append(("Ticket", args.ticket_id))
    if args.ticket_system is not None:
        updates.append(("Ticket System", args.ticket_system))
    if args.completed_on is not None:
        updates.append(("Completed on", parse_date_arg(args.completed_on)))
    if args.agent is not None:
        updates.append(("Agent", args.agent))
    if args.status is not None:
        updates.append(("Status", args.status))
    if args.kind is not None:
        updates.append(("Kind", validate_kind(args.kind)))
    if not updates:
        raise PlanKeeperCliError(
            "file-meta set requires at least one of --ticket-id, --ticket-system, "
            "--completed-on, --agent, --status, --kind",
            code=2,
        )
    path = _resolve_file_meta_path(args)
    text = path.read_text(encoding="utf-8")
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        raise PlanKeeperCliError(
            f"{path} has no frontmatter — re-save via plan-save to get defaults",
            code=2,
        )
    meta, body = parse_frontmatter(text)  # may raise PlanKeeperCliError(5)
    for key, value in updates:
        meta[key] = value

    # A terminal status (done/deferred) is also a location: relocate the plan
    # into the matching subdir so Status and directory never disagree. Active
    # statuses are a pure in-place rewrite. `done` stamps Completed on (today,
    # unless the caller already supplied --completed-on).
    if args.status in TERMINAL_DIRS:
        if args.status == "done" and args.completed_on is None:
            meta["Completed on"] = date.today().isoformat()
        target = _terminal_target(path, args.status)
        if target != path and target.exists():
            if args.on_collision == "fail":
                emit_collision(target)
                return 2
            if args.on_collision == "suffix":
                target = find_unused_suffix(target)
            # "overwrite" → write_atomic replaces it below
    elif args.status is not None and path.parent.name in TERMINAL_DIRS:
        # Reactivating a terminal plan (moving it back to the active root) is
        # out of scope. Refuse loudly rather than rewrite in place and leave an
        # active-status plan parked in done/ or deferred/, where active list,
        # push --ticket, and ticket resolution would never find it.
        raise PlanKeeperCliError(
            f"cannot set active status {args.status!r} on a plan in "
            f"{path.parent.name}/ — reactivating a {path.parent.name} plan is "
            f"not supported; move the file back to the active dir first",
            code=2,
        )
    else:
        target = path

    new_text = serialize_frontmatter(meta, body)
    if not new_text.endswith("\n"):
        new_text += "\n"
    write_atomic(target, new_text)
    if target.resolve() != path.resolve():
        path.unlink()  # relocation: drop the source only after the dest write
    print(target)
    return 0


def _terminal_target(source: Path, status: str) -> Path:
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
    path = resolve_ticket_to_path(args.ticket) if args.ticket else Path(args.file)
    result = push_subcommand(args.name, str(path), force_new=args.force_new)
    print(json.dumps(result))
    return 0


_LINEAR_KINDS = {"viewer", "teams", "projects", "labels", "users"}
_JIRA_KINDS_BASE = {"viewer", "projects", "components", "users", "issuetypes"}
_JIRA_KINDS_NEED_PROJECT_KEY = {"components", "users", "issuetypes"}


def _validate_ticket_api_args(args) -> None:
    """Verify required flags are present for the requested (name, kind).

    Without this, missing flags slip through and cause downstream calls
    like `jira_viewer(None, None, None)` that surface as network or
    `_resolve_jira_project_id(... None)` errors. Validation up-front gives
    the user a clear CLI message instead.
    """
    kind = args.ticket_api_kind
    if args.name == "linear":
        if kind in _LINEAR_KINDS and not args.api_key:
            raise PlanKeeperCliError(
                f"ticket-api {kind} --name linear requires --api-key", code=2,
            )
    else:  # jira
        if kind in _JIRA_KINDS_BASE:
            for flag, value in (
                ("--site", args.site),
                ("--email", args.email),
                ("--api-key", args.api_key),
            ):
                if not value:
                    raise PlanKeeperCliError(
                        f"ticket-api {kind} --name jira requires {flag}",
                        code=2,
                    )
            _validate_jira_site(args.site)
        if kind in _JIRA_KINDS_NEED_PROJECT_KEY and not args.project_key:
            raise PlanKeeperCliError(
                f"ticket-api {kind} --name jira requires --project-key",
                code=2,
            )


def cmd_ticket_api(args) -> int:
    """Dispatch ticket-api subcommands.

    Each kind ({viewer, teams, projects, labels, users, components, issuetypes})
    is implemented by a per-system function. Output is always JSON to stdout.
    """
    _validate_ticket_api_args(args)
    if args.name == "linear":
        impl = {
            "viewer": lambda: linear_viewer(args.api_key),
            "teams": lambda: linear_teams(args.api_key),
            "projects": lambda: linear_projects(args.api_key),
            "labels": lambda: linear_labels(args.api_key),
            "users": lambda: linear_users(args.api_key),
        }
    else:  # jira
        site, email, token = args.site, args.email, args.api_key
        pkey = args.project_key
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
    fn = impl.get(args.ticket_api_kind)
    if fn is None:
        raise PlanKeeperCliError(
            f"ticket-api {args.ticket_api_kind} not implemented for {args.name}",
            code=2,
        )
    print(json.dumps(fn()))
    return 0


# --- groundcrew shell adapter ----------------------------------------------


def cmd_crew_fetch(args) -> int:
    """Emit a JSON array of issues for groundcrew's shell adapter to consume.

    Scans ~/plans/*/*.md (one level deep — skips done/ and deferred/).
    """
    del args
    issues: list[dict] = []
    if not storage.PLAN_ROOT.exists():
        print("[]")
        return 0
    for repo_entry in sorted(storage.PLAN_ROOT.iterdir()):
        if not repo_entry.is_dir() or repo_entry.name.startswith("."):
            continue
        for plan in sorted(repo_entry.iterdir()):
            if not plan.is_file() or not plan.name.endswith(".md"):
                continue
            issue = _plan_to_issue(plan)
            if issue is not None:
                issues.append(issue)
    _assert_no_groundcrew_id_collisions(issues)
    for issue in issues:
        _stamp_groundcrew_ticket(Path(issue["sourceRef"]["path"]), issue["id"])
    print(json.dumps(issues))
    return 0


_GROUNDCREW_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def cmd_crew_get(args) -> int:
    """Print one issue JSON for `${id}`, exit 3 if not found."""
    if not _GROUNDCREW_ID_RE.match(args.id):
        raise PlanKeeperCliError(
            f"invalid id {args.id!r}: must match [A-Za-z0-9._-]+ (no path separators)",
            code=2,
        )
    if not storage.PLAN_ROOT.exists():
        return 3
    # The id is synthesized (see groundcrew_id), so we can't map it straight
    # to a filename — instead recompute each plan's id and match. Search
    # active plans first, then done/, then deferred/, so a live plan wins
    # over an archived plan that shares its stem (same synthesized id).
    for repo_entry in storage.PLAN_ROOT.iterdir():
        if not repo_entry.is_dir() or repo_entry.name.startswith("."):
            continue
        for subdir in (repo_entry, repo_entry / "done", repo_entry / "deferred"):
            if not subdir.exists():
                continue
            for plan in sorted(subdir.iterdir()):
                if not plan.is_file() or not plan.name.endswith(".md"):
                    continue
                issue = _plan_to_issue(plan)
                if issue is not None and issue["id"] == args.id:
                    print(json.dumps(issue))
                    return 0
    return 3


def cmd_crew_start(args) -> int:
    """Read {'path': ...} from stdin, flip that plan's Status to in-progress.

    Validates the path is a string, absolute, points to a .md file, and
    resolves to a location inside PLAN_ROOT. This is defense-in-depth:
    groundcrew is the expected caller (and always produces well-formed
    sourceRef.path values), but the CLI is also auto-approved by a
    PreToolUse hook, so it should not be willing to mutate arbitrary
    .md files anywhere on disk.
    """
    del args
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PlanKeeperCliError(f"stdin is not valid JSON: {e}", code=2)
    if not isinstance(payload, dict) or "path" not in payload:
        raise PlanKeeperCliError(
            "stdin JSON must be {'path': <abs-path>}; 'path' field required",
            code=2,
        )
    raw_path = payload["path"]
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise PlanKeeperCliError(
            "stdin JSON field 'path' must be a non-empty string", code=2,
        )
    path = Path(raw_path)
    if not path.is_absolute():
        raise PlanKeeperCliError(f"path must be absolute: {path}", code=2)
    if path.suffix != ".md":
        raise PlanKeeperCliError(f"path must point to a .md plan file: {path}", code=2)
    resolved = path.resolve()
    plan_root = storage.PLAN_ROOT.resolve()
    try:
        resolved.relative_to(plan_root)
    except ValueError:
        raise PlanKeeperCliError(
            f"path is outside PLAN_ROOT ({plan_root}): {path}", code=2,
        )
    if not resolved.exists():
        raise PlanKeeperCliError(f"plan file not found: {resolved}", code=3)
    text = resolved.read_text(encoding="utf-8")
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        raise PlanKeeperCliError(
            f"{resolved} has no frontmatter (cannot mark in-progress)", code=2,
        )
    meta, body = parse_frontmatter(text)
    meta["Status"] = "in-progress"
    new_text = serialize_frontmatter(meta, body)
    if not new_text.endswith("\n"):
        new_text += "\n"
    write_atomic(resolved, new_text)
    print(resolved)
    return 0


def cmd_queue_set(args) -> int:
    """Bulk-set Status on plans named by newline-delimited stdin paths.

    Reads absolute plan paths (one per line) from stdin and writes each
    plan's frontmatter Status to --status (todo|backlog), atomically. When
    --status is todo and --default-agent is given, a plan whose Agent is
    missing/empty also gets Agent: <name> in the same update; a plan that
    already names an Agent keeps it. Dequeue (--status backlog) never
    touches Agent.

    Path validation mirrors `crew start`: every path must be
    absolute, end in .md, resolve inside PLAN_ROOT, exist, and have
    frontmatter. The whole batch is validated FIRST — if any path is
    invalid, nothing is written (all-or-nothing), so a typo can't leave the
    queue half-mutated.
    """
    raw = sys.stdin.read()
    paths = [line.strip() for line in raw.splitlines() if line.strip()]
    if not paths:
        raise PlanKeeperCliError("crew queue set: no plan paths on stdin", code=2)
    plan_root = storage.PLAN_ROOT.resolve()
    resolved_paths: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute():
            raise PlanKeeperCliError(f"path must be absolute: {path}", code=2)
        if path.suffix != ".md":
            raise PlanKeeperCliError(
                f"path must point to a .md plan file: {path}", code=2
            )
        resolved = path.resolve()
        try:
            resolved.relative_to(plan_root)
        except ValueError as err:
            raise PlanKeeperCliError(
                f"path is outside PLAN_ROOT ({plan_root}): {path}", code=2
            ) from err
        if not resolved.exists():
            raise PlanKeeperCliError(f"plan file not found: {resolved}", code=3)
        text = resolved.read_text(encoding="utf-8")
        if not (text.startswith("---\n") or text.startswith("---\r\n")):
            raise PlanKeeperCliError(
                f"{resolved} has no frontmatter (cannot set Status)", code=2
            )
        resolved_paths.append(resolved)
    for resolved in resolved_paths:
        text = resolved.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        meta["Status"] = args.status
        if args.status == "todo":
            # Promote = "ready for groundcrew", so claim the groundcrew Ticket
            # now (same id fetch would synthesize) — the mapping is visible the
            # moment a plan is queued, not only after the first dispatch tick.
            if args.default_agent and not meta.get("Agent", "").strip():
                meta["Agent"] = args.default_agent
            _apply_groundcrew_ticket(
                meta, groundcrew_id(_repo_for_plan(resolved), resolved.stem)
            )
        new_text = serialize_frontmatter(meta, body)
        if not new_text.endswith("\n"):
            new_text += "\n"
        write_atomic(resolved, new_text)
        print(resolved)
    return 0


def cmd_queue_list(args) -> int:
    """Emit a JSON array of active plans across all repos, for plan-crew.

    Each element is {repo, file, status, agent} where status/agent are the
    raw frontmatter values ("" when unset). Scans ~/plans/<repo>/*.md one
    level deep — skips done/ and deferred/ (those are not dispatchable) and
    skips files without frontmatter (not plan-keeper plans). This is the
    read side of the groundcrew queue the plan-crew skill renders.
    """
    del args
    rows: list[dict] = []
    if not storage.PLAN_ROOT.exists():
        print("[]")
        return 0
    for repo_entry in sorted(storage.PLAN_ROOT.iterdir()):
        if not repo_entry.is_dir() or repo_entry.name.startswith("."):
            continue
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
            rows.append({
                "repo": repo_entry.name,
                "file": plan.name,
                "status": meta.get("Status", "").strip(),
                "agent": meta.get("Agent", "").strip(),
            })
    print(json.dumps(rows))
    return 0


# --- Parser -----------------------------------------------------------------


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

    p_repo = sub.add_parser("repo", help="print the resolved <repo> folder name")
    p_repo.add_argument("--override", help="explicit override (normalized)")
    p_repo.add_argument("--cwd", help="working dir (defaults to $PWD)")
    p_repo.add_argument(
        "--full",
        action="store_true",
        help="emit owner/name (e.g., herds-social/herds) by parsing git remote origin URL",
    )

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
    p_list.add_argument(
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

    sub.add_parser(
        "list-repos",
        help="list all repos under ~/plans/ with per-state counts",
    )

    p_backfill = sub.add_parser(
        "backfill-created",
        help="one-time: stamp `Created` (from file birthtime) on plans missing it",
    )
    p_backfill.add_argument("--override", help="explicit override for <repo>")

    p_save = sub.add_parser(
        "save",
        help="write body (stdin) to ~/plans/<repo>/<date>-<slug>.<ext>",
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
        "--agent",
        default="claude",
        help="agent name to inject as 'Agent: <name>' frontmatter on "
             "markdown saves (default: claude). Heredoc + .md shape only; "
             "ignored for --extension other than md and for --from-path.",
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

        `--ticket` locates a plan by its Ticket: frontmatter across all repos
        — the same meaning it has on push, so the flag never doubles
        as a value setter (set writes the Ticket value via --ticket-id).
        """
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--file", help="path to a plan .md file")
        g.add_argument(
            "--ticket",
            help="locate the plan by its Ticket: frontmatter across all repos "
                 "(alternative to --file)",
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
        "--ticket-system", choices=["linear", "jira"], help="set Ticket System"
    )
    p_fm_set.add_argument(
        "--ticket-id",
        help="set the Ticket: value (distinct from --ticket, which locates a "
             "plan; use --ticket-id to record an issue id like ENG-123)",
    )

    p_fm_strip = file_meta_sub.add_parser("strip", help="print body without frontmatter")
    _add_locator(p_fm_strip)

    p_tsc = sub.add_parser(
        "ticket-system-config",
        help="CRUD for ticket-system entries in ~/plans/<repo>/.plankeeper.json",
    )
    tsc_sub = p_tsc.add_subparsers(
        dest="tsc_cmd", required=True, metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )

    p_tsc_get = tsc_sub.add_parser("get", help="print one ticket-system section as JSON")
    p_tsc_get.add_argument("--name", required=True, choices=["linear", "jira"])
    p_tsc_get.add_argument(
        "--show-secrets",
        action="store_true",
        help="include credentials in output (default: redact apiKey/apiToken)",
    )

    p_tsc_save = tsc_sub.add_parser("save", help="write a ticket-system section (JSON on stdin)")
    p_tsc_save.add_argument("--name", required=True, choices=["linear", "jira"])

    _ = tsc_sub.add_parser("list", help="list configured ticket-system names")

    p_tsc_refresh = tsc_sub.add_parser("refresh", help="re-fetch metadata into cache")
    p_tsc_refresh.add_argument("--name", required=True, choices=["linear", "jira"])
    p_tsc_refresh.add_argument("--api-key", help="Linear API key (or Jira token)")
    p_tsc_refresh.add_argument("--email", help="Jira email")
    p_tsc_refresh.add_argument("--site", help="Jira site URL")

    p_ta = sub.add_parser(
        "ticket-api",
        help="low-level Linear/Jira API calls (used by setup and refresh)",
    )
    p_ta.add_argument(
        "ticket_api_kind",
        choices=["viewer", "teams", "projects", "labels", "users", "components", "issuetypes"],
    )
    p_ta.add_argument("--name", required=True, choices=["linear", "jira"])
    p_ta.add_argument("--api-key", help="API key (Linear) or token (Jira)")
    p_ta.add_argument("--email", help="email for Jira Basic auth")
    p_ta.add_argument("--site", help="Jira site URL (e.g., herds.atlassian.net)")
    p_ta.add_argument(
        "--project-key",
        help="project key (Jira; required for per-project kinds)",
    )

    p_push = sub.add_parser("push", help="create or update a ticket from a plan file")
    p_push.add_argument("--name", required=True, choices=["linear", "jira"])
    push_target = p_push.add_mutually_exclusive_group(required=True)
    push_target.add_argument("--file", help="path to a plan .md file")
    push_target.add_argument(
        "--ticket",
        help="locate the plan by its Ticket: frontmatter across all repos "
             "(alternative to --file)",
    )
    p_push.add_argument(
        "--force-new",
        action="store_true",
        help="ignore existing Ticket frontmatter and create a fresh ticket",
    )

    # `crew` groups the groundcrew dispatch adapter (fetch/get/start — the
    # machine protocol the crew.config.ts shell wrappers call) with the
    # cross-repo `queue` manager the plan-crew skill drives. fetch/get/start
    # deliberately avoid list/get/set naming: fetch and start both mutate
    # (fetch stamps the groundcrew Ticket; start flips Status), so a read-only
    # `list`/`get` label would mislead — and `crew queue list`/`crew queue set` are the
    # genuinely read-only / general-write pair.
    p_crew = sub.add_parser(
        "crew",
        help="groundcrew dispatch adapter (fetch/get/start) + cross-repo queue",
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

    crew_sub.add_parser(
        "start",
        help="flip Status to in-progress on a plan named by stdin sourceRef JSON",
    )

    p_crew_queue = crew_sub.add_parser(
        "queue",
        help="cross-repo groundcrew queue: list active plans / set Status in bulk",
    )
    crew_queue_sub = p_crew_queue.add_subparsers(
        dest="queue_cmd", required=True, metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )

    crew_queue_sub.add_parser(
        "list",
        help="emit JSON array of active plans across all repos "
             "(repo/file/status/agent)",
    )

    p_queue_set = crew_queue_sub.add_parser(
        "set",
        help="set Status on plans named by newline-delimited stdin paths",
    )
    p_queue_set.add_argument(
        "--status", required=True, choices=["todo", "backlog"],
        help="Status to write on every listed plan",
    )
    p_queue_set.add_argument(
        "--default-agent",
        help="when --status todo, fill Agent: <name> on plans with no Agent "
             "set (ignored for --status backlog)",
    )

    return parser


# Sub-dispatch table for `file-meta <get|set|strip>`. Each entry handles one
# `file_meta_cmd`. Kept as a module-level constant so tasks adding `set` and
# `strip` only need to add one line here, not edit a lambda body in main().
_FILE_META_DISPATCH = {
    "get": cmd_file_meta_get,
    "set": cmd_file_meta_set,
    "strip": cmd_file_meta_strip,
}

_TICKET_SYSTEM_CONFIG_DISPATCH = {
    "get": cmd_ticket_system_config_get,
    "save": cmd_ticket_system_config_save,
    "list": cmd_ticket_system_config_list,
    "refresh": cmd_ticket_system_config_refresh,
}

_QUEUE_DISPATCH = {
    "list": cmd_queue_list,
    "set": cmd_queue_set,
}

_CREW_DISPATCH = {
    "fetch": cmd_crew_fetch,
    "get": cmd_crew_get,
    "start": cmd_crew_start,
    "queue": lambda a: _QUEUE_DISPATCH[a.queue_cmd](a),
}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "repo": cmd_repo,
        "list": cmd_list,
        "list-repos": cmd_list_repos,
        "backfill-created": cmd_backfill_created,
        "save": cmd_save,
        "file-meta": lambda a: _FILE_META_DISPATCH[a.file_meta_cmd](a),
        "ticket-system-config": lambda a: _TICKET_SYSTEM_CONFIG_DISPATCH[a.tsc_cmd](a),
        "ticket-api": cmd_ticket_api,
        "push": cmd_push,
        "crew": lambda a: _CREW_DISPATCH[a.crew_cmd](a),
    }
    try:
        return dispatch[args.cmd](args)
    except PlanKeeperCliError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
