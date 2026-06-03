"""Repo-name derivation and the slugify / name / extension validation rules.

The algorithm for repo derivation lives in
``plugins/plan-keeper/repo-derivation.md`` and is implemented here.
"""
import os
import re
import subprocess
from typing import Optional

from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.storage import MAX_SLUG_LEN


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


def _repo_from_git(cwd: Optional[str] = None) -> Optional[str]:
    """Return the repo folder name from `git remote origin`, or None.

    The git half of derive_repo, split out so callers that need to distinguish
    "there is a repo context here" from "fall back to something" can do so —
    `list` treats a None here as "no repo context, list every repo". Returns the
    validated single-token name (origin URL basename, `.git` stripped); None when
    cwd is not a git repo, has no `origin`, or the URL has no usable basename.
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
            if url:
                base = os.path.basename(url)
                if base.endswith(".git"):
                    base = base[:-4]
                if base:
                    return validate_repo_name(base)
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def derive_repo(override: Optional[str], cwd: Optional[str] = None) -> str:
    """Resolve <repo> per repo-derivation.md.

    Contract unchanged: override (normalized) → git origin → cwd basename. The
    git lookup is delegated to _repo_from_git; this still always returns exactly
    one repo name (the cwd-basename fallback guarantees a value).
    """
    if override:
        return validate_repo_name(normalize_override(override))
    from_git = _repo_from_git(cwd)
    if from_git is not None:
        return from_git
    cwd = cwd or os.getcwd()
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
