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
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Optional


PLAN_ROOT = Path.home() / "plans"
MAX_SLUG_LEN = 50
MAX_SUFFIX = 99


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
    """Return sorted plans for a repo in a given state, newest-first."""
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
    files = [p for p in d.iterdir() if p.is_file() and p.suffix == ".md"]
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
    for entry in sorted(PLAN_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        active = sum(
            1 for p in entry.iterdir() if p.is_file() and p.suffix == ".md"
        )
        done_dir = entry / "done"
        done = (
            sum(1 for p in done_dir.iterdir() if p.is_file() and p.suffix == ".md")
            if done_dir.exists()
            else 0
        )
        deferred_dir = entry / "deferred"
        deferred = (
            sum(
                1
                for p in deferred_dir.iterdir()
                if p.is_file() and p.suffix == ".md"
            )
            if deferred_dir.exists()
            else 0
        )
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
    slug = slugify_topic(args.topic)
    if not slug:
        raise PlanKeeperCliError(
            f"topic {args.topic!r} slugified to empty string", code=2
        )
    date_str = parse_date_arg(args.date) if args.date else date.today().isoformat()
    target = repo_dir(repo) / f"{date_str}-{slug}.md"

    if target.exists():
        if args.on_collision == "fail":
            emit_collision(target)
            return 2
        if args.on_collision == "suffix":
            target = find_unused_suffix(target)
        # "overwrite" → fall through

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
    body = source.read_text(encoding="utf-8")
    if not body.endswith("\n"):
        body += "\n"
    stamped = body + f"\n---\n*Completed: {completed}*\n"

    write_atomic(target, stamped)
    source.unlink()
    print(target)
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


def cmd_file_meta_get(args) -> int:
    path = Path(args.file)
    if not path.exists():
        raise PlanKeeperCliError(f"plan file not found: {path}", code=3)
    text = path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(text)
    print(json.dumps(meta))
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
        help="write plan body (stdin) to ~/plans/<repo>/<date>-<slug>.md",
    )
    p_save.add_argument("--override", help="explicit override for <repo>")
    p_save.add_argument(
        "--topic", required=True, help="topic string (will be slugified)"
    )
    p_save.add_argument(
        "--date",
        help="YYYY-MM-DD date prefix (default: today)",
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

    return parser


# Sub-dispatch table for `file-meta <get|set|strip>`. Each entry handles one
# `file_meta_cmd`. Kept as a module-level constant so tasks adding `set` and
# `strip` only need to add one line here, not edit a lambda body in main().
_FILE_META_DISPATCH = {
    "get": cmd_file_meta_get,
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
    }
    try:
        return dispatch[args.cmd](args)
    except PlanKeeperCliError as e:
        print(f"plan_keeper_cli: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
