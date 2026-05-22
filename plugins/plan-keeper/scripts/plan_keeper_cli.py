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
    for p in list_plans(repo, args.state):
        print(p.name)
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
    if args.from_path:
        for flag, value in (
            ("--topic", args.topic),
            ("--extension", args.extension),
            ("--date", args.date),
        ):
            if value is not None:
                raise PlanKeeperCliError(
                    f"{flag} is incompatible with --from-path "
                    "(--from-path preserves the source basename verbatim); "
                    f"drop {flag} or drop --from-path",
                    code=2,
                )
        source = Path(args.from_path)
        if not source.exists():
            raise PlanKeeperCliError(f"source not found: {source}", code=3)
        if not source.is_file():
            raise PlanKeeperCliError(f"source is not a file: {source}", code=3)
        target = repo_dir(repo) / source.name
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
_FRONTMATTER_FIELDS = ("Ticket", "Ticket System", "Completed on")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a plan file into (frontmatter_dict, body_text).

    Frontmatter is the optional top block delimited by `---` lines. Each
    inner line is "Key: value" (whitespace around the colon ignored).

    Returns:
        (meta, body) where meta ALWAYS has exactly the fields in
        _FRONTMATTER_FIELDS (empty string when a field is absent or when
        the file has no frontmatter at all), and body is the text after
        the closing `---` (or all of `text` if no frontmatter).

    Raises:
        PlanKeeperCliError(code=5) on malformed frontmatter (no closing `---`,
        unrecognized field, missing colon).
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
        key = key.strip()
        value = value.strip()
        if key not in _FRONTMATTER_FIELDS:
            raise PlanKeeperCliError(
                f"malformed frontmatter: unknown field {key!r}", code=5
            )
        meta[key] = value
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
    was never set stays out of the file entirely). Field order matches
    _FRONTMATTER_FIELDS.

    If meta has all-empty values, returns body unchanged (no frontmatter
    block written). This preserves the "bare plan has no `---`" invariant.
    """
    non_empty = [(k, v) for k in _FRONTMATTER_FIELDS for v in [meta.get(k, "")] if v]
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
    }
    try:
        return dispatch[args.cmd](args)
    except PlanKeeperCliError as e:
        print(f"plan_keeper_cli: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
