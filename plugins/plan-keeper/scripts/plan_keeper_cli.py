#!/usr/bin/env python3
"""I/O + naming + mutation backend for the plan-keeper skills.

Single canonical interface for `plan-save`, `plan-do`, `plan-done`.
Replaces inline bash (mkdir, mv, ls, date, file appending) in each
SKILL.md with a small set of subcommands.

The algorithm for repo derivation lives in
`plugins/plan-keeper/repo-derivation.md` and is implemented here.
See each `plan-*` SKILL.md for invocation patterns.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode


PLAN_ROOT = Path.home() / "plans"
MAX_SLUG_LEN = 50
MAX_SUFFIX = 99
CONFIG_FILE_NAME = ".plankeeper.json"

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


class HelpfulArgumentParser(argparse.ArgumentParser):
    """Print full help (not just usage) before erroring on bad args."""

    def error(self, message: str):
        self.print_help(sys.stderr)
        self.exit(2, f"\n{self.prog}: error: {message}\n")


class PlanKeeperCliError(Exception):
    """Expected, user-facing errors. Carries an exit code."""

    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.code = code


# --- Helpers ----------------------------------------------------------------


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Slugify ----------------------------------------------------------------


def slugify_topic(text: str) -> str:
    """Slugify a topic string for use as a filename component.

    Single source of truth for the slugify rule. SKILL.md docs describe
    the contract; the implementation lives here.

    Rules:
    - Lowercase.
    - Allowed chars: [a-z0-9_-]. Underscores preserved.
    - Runs of non-allowed chars collapse to a single `-`.
    - Trim leading/trailing `-`.
    - Truncate to 50 chars at a word boundary (split on `-`).
    """
    s = text.strip().lower()
    out: list[str] = []
    last_dash = False
    for ch in s:
        if ch.isalnum() or ch in "_-":
            out.append(ch)
            last_dash = ch == "-"
        elif not last_dash:
            out.append("-")
            last_dash = True
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    if len(slug) <= MAX_SLUG_LEN:
        return slug
    parts = slug.split("-")
    kept: list[str] = []
    used = 0
    for p in parts:
        new_len = len(p) if not kept else used + 1 + len(p)
        if new_len > MAX_SLUG_LEN:
            break
        kept.append(p)
        used = new_len
    if kept:
        return "-".join(kept)
    return slug[:MAX_SLUG_LEN].rstrip("-")


# --- Repo derivation --------------------------------------------------------


def normalize_override(name: str) -> str:
    """Apply repo-derivation.md step-2 normalization.

    Lowercase + whitespace→hyphen, preserve everything else (including
    underscores and existing hyphens). Per the asymmetry called out in
    repo-derivation.md: user-typed overrides get this light normalization;
    git-remote-derived names stay verbatim.
    """
    s = name.strip().lower()
    out: list[str] = []
    last_dash = False
    for ch in s:
        if ch.isspace():
            if not last_dash:
                out.append("-")
                last_dash = True
        else:
            out.append(ch)
            last_dash = ch == "-"
    return "".join(out).strip("-")


def validate_repo_name(name: str) -> str:
    """Reject repo names that would escape ~/plans/<repo>/ or are empty.

    Defense against path traversal via untrusted `--override`, an
    odd-shaped git remote URL, or a weird cwd basename. Empty / "." /
    ".." would resolve `PLAN_ROOT / repo` outside the intended dir;
    a slash- or backslash-containing name would compose multiple path
    components and skip past `~/plans/`.
    """
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise PlanKeeperCliError(f"invalid repo name: {name!r}", code=2)
    return name


_EXTENSION_RE = re.compile(r"^[a-z0-9]+$")


def validate_extension(ext: str) -> str:
    """Normalize and validate a file-extension argument.

    Strips a single leading dot, then requires `^[a-z0-9]+$`. Stricter
    than `validate_repo_name` because file extensions have a narrower
    convention: a single token of lowercase alphanumerics. Dots inside
    the value, uppercase, whitespace, slashes, etc., would either let
    the caller smuggle additional path components or produce surprising
    filenames like `*.MD.bak`.
    """
    if ext.startswith("."):
        ext = ext[1:]
    if not _EXTENSION_RE.match(ext):
        raise PlanKeeperCliError(
            f"invalid extension {ext!r}: must match [a-z0-9]+", code=2,
        )
    return ext


def derive_repo(override: Optional[str], cwd: Optional[str] = None) -> str:
    """Resolve <repo> per repo-derivation.md."""
    if override:
        return validate_repo_name(normalize_override(override))
    cwd = cwd or os.getcwd()
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            if url:
                base = os.path.basename(url)
                if base.endswith(".git"):
                    base = base[:-4]
                if base:
                    return validate_repo_name(base)
    except (subprocess.SubprocessError, OSError):
        pass
    return validate_repo_name(os.path.basename(os.path.abspath(cwd)))


_GITHUB_URL_RE = re.compile(
    r"^(?:"
    r"git@github\.com:"
    r"|https?://github\.com/"
    r"|ssh://git@github\.com/"
    r")(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
)


def derive_repo_full(cwd: Optional[str] = None) -> str:
    """Return 'owner/name' for the current repo, or 'unknown/<basename>' as a fallback.

    Used by the push subcommand's 'Repo:' description line. Distinct from
    derive_repo() which strips to a single token for use as a folder name.
    """
    cwd = cwd or os.getcwd()
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            m = _GITHUB_URL_RE.match(url)
            if m:
                return f"{m.group('owner')}/{m.group('name')}"
    except (subprocess.SubprocessError, OSError):
        pass
    basename = os.path.basename(os.path.abspath(cwd))
    return f"unknown/{validate_repo_name(basename)}"


# --- Atomic write -----------------------------------------------------------


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


# --- Path helpers -----------------------------------------------------------


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
    elif state == "done":
        d = base / "done"
    elif state == "deferred":
        d = base / "deferred"
    else:
        raise PlanKeeperCliError(f"unknown state: {state}", code=2)
    if not d.exists():
        return []
    files = [p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")]
    files.sort(key=lambda p: p.name, reverse=True)
    return files


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


def parse_date_arg(s: str) -> str:
    """Validate a YYYY-MM-DD argument and return it as an ISO string."""
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError as e:
        raise PlanKeeperCliError(f"invalid date {s!r}: {e}", code=2)


def emit_collision(target: Path) -> None:
    """Print structured collision diagnostic to stderr."""
    suggestion = find_unused_suffix(target)
    print("ERROR: collision", file=sys.stderr)
    print(f"existing: {target}", file=sys.stderr)
    print(f"suggestion: {suggestion}", file=sys.stderr)


# --- Config helpers ---------------------------------------------------------


def config_path(repo: str) -> Path:
    return repo_dir(repo) / CONFIG_FILE_NAME


def load_config(repo: str) -> dict:
    """Read the per-repo config JSON. Returns {} if file is missing.

    Raises PlanKeeperCliError(5) on malformed JSON.
    """
    path = config_path(repo)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PlanKeeperCliError(f"malformed config at {path}: {e}", code=5)


def save_config(repo: str, data: dict) -> Path:
    """Atomically write the per-repo config JSON, then chmod 600.

    The chmod is best-effort — if it fails the write itself still
    succeeds (just with default permissions). A warning is printed
    to stderr.
    """
    path = config_path(repo)
    write_atomic(path, json.dumps(data, indent=2) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        print(
            f"warning: couldn't chmod 600 {path}: {e}",
            file=sys.stderr,
        )
    return path


# --- Subcommands ------------------------------------------------------------


def cmd_repo(args) -> int:
    if args.full:
        print(derive_repo_full(args.cwd))
    else:
        print(derive_repo(args.override, args.cwd))
    return 0


def cmd_list(args) -> int:
    repo = derive_repo(args.override)
    plans = list_plans(repo, args.state)

    raw_filter = getattr(args, "status", None)
    if not raw_filter:
        for p in plans:
            print(p.name)
        return 0

    # Status-filtered listing. The filter doubles as the tier order: plans are
    # grouped by the requested statuses in the order given (e.g. "in-progress,
    # todo" => in-progress group first), newest-first within each group. Output
    # is `status<TAB>filename` so callers can render "[status] filename".
    tiers = [s.strip().lower() for s in raw_filter.split(",") if s.strip()]
    tier_rank = {s: i for i, s in enumerate(tiers)}
    annotated = [(p, plan_status(p)) for p in plans]

    shown = [(p, s) for (p, s) in annotated if s in tier_rank]
    # list_plans is already newest-first; a stable sort by tier preserves that
    # within each group.
    shown.sort(key=lambda ps: tier_rank[ps[1]])
    for p, s in shown:
        print(f"{s}\t{p.name}")

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


def cmd_list_repos(args) -> int:
    del args
    if not PLAN_ROOT.exists():
        return 0
    def _count(d: Path) -> int:
        if not d.exists():
            return 0
        return sum(
            1 for p in d.iterdir() if p.is_file() and not p.name.startswith(".")
        )

    for entry in sorted(PLAN_ROOT.iterdir()):
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
                    "(set Kind afterward via `file-meta update --field Kind=...`)",
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
        # File already exists on disk — relocate it byte-for-byte, preserving
        # mtime/permissions and avoiding the trailing-newline normalization
        # that write_atomic applies. shutil.move uses os.rename when src and
        # dst are on the same filesystem (a single atomic syscall), and falls
        # back to copy2 + unlink across filesystems.
        target.parent.mkdir(parents=True, exist_ok=True)
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


def cmd_archive(args) -> int:
    repo = derive_repo(args.override)
    if "/" in args.file or "\\" in args.file or args.file in ("", ".", ".."):
        raise PlanKeeperCliError(
            f"--file must be a basename only (no path separators), got: {args.file!r}",
            code=2,
        )
    source = repo_dir(repo) / args.file
    if not source.exists():
        raise PlanKeeperCliError(f"plan not found: {source}", code=3)
    if not source.is_file():
        raise PlanKeeperCliError(f"not a file: {source}", code=3)

    target = repo_dir(repo) / "done" / args.file
    if target.exists():
        if args.on_collision == "fail":
            emit_collision(target)
            return 2
        if args.on_collision == "suffix":
            target = find_unused_suffix(target)
        # "overwrite" → fall through

    completed = (
        parse_date_arg(args.completed_date)
        if args.completed_date
        else date.today().isoformat()
    )
    text = source.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    meta["Completed on"] = completed
    stamped = serialize_frontmatter(meta, body)
    if not stamped.endswith("\n"):
        stamped += "\n"

    write_atomic(target, stamped)
    source.unlink()
    print(target)
    return 0


_SECRET_CONFIG_FIELDS = ("apiKey", "apiToken")


def _redact_section(section: dict) -> dict:
    """Return a copy of `section` with credential fields masked.

    Credentials live in the section root (e.g., `apiKey` for linear,
    `apiToken` for jira). The picker UI in the setup wizard reads
    everything ELSE from the section (`defaults`, `cache`) — those are
    not sensitive. Masking only the secret-named fields preserves the
    structure callers expect.
    """
    out = dict(section)
    for key in _SECRET_CONFIG_FIELDS:
        if key in out and out[key]:
            out[key] = "***redacted***"
    return out


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


# --- Frontmatter ------------------------------------------------------------

# Order matters in the output — keep this canonical so callers see a stable shape.
_FRONTMATTER_FIELDS = ("Ticket", "Ticket System", "Completed on", "Agent", "Status", "Kind")

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


def _inject_default_frontmatter(body_text: str, agent: str, kind: Optional[str] = None) -> str:
    """Ensure body_text starts with frontmatter containing Agent and Status
    (and Kind, when a kind is supplied).

    Three cases:
      1. body has no frontmatter → prepend a fresh '---\\nAgent: <agent>\\nStatus: backlog\\n---\\n\\n' block.
      2. body has frontmatter with the fields already set → return unchanged
         (user-supplied values win over defaults).
      3. body has frontmatter missing some → fill in the missing fields,
         re-serialize, return.

    Why agent/status/kind are 'fill if absent' rather than 'overwrite':
    a user who hand-wrote `Status: todo` (or `Kind: prd`) in the body shouldn't
    have it stomped by the save invocation. The CLI default is a floor, not an
    override. `kind` is only written when the caller passed one — there is no
    default Kind, because an absent Kind is a valid state (plan-do then infers
    it from the content instead).
    """
    meta, body = parse_frontmatter(body_text)
    if not meta.get("Agent"):
        meta["Agent"] = agent
    if not meta.get("Status"):
        meta["Status"] = "backlog"
    if kind and not meta.get("Kind"):
        meta["Kind"] = kind
    out = serialize_frontmatter(meta, body)
    if not out.endswith("\n"):
        out += "\n"
    return out


def cmd_file_meta_get(args) -> int:
    path = Path(args.file)
    if not path.exists():
        raise PlanKeeperCliError(f"plan file not found: {path}", code=3)
    text = path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(text)
    print(json.dumps(meta))
    return 0


def cmd_file_meta_strip(args) -> int:
    path = Path(args.file)
    if not path.exists():
        raise PlanKeeperCliError(f"plan file not found: {path}", code=3)
    text = path.read_text(encoding="utf-8")
    _, body = parse_frontmatter(text)
    sys.stdout.write(body)
    return 0


def cmd_file_meta_set(args) -> int:
    # At least one of the set flags must be provided.
    if (
        args.ticket is None
        and args.ticket_system is None
        and args.completed_on is None
    ):
        raise PlanKeeperCliError(
            "file-meta set requires at least one of --ticket, --ticket-system, --completed-on",
            code=2,
        )
    path = Path(args.file)
    if not path.exists():
        raise PlanKeeperCliError(f"plan file not found: {path}", code=3)
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)  # may raise PlanKeeperCliError(5)
    if args.ticket is not None:
        meta["Ticket"] = args.ticket
    if args.ticket_system is not None:
        meta["Ticket System"] = args.ticket_system
    if args.completed_on is not None:
        # Validate the date format up front to catch typos.
        meta["Completed on"] = parse_date_arg(args.completed_on)
    new_text = serialize_frontmatter(meta, body)
    if not new_text.endswith("\n"):
        new_text += "\n"
    write_atomic(path, new_text)
    print(path)
    return 0


def cmd_file_meta_update(args) -> int:
    """Generic frontmatter editor. Each --field is 'Key=value'.

    Unlike cmd_file_meta_set (which has per-field flags), this accepts any
    whitelisted key via a single --field shape. Used by the plan-update
    skill and by the groundcrew markInProgress wrapper.

    Whitelist semantics: unknown keys are rejected with code 2 before any
    write. The file must already have frontmatter (no auto-creation) — if
    it doesn't, the user must re-save via plan-save first so they go
    through the agent/status defaults path.
    """
    if not args.field:
        raise PlanKeeperCliError(
            "file-meta update requires at least one --field key=value", code=2,
        )
    updates: list[tuple[str, str]] = []
    for raw in args.field:
        if "=" not in raw:
            raise PlanKeeperCliError(
                f"--field must be key=value (got {raw!r}); add an '=' or quote the value",
                code=2,
            )
        key, _, value = raw.partition("=")
        key = key.strip()
        if not key:
            raise PlanKeeperCliError(
                f"--field {raw!r}: empty key", code=2,
            )
        if key not in _FRONTMATTER_FIELDS:
            raise PlanKeeperCliError(
                f"unknown frontmatter field {key!r}: must be one of "
                + ", ".join(repr(k) for k in _FRONTMATTER_FIELDS),
                code=2,
            )
        if key == "Kind" and value.strip():
            value = validate_kind(value)
        updates.append((key, value))
    path = Path(args.file)
    if not path.exists():
        raise PlanKeeperCliError(f"plan file not found: {path}", code=3)
    text = path.read_text(encoding="utf-8")
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        raise PlanKeeperCliError(
            f"{path} has no frontmatter — re-save via plan-save to get defaults",
            code=2,
        )
    meta, body = parse_frontmatter(text)  # may raise PlanKeeperCliError(5)
    for key, value in updates:
        meta[key] = value
    new_text = serialize_frontmatter(meta, body)
    if not new_text.endswith("\n"):
        new_text += "\n"
    write_atomic(path, new_text)
    print(path)
    return 0


# --- HTTP helpers -----------------------------------------------------------

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
HTTP_TIMEOUT = 30


def http_post_json(
    url: str,
    payload: dict,
    headers: dict[str, str],
) -> dict:
    """POST a JSON body, return the decoded JSON response.

    Single chokepoint for all outbound HTTP. Maps urllib exceptions to
    PlanKeeperCliError with the documented exit codes:
        3 — 401/403 auth failures
        4 — DNS/connection/timeout failures
        5 — non-2xx HTTP responses with the body in the error message
    """
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PlanKeeperCliError(f"auth failure ({e.code}): {e.reason}", code=3)
        raise PlanKeeperCliError(f"HTTP {e.code}: {e.reason}", code=5)
    except urllib.error.URLError as e:
        raise PlanKeeperCliError(f"network error: {e.reason}", code=4)
    except Exception as e:
        raise PlanKeeperCliError(f"unexpected error: {e}", code=5)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise PlanKeeperCliError(f"non-JSON response: {e}; body={body[:200]!r}", code=5)


def http_get_json(url: str, headers: dict[str, str]) -> dict:
    """GET a URL, return the decoded JSON response. Same error mapping as http_post_json."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PlanKeeperCliError(f"auth failure ({e.code}): {e.reason}", code=3)
        raise PlanKeeperCliError(f"HTTP {e.code}: {e.reason}", code=5)
    except urllib.error.URLError as e:
        raise PlanKeeperCliError(f"network error: {e.reason}", code=4)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise PlanKeeperCliError(f"non-JSON response: {e}; body={body[:200]!r}", code=5)


def linear_viewer(api_key: str) -> dict:
    """Call Linear's viewer query — returns {id, name, email} on success."""
    query = "query { viewer { id name email } }"
    resp = http_post_json(
        LINEAR_GRAPHQL_URL,
        {"query": query},
        {"Authorization": api_key},
    )
    if "errors" in resp:
        raise PlanKeeperCliError(f"Linear API error: {resp['errors']}", code=5)
    return resp["data"]["viewer"]


def _linear_paginated(
    api_key: str,
    query: str,
    root_key: str,
    transform_node: Callable[[dict], dict],
) -> list[dict]:
    """Run a paginated Linear query and concatenate transformed nodes.

    Args:
        api_key: Linear API key.
        query: GraphQL query string expecting `$after: String` variable and
               returning a `<root_key>(first: 100, after: $after) { nodes ...,
               pageInfo { endCursor hasNextPage } }` shape.
        root_key: The top-level field name (e.g., "teams", "projects").
        transform_node: callable(node_dict) -> transformed_dict.

    Returns the concatenated list of transformed nodes across all pages.
    """
    all_nodes: list[dict] = []
    after: Optional[str] = None
    while True:
        resp = http_post_json(
            LINEAR_GRAPHQL_URL,
            {"query": query, "variables": {"after": after}},
            {"Authorization": api_key},
        )
        if "errors" in resp:
            raise PlanKeeperCliError(f"Linear API error: {resp['errors']}", code=5)
        section = resp["data"][root_key]
        all_nodes.extend(transform_node(n) for n in section["nodes"])
        page_info = section["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        after = page_info["endCursor"]
    return all_nodes


def linear_teams(api_key: str) -> list[dict]:
    query = (
        "query Teams($after: String) {"
        "  teams(first: 100, after: $after) {"
        "    nodes { id name }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )
    return _linear_paginated(api_key, query, "teams", lambda n: {"id": n["id"], "name": n["name"]})


def linear_projects(api_key: str) -> list[dict]:
    query = (
        "query Projects($after: String) {"
        "  projects(first: 100, after: $after) {"
        "    nodes { id name teams(first: 10) { nodes { id } } }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )
    return _linear_paginated(
        api_key,
        query,
        "projects",
        lambda n: {
            "id": n["id"],
            "name": n["name"],
            "teamIds": [t["id"] for t in n["teams"]["nodes"]],
        },
    )


def linear_labels(api_key: str) -> list[dict]:
    query = (
        "query Labels($after: String) {"
        "  issueLabels(first: 100, after: $after) {"
        "    nodes { id name team { id } }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )
    return _linear_paginated(
        api_key,
        query,
        "issueLabels",
        lambda n: {
            "id": n["id"],
            "name": n["name"],
            "teamId": n["team"]["id"] if n.get("team") else None,
        },
    )


def linear_users(api_key: str) -> list[dict]:
    query = (
        "query Users($after: String) {"
        "  users(first: 100, after: $after) {"
        "    nodes { id name email }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )
    return _linear_paginated(
        api_key,
        query,
        "users",
        lambda n: {"id": n["id"], "name": n["name"], "email": n["email"]},
    )


def refresh_linear_cache(api_key: str) -> list[str]:
    """Fetch all Linear metadata and write into config['linear']['cache'].

    Returns a list of warning strings (e.g., when defaults reference IDs
    that aren't in the new cache). Empty list on clean refresh.
    """
    teams = linear_teams(api_key)
    projects = linear_projects(api_key)
    labels = linear_labels(api_key)
    users = linear_users(api_key)
    repo = derive_repo(None)
    config = load_config(repo)
    section = config.setdefault("linear", {})
    section["cache"] = {
        "refreshedAt": _iso_utc_now(),
        "teams": teams,
        "projects": projects,
        "labels": labels,
        "users": users,
    }
    save_config(repo, config)
    # Check defaults for stale IDs.
    warnings: list[str] = []
    defaults = section.get("defaults", {})
    team_ids = {t["id"] for t in teams}
    if defaults.get("teamId") and defaults["teamId"] not in team_ids:
        warnings.append(
            f"defaults.teamId={defaults['teamId']!r} ({defaults.get('teamName', '?')!r}) "
            "no longer exists in Linear"
        )
    project_ids = {p["id"] for p in projects}
    if defaults.get("projectId") and defaults["projectId"] not in project_ids:
        warnings.append(
            f"defaults.projectId={defaults['projectId']!r} ({defaults.get('projectName', '?')!r}) "
            "no longer exists in Linear"
        )
    assignee_id = defaults.get("assigneeId")
    user_ids = {u["id"] for u in users}
    if assignee_id and assignee_id not in user_ids:
        warnings.append(
            f"defaults.assigneeId={assignee_id!r} ({defaults.get('assigneeName', '?')!r}) "
            "no longer exists in Linear"
        )
    label_ids_cached = {lbl["id"] for lbl in labels}
    for lbl_id in defaults.get("labelIds", []):
        if lbl_id not in label_ids_cached:
            warnings.append(f"defaults.labelIds contains {lbl_id!r} which is no longer in Linear")
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    return warnings


def refresh_jira_cache(site: str, email: str, api_token: str) -> list[str]:
    """Fetch all Jira metadata and write into config['jira']['cache'].

    Returns a list of warning strings (e.g., when defaults reference keys/IDs
    that aren't in the new cache). Empty list on clean refresh.
    """
    projects = jira_projects(site, email, api_token)
    all_components: list[dict] = []
    all_users: list[dict] = []
    all_issuetypes: list[dict] = []
    for p in projects:
        all_components.extend(jira_components(site, email, api_token, p["key"]))
        all_users.extend(jira_users(site, email, api_token, p["key"]))
        all_issuetypes.extend(jira_issuetypes(site, email, api_token, p["id"]))
    repo = derive_repo(None)
    config = load_config(repo)
    section = config.setdefault("jira", {})
    section["cache"] = {
        "refreshedAt": _iso_utc_now(),
        "projects": projects,
        "components": all_components,
        "users": all_users,
        "issueTypes": all_issuetypes,
    }
    save_config(repo, config)
    warnings: list[str] = []
    defaults = section.get("defaults", {})
    project_keys = {p["key"] for p in projects}
    if defaults.get("projectKey") and defaults["projectKey"] not in project_keys:
        warnings.append(
            f"defaults.projectKey={defaults['projectKey']!r} no longer exists in Jira"
        )
    component_ids = {c["id"] for c in all_components}
    for cid in defaults.get("componentIds", []):
        if cid not in component_ids:
            warnings.append(f"defaults.componentIds contains {cid!r} which is no longer in Jira")
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    return warnings


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


LINEAR_DESCRIPTION_LIMIT = 65_000


def _extract_h1(body: str) -> str:
    """Find the first H1 or H2 in a plan body. Returns the heading text only."""
    for raw_line in body.split("\n"):
        line = raw_line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.startswith("## "):
            return line[3:].strip()
    raise PlanKeeperCliError("plan has no H1 or H2 heading", code=2)


def _compose_description(repo_full: str, body: str) -> str:
    return f"Repo: {repo_full}\n\n{body.rstrip()}\n"


def linear_create_issue(api_key: str, input_dict: dict) -> dict:
    query = (
        "mutation IssueCreate($input: IssueCreateInput!) {"
        "  issueCreate(input: $input) {"
        "    success"
        "    issue { id identifier url title }"
        "  }"
        "}"
    )
    resp = http_post_json(
        LINEAR_GRAPHQL_URL,
        {"query": query, "variables": {"input": input_dict}},
        {"Authorization": api_key},
    )
    if "errors" in resp:
        raise PlanKeeperCliError(f"Linear API error: {resp['errors']}", code=5)
    payload = resp["data"]["issueCreate"]
    if not payload["success"]:
        raise PlanKeeperCliError("Linear API reported success=false", code=5)
    return payload["issue"]


def push_subcommand(name: str, file_path: str, force_new: bool = False) -> dict:
    """Create or update a ticket. Returns the result JSON.

    Called both from cmd_push (CLI entrypoint) and directly from tests.
    """
    path = Path(file_path)
    if not path.exists():
        raise PlanKeeperCliError(f"plan file not found: {path}", code=3)
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    title = _extract_h1(body)
    repo_full = derive_repo_full()
    description = _compose_description(repo_full, body)
    if len(description) > LINEAR_DESCRIPTION_LIMIT and name == "linear":
        raise PlanKeeperCliError(
            f"description is {len(description)} chars, exceeds Linear limit of 65000",
            code=2,
        )
    repo = derive_repo(None)
    config = load_config(repo)
    section = config.get(name)
    if section is None:
        raise PlanKeeperCliError(f"{name} not configured for repo {repo!r}", code=2)
    _validate_config_for_push(name, section)
    if name == "linear":
        return _push_linear(section, title, description, meta, force_new)
    elif name == "jira":
        return _push_jira(section, title, description, meta, force_new)
    raise PlanKeeperCliError(f"push to {name!r} not yet implemented", code=2)


def _validate_config_for_push(name: str, section: object) -> None:
    """Verify the config section has the fields push needs.

    Surfaces a friendly PlanKeeperCliError instead of letting a downstream
    KeyError leak as an internal stack trace. Each branch checks the minimum
    set of credentials + defaults that push will index into.

    `section` is typed as `object` because it comes from `json.loads` and
    could in principle be any JSON value (string/number/list) if the config
    file was hand-edited — the isinstance check below is meaningful.
    """
    if not isinstance(section, dict):
        raise PlanKeeperCliError(
            f"config section for {name!r} must be a JSON object", code=2,
        )
    defaults = section.get("defaults")
    if not isinstance(defaults, dict):
        raise PlanKeeperCliError(
            f"config section for {name!r} is missing 'defaults' — "
            f"run /plan-push setup to configure",
            code=2,
        )
    if name == "linear":
        if not section.get("apiKey"):
            raise PlanKeeperCliError(
                "linear config missing apiKey — run /plan-push setup", code=2,
            )
        if not defaults.get("teamId"):
            raise PlanKeeperCliError(
                "linear config defaults missing teamId — run /plan-push setup",
                code=2,
            )
    elif name == "jira":
        for field in ("site", "email", "apiToken"):
            if not section.get(field):
                raise PlanKeeperCliError(
                    f"jira config missing {field} — run /plan-push setup",
                    code=2,
                )
        if not defaults.get("projectKey"):
            raise PlanKeeperCliError(
                "jira config defaults missing projectKey — run /plan-push setup",
                code=2,
            )


def _push_linear(section: dict, title: str, description: str, meta: dict, force_new: bool) -> dict:
    api_key = section["apiKey"]
    defaults = section["defaults"]
    has_existing = bool(meta.get("Ticket")) and meta.get("Ticket System") == "linear"
    if has_existing and not force_new:
        # update path — implemented in Task 10
        return _push_linear_update(api_key, meta["Ticket"], title, description)
    input_dict = {
        "title": title,
        "description": description,
        "teamId": defaults["teamId"],
    }
    if defaults.get("projectId"):
        input_dict["projectId"] = defaults["projectId"]
    if defaults.get("assigneeId"):
        input_dict["assigneeId"] = defaults["assigneeId"]
    if defaults.get("labelIds"):
        input_dict["labelIds"] = list(defaults["labelIds"])
    issue = linear_create_issue(api_key, input_dict)
    return {
        "action": "create",
        "system": "linear",
        "id": issue["identifier"],
        "url": issue["url"],
        "title": issue["title"],
    }


def _push_linear_update(api_key: str, identifier: str, title: str, description: str) -> dict:
    query = (
        "mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {"
        "  issueUpdate(id: $id, input: $input) {"
        "    success"
        "    issue { id identifier url title }"
        "  }"
        "}"
    )
    resp = http_post_json(
        LINEAR_GRAPHQL_URL,
        {
            "query": query,
            "variables": {
                "id": identifier,  # Linear accepts identifier as id
                "input": {"title": title, "description": description},
            },
        },
        {"Authorization": api_key},
    )
    if "errors" in resp:
        # 404-style errors come back as a GraphQL error with code EntityNotFound.
        for err in resp.get("errors", []):
            ext = err.get("extensions", {})
            if ext.get("code") == "EntityNotFound" or "not found" in err.get("message", "").lower():
                raise PlanKeeperCliError(f"Linear ticket {identifier} not found", code=5)
        raise PlanKeeperCliError(f"Linear API error: {resp['errors']}", code=5)
    payload = resp["data"]["issueUpdate"]
    if not payload["success"]:
        raise PlanKeeperCliError("Linear API reported success=false", code=5)
    issue = payload["issue"]
    return {
        "action": "update",
        "system": "linear",
        "id": issue["identifier"],
        "url": issue["url"],
        "title": issue["title"],
    }


# --- Jira helpers -----------------------------------------------------------


_JIRA_SITE_RE = re.compile(r"^[A-Za-z0-9.\-]+(?::\d+)?$")


def _validate_jira_site(site: str) -> str:
    """Reject anything that isn't a bare hostname (optionally with :port).

    Inputs like `https://herds.atlassian.net`, `herds.atlassian.net/path`,
    or `herds.atlassian.net@evil.test` would break URL construction or
    redirect the Basic-auth header to the wrong host. Accept only
    `<host>` or `<host>:<port>`.
    """
    if not site or not isinstance(site, str):
        raise PlanKeeperCliError("jira site must be a non-empty string", code=2)
    if not _JIRA_SITE_RE.match(site):
        raise PlanKeeperCliError(
            f"jira site must be a bare hostname (no scheme, path, userinfo, or whitespace); got {site!r}",
            code=2,
        )
    return site


def _jira_auth_header(email: str, api_token: str) -> str:
    raw = f"{email}:{api_token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def jira_viewer(site: str, email: str, api_token: str) -> dict:
    url = f"https://{site}/rest/api/3/myself"
    return http_get_json(url, {"Authorization": _jira_auth_header(email, api_token)})


def _jira_paginated(
    site: str, email: str, api_token: str,
    path: str, query_params: dict[str, str],
) -> list[dict]:
    """Walk Jira's startAt/maxResults pagination and return all `values`."""
    all_values: list[dict] = []
    start_at = 0
    page_size = 50
    while True:
        params = {**query_params, "startAt": str(start_at), "maxResults": str(page_size)}
        url = f"https://{site}/rest/api/3/{path}?{urlencode(params)}"
        resp = http_get_json(url, {"Authorization": _jira_auth_header(email, api_token)})
        page_values = resp.get("values", [])
        all_values.extend(page_values)
        if "isLast" in resp:
            if resp["isLast"]:
                break
        elif len(page_values) < page_size:
            break
        start_at += page_size
    return all_values


def jira_projects(site: str, email: str, api_token: str) -> list[dict]:
    raw = _jira_paginated(site, email, api_token, "project/search", {})
    return [{"key": p["key"], "id": p["id"], "name": p["name"]} for p in raw]


def jira_components(site: str, email: str, api_token: str, project_key: str) -> list[dict]:
    url = f"https://{site}/rest/api/3/project/{project_key}/components"
    raw = http_get_json(url, {"Authorization": _jira_auth_header(email, api_token)})
    # Endpoint returns a flat list, not a paginated object.
    if not isinstance(raw, list):
        raise PlanKeeperCliError(f"unexpected Jira components response: {raw!r}", code=5)
    return [{"id": c["id"], "name": c["name"], "projectKey": project_key} for c in raw]


def jira_users(site: str, email: str, api_token: str, project_key: str) -> list[dict]:
    all_users: list[dict] = []
    start_at = 0
    page_size = 50
    while True:
        url = (
            f"https://{site}/rest/api/3/user/assignable/multiProjectSearch?"
            + urlencode({
                "projectKeys": project_key,
                "startAt": str(start_at),
                "maxResults": str(page_size),
            })
        )
        raw = http_get_json(url, {"Authorization": _jira_auth_header(email, api_token)})
        if not isinstance(raw, list):
            raise PlanKeeperCliError(f"unexpected Jira users response: {raw!r}", code=5)
        all_users.extend(raw)
        if len(raw) < page_size:
            break
        start_at += page_size
    return [
        {"accountId": u["accountId"], "name": u["displayName"],
         "email": u.get("emailAddress", "")}
        for u in all_users
    ]


def jira_issuetypes(site: str, email: str, api_token: str, project_id: str) -> list[dict]:
    url = (
        f"https://{site}/rest/api/3/issuetype/project?"
        + urlencode({"projectId": project_id})
    )
    raw = http_get_json(url, {"Authorization": _jira_auth_header(email, api_token)})
    if not isinstance(raw, list):
        raise PlanKeeperCliError(f"unexpected Jira issuetypes response: {raw!r}", code=5)
    return [{"id": t["id"], "name": t["name"], "projectId": project_id} for t in raw]


def _adf_paragraph(text: str) -> dict:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        ],
    }


def jira_create_issue(
    site: str, email: str, api_token: str, fields: dict,
) -> dict:
    url = f"https://{site}/rest/api/3/issue"
    req = urllib.request.Request(
        url,
        data=json.dumps({"fields": fields}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": _jira_auth_header(email, api_token),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PlanKeeperCliError(f"Jira auth failure ({e.code})", code=3)
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        raise PlanKeeperCliError(f"Jira HTTP {e.code}: {body[:200]}", code=5)
    except urllib.error.URLError as e:
        raise PlanKeeperCliError(f"Jira network error: {e.reason}", code=4)


def jira_update_issue(
    site: str, email: str, api_token: str, key: str, fields: dict,
) -> None:
    url = f"https://{site}/rest/api/3/issue/{key}"
    req = urllib.request.Request(
        url,
        data=json.dumps({"fields": fields}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": _jira_auth_header(email, api_token),
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT):
            return  # 204 No Content on success
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PlanKeeperCliError(f"Jira auth failure ({e.code})", code=3)
        if e.code == 404:
            raise PlanKeeperCliError(f"Jira ticket {key} not found", code=5)
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        raise PlanKeeperCliError(f"Jira HTTP {e.code}: {body[:200]}", code=5)
    except urllib.error.URLError as e:
        raise PlanKeeperCliError(f"Jira network error: {e.reason}", code=4)


def _push_jira(section: dict, title: str, description: str, meta: dict, force_new: bool) -> dict:
    site = _validate_jira_site(section["site"])
    email = section["email"]
    token = section["apiToken"]
    defaults = section["defaults"]
    has_existing = bool(meta.get("Ticket")) and meta.get("Ticket System") == "jira"
    adf = _adf_paragraph(description)
    if has_existing and not force_new:
        key = meta["Ticket"]
        jira_update_issue(
            site, email, token, key,
            {"summary": title, "description": adf},
        )
        return {
            "action": "update",
            "system": "jira",
            "id": key,
            "url": f"https://{site}/browse/{key}",
            "title": title,
        }
    fields = {
        "project": {"key": defaults["projectKey"]},
        "summary": title,
        "description": adf,
        "issuetype": {"name": defaults.get("issueType", "Task")},
    }
    if defaults.get("componentIds"):
        fields["components"] = [{"id": cid} for cid in defaults["componentIds"]]
    if defaults.get("assigneeAccountId"):
        fields["assignee"] = {"accountId": defaults["assigneeAccountId"]}
    if defaults.get("labels"):
        fields["labels"] = list(defaults["labels"])
    created = jira_create_issue(site, email, token, fields)
    key = created["key"]
    return {
        "action": "create",
        "system": "jira",
        "id": key,
        "url": f"https://{site}/browse/{key}",
        "title": title,
    }


def cmd_push(args) -> int:
    result = push_subcommand(args.name, args.file, force_new=args.force_new)
    print(json.dumps(result))
    return 0


def _resolve_jira_project_id(
    site: str, email: str, api_token: str, project_key: str,
) -> str:
    """Look up the numeric Jira project id for a given key.

    `jira_issuetypes` calls `/issuetype/project?projectId=<id>` which requires
    the numeric id, not the human-readable key. Other per-project endpoints
    (`jira_components`, `jira_users`) accept the key directly. This helper
    bridges the gap by fetching projects and matching on key.
    """
    for p in jira_projects(site, email, api_token):
        if p["key"] == project_key:
            return p["id"]
    raise PlanKeeperCliError(
        f"Jira project key {project_key!r} not found", code=2,
    )


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


GROUNDCREW_TICKET_SYSTEM = "groundcrew"


def _stamp_groundcrew_ticket(path: Path, ticket: str) -> None:
    """Mirror the synthesized id into the plan's `Ticket` / `Ticket System`
    frontmatter (the same pair plan-push uses), so a human can see which plan
    a ``plan-<n>`` id maps to.

    Display-only and self-healing: ``groundcrew_id()`` stays the canonical id,
    so ``resolve-one`` never trusts these fields — it recomputes the hash. The
    stamp only *claims* the pair when it's empty or already ``groundcrew``;
    a ``linear``/``jira`` reference (written by plan-push) is left untouched,
    so a pushed plan keeps showing its real tracker ticket and still
    dispatches via the recomputed id. Rewrites only when absent or stale, so
    steady-state fetches don't churn the file. Best-effort: a read/parse error
    is swallowed so one unwritable file can't abort the whole fetch.
    """
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, PlanKeeperCliError):
        return
    system = (meta.get("Ticket System") or "").strip().lower()
    if system == GROUNDCREW_TICKET_SYSTEM:
        if meta.get("Ticket") == ticket:
            return  # already current
    elif system or meta.get("Ticket"):
        return  # another tracker (or an orphan Ticket) owns these fields
    meta["Ticket"] = ticket
    meta["Ticket System"] = GROUNDCREW_TICKET_SYSTEM
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
    parent = path.parent
    if parent.name in {"done", "deferred"}:
        # Plan is archived/paused — the repo dir is the grandparent. Without
        # this, `groundcrew-resolve-one` would report repository="done" for
        # any plan it found in ~/plans/<repo>/done/, breaking groundcrew's
        # `workspace.knownRepositories` lookup.
        repo_name = parent.parent.name
    else:
        repo_name = parent.name
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


def cmd_groundcrew_fetch(args) -> int:
    """Emit a JSON array of issues for groundcrew's shell adapter to consume.

    Scans ~/plans/*/*.md (one level deep — skips done/ and deferred/).
    """
    del args
    issues: list[dict] = []
    if not PLAN_ROOT.exists():
        print("[]")
        return 0
    for repo_entry in sorted(PLAN_ROOT.iterdir()):
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


def cmd_groundcrew_resolve_one(args) -> int:
    """Print one issue JSON for `${id}`, exit 3 if not found."""
    if not _GROUNDCREW_ID_RE.match(args.id):
        raise PlanKeeperCliError(
            f"invalid id {args.id!r}: must match [A-Za-z0-9._-]+ (no path separators)",
            code=2,
        )
    if not PLAN_ROOT.exists():
        return 3
    # The id is synthesized (see groundcrew_id), so we can't map it straight
    # to a filename — instead recompute each plan's id and match. Search
    # active plans first, then done/, then deferred/, so a live plan wins
    # over an archived plan that shares its stem (same synthesized id).
    for repo_entry in PLAN_ROOT.iterdir():
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


def cmd_groundcrew_mark_in_progress(args) -> int:
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
    plan_root = PLAN_ROOT.resolve()
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

    Path validation mirrors groundcrew-mark-in-progress: every path must be
    absolute, end in .md, resolve inside PLAN_ROOT, exist, and have
    frontmatter. The whole batch is validated FIRST — if any path is
    invalid, nothing is written (all-or-nothing), so a typo can't leave the
    queue half-mutated.
    """
    raw = sys.stdin.read()
    paths = [line.strip() for line in raw.splitlines() if line.strip()]
    if not paths:
        raise PlanKeeperCliError("queue set: no plan paths on stdin", code=2)
    plan_root = PLAN_ROOT.resolve()
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
        if (
            args.status == "todo"
            and args.default_agent
            and not meta.get("Agent", "").strip()
        ):
            meta["Agent"] = args.default_agent
        new_text = serialize_frontmatter(meta, body)
        if not new_text.endswith("\n"):
            new_text += "\n"
        write_atomic(resolved, new_text)
        print(resolved)
    return 0


def cmd_queue_list(args) -> int:
    """Emit a JSON array of active plans across all repos, for plan-queue.

    Each element is {repo, file, status, agent} where status/agent are the
    raw frontmatter values ("" when unset). Scans ~/plans/<repo>/*.md one
    level deep — skips done/ and deferred/ (those are not dispatchable) and
    skips files without frontmatter (not plan-keeper plans). This is the
    read side of the groundcrew queue the plan-queue skill renders.
    """
    del args
    rows: list[dict] = []
    if not PLAN_ROOT.exists():
        print("[]")
        return 0
    for repo_entry in sorted(PLAN_ROOT.iterdir()):
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
        prog="plan_keeper_cli",
        description="I/O backend for the plan-keeper skills.",
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

    p_list = sub.add_parser("list", help="list plans for a repo, newest-first")
    p_list.add_argument("--override", help="explicit override for <repo>")
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
            "first within each. Output becomes 'status<TAB>filename'. "
            "Missing/blank Status counts as 'backlog'. Excluded active plans "
            "are summarized on stderr. Omit to list bare filenames as before."
        ),
    )

    sub.add_parser(
        "list-repos",
        help="list all repos under ~/plans/ with per-state counts",
    )

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
             "for --from-path (set Kind afterward via `file-meta update`). "
             "Fill-if-absent: a Kind already in the body is preserved.",
    )
    p_save.add_argument(
        "--on-collision",
        choices=["fail", "suffix", "overwrite"],
        default="fail",
        help="what to do if the target file exists "
        "(default: fail with exit 2; use suffix for next unused -N)",
    )

    p_archive = sub.add_parser(
        "archive",
        help="append a completion stamp and move ~/plans/<repo>/<file> to done/",
    )
    p_archive.add_argument("--override", help="explicit override for <repo>")
    p_archive.add_argument(
        "--file",
        required=True,
        help="filename (basename only, must live in ~/plans/<repo>/)",
    )
    p_archive.add_argument(
        "--on-collision",
        choices=["fail", "suffix", "overwrite"],
        default="fail",
        help="what to do if a same-name file exists in done/ (default: fail)",
    )
    p_archive.add_argument(
        "--completed-date",
        help="YYYY-MM-DD date for the completion stamp (default: today)",
    )

    p_file_meta = sub.add_parser("file-meta", help="read/write/strip plan-file frontmatter")
    file_meta_sub = p_file_meta.add_subparsers(
        dest="file_meta_cmd",
        required=True,
        metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )

    p_fm_get = file_meta_sub.add_parser("get", help="print frontmatter as JSON")
    p_fm_get.add_argument("--file", required=True, help="path to a plan .md file")

    p_fm_set = file_meta_sub.add_parser("set", help="write or update plan frontmatter")
    p_fm_set.add_argument("--file", required=True)
    p_fm_set.add_argument("--ticket", help="ticket identifier (e.g., ENG-123)")
    p_fm_set.add_argument("--ticket-system", choices=["linear", "jira"], help="ticket system")
    p_fm_set.add_argument("--completed-on", help="completion date YYYY-MM-DD")

    p_fm_strip = file_meta_sub.add_parser("strip", help="print body without frontmatter")
    p_fm_strip.add_argument("--file", required=True)

    p_fm_update = file_meta_sub.add_parser(
        "update",
        help="apply --field Key=value updates to plan frontmatter "
             "(any whitelisted field)",
    )
    p_fm_update.add_argument("--file", required=True, help="path to a plan file")
    p_fm_update.add_argument(
        "--field",
        action="append",
        metavar="Key=value",
        help="frontmatter field to set (repeat for multiple fields); "
             "Key must be one of: " + ", ".join(_FRONTMATTER_FIELDS),
    )

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
    p_push.add_argument("--file", required=True)
    p_push.add_argument(
        "--force-new",
        action="store_true",
        help="ignore existing Ticket frontmatter and create a fresh ticket",
    )

    sub.add_parser(
        "groundcrew-fetch",
        help="emit shell-adapter JSON array of active plans (for crew.config.ts fetch)",
    )

    p_gc_one = sub.add_parser(
        "groundcrew-resolve-one",
        help="emit one shell-adapter issue JSON for ${id}, or exit 3 if missing",
    )
    p_gc_one.add_argument("id", help="synthesized plan id (plan-<digits>, from fetch)")

    sub.add_parser(
        "groundcrew-mark-in-progress",
        help="flip Status to in-progress on a plan named by stdin sourceRef JSON",
    )

    p_queue = sub.add_parser(
        "queue",
        help="cross-repo groundcrew queue: list active plans / set Status in bulk",
    )
    queue_sub = p_queue.add_subparsers(
        dest="queue_cmd", required=True, metavar="<subcommand>",
        parser_class=HelpfulArgumentParser,
    )

    _ = queue_sub.add_parser(
        "list",
        help="emit JSON array of active plans across all repos "
             "(repo/file/status/agent)",
    )

    p_queue_set = queue_sub.add_parser(
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
    "update": cmd_file_meta_update,
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


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "repo": cmd_repo,
        "list": cmd_list,
        "list-repos": cmd_list_repos,
        "save": cmd_save,
        "archive": cmd_archive,
        "file-meta": lambda a: _FILE_META_DISPATCH[a.file_meta_cmd](a),
        "ticket-system-config": lambda a: _TICKET_SYSTEM_CONFIG_DISPATCH[a.tsc_cmd](a),
        "ticket-api": cmd_ticket_api,
        "push": cmd_push,
        "groundcrew-fetch": cmd_groundcrew_fetch,
        "groundcrew-resolve-one": cmd_groundcrew_resolve_one,
        "groundcrew-mark-in-progress": cmd_groundcrew_mark_in_progress,
        "queue": lambda a: _QUEUE_DISPATCH[a.queue_cmd](a),
    }
    try:
        return dispatch[args.cmd](args)
    except PlanKeeperCliError as e:
        print(f"plan_keeper_cli: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
