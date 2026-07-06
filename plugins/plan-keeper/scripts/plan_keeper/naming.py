"""Repo-name derivation and the slugify / name / extension validation rules.

The algorithm for repo derivation lives in
``plugins/plan-keeper/repo-derivation.md`` and is implemented here.
"""
import os
import re
import subprocess
import sys
from typing import Optional

from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.frontmatter import VALID_KINDS
from plan_keeper.storage import MAX_SLUG_LEN
from plan_keeper.types import Kind, RepoAlias


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


# The Kind separator. `slugify_topic` collapses every run of `-` (and every
# disallowed char) to a single `-`, and the date prefix is single-hyphen, so
# `--` can never occur inside a date or slug. That makes it the one
# unambiguous Kind boundary in a plan filename — `plan_group_key` recovers the
# project slug with a single rpartition, no enum-stripping guesswork.
KIND_SEP = "--"

_NAME_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
# A `-N` collision suffix that `find_unused_suffix` appends after the Kind.
_NAME_COLLISION_SUFFIX_RE = re.compile(r"-\d+$")


def plan_filename(date_str: str, slug: str, ext: str, kind: Optional[Kind]) -> str:
    """Build a plan's filename.

    `<date>-<slug>--<kind>.<ext>` on a markdown save that carries a Kind, else
    `<date>-<slug>.<ext>`. Single source of truth for the on-disk name shape;
    `cmd_save` builds every target through here. Non-markdown saves never get a
    Kind suffix (frontmatter — and therefore Kind — lives only in markdown).
    """
    base = f"{date_str}-{slug}"
    if kind and ext == "md":
        base = f"{base}{KIND_SEP}{kind}"
    return f"{base}.{ext}"


def plan_group_key(name: str) -> str:
    """Recover the project slug (grouping key) from a plan filename.

    Inverse of `plan_filename`: strips the leading `YYYY-MM-DD-` date prefix,
    the extension, and a trailing `--<kind>` segment when that segment is a
    valid Kind. Files with no date prefix fall back to their stem. The grouped
    listing clusters by this key so the stages of one project (which share a
    slug) appear together.
    """
    stem = name.rsplit(".", 1)[0] if "." in name else name
    m = _NAME_DATE_PREFIX_RE.match(stem)
    rest = stem[m.end():] if m else stem
    head, sep, tail = rest.rpartition(KIND_SEP)
    # `tail` is the trailing Kind. A same-kind/same-day/same-topic re-save makes
    # `find_unused_suffix` append `-N` to the whole stem, so the on-disk form is
    # `…--<kind>-N`; strip that numeric collision suffix before the Kind check so
    # a copy still groups with its original rather than as its own project. Gate
    # on `m` (a real date prefix): the `--<kind>` recovery applies only to dated
    # plan filenames, so a hand-named no-date `README--spec.md` falls back to its
    # whole stem rather than being read as a `spec` stage of project `README`.
    if m and sep and _NAME_COLLISION_SUFFIX_RE.sub("", tail) in VALID_KINDS:
        return head
    return rest


def rename_for_kind(name: str, new_kind: Kind) -> str:
    """Re-stamp a plan filename's `--<kind>` segment for a Kind change.

    Inverse of `plan_group_key`'s `--<kind>[-N]` recovery: strip a trailing
    `--<valid-kind>` segment (including any `-N` collision suffix), then append
    `--<new_kind>`. The result is always the canonical
    `<date>-<slug>--<new_kind>.<ext>` shape; any collision suffix is dropped so
    the caller (`cmd_file_meta_set`) re-resolves collisions against the new name
    via its `--on-collision` policy. A dated name with no `--<kind>` segment
    (saved without `--kind`) simply gains one. Gated on a real date prefix,
    matching `plan_group_key`: only dated plan names carry a meaningful Kind
    segment, so a hand-named no-date file (`plan.md`, `README.md`) is returned
    unchanged rather than gaining a segment the grouping logic would ignore.
    """
    stem, dotext = os.path.splitext(name)
    m = _NAME_DATE_PREFIX_RE.match(stem)
    if not m:
        return name
    rest = stem[m.end():]
    head, sep, tail = rest.rpartition(KIND_SEP)
    if sep and _NAME_COLLISION_SUFFIX_RE.sub("", tail) in VALID_KINDS:
        stem = stem[: m.end()] + head
    return f"{stem}{KIND_SEP}{new_kind}{dotext}"


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
    components and skip past `~/plans/`. Control whitespace (\\t / \\n /
    \\r) would silently corrupt the TSV contract that ``repo alias list``
    prints (`remote\\tsubpath\\tname` — a tab or newline inside `name`
    duplicates fields or rows from a downstream consumer's view).
    """
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise PlanKeeperCliError(f"invalid repo name: {name!r}", code=2)
    if any(ch in name for ch in ("\t", "\n", "\r")):
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


# Ambient-state dependency: the repo name resolved by _repo_from_git /
# derive_repo — and therefore which ~/plans/<repo>/ dir and which
# .plankeeper.json config get read and written — depends on os.getcwd() and the
# git remote origin, resolved via subprocess. With no explicit `cwd`, the result
# is a function of the process's current working directory.
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


def _git_toplevel(cwd: Optional[str] = None) -> Optional[str]:
    """Return the absolute path of the git toplevel for `cwd`, or None.

    Used by alias resolution to compute the subpath from the monorepo root to
    `cwd`. None when `cwd` is not in a git repo, mirroring `_repo_from_git`'s
    swallow-everything failure mode.
    """
    cwd = cwd or os.getcwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            top = result.stdout.strip()
            if top:
                return top
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def _subpath_from_toplevel(toplevel: str, cwd: str) -> Optional[str]:
    """Return the relative path from `toplevel` to `cwd`, or None if outside.

    The empty string is a legitimate return value (cwd IS the toplevel) — only
    a cwd that resolves outside the toplevel returns None. Uses real paths so a
    symlinked or `..`-laced cwd still aligns correctly.
    """
    try:
        rel = os.path.relpath(os.path.realpath(cwd), os.path.realpath(toplevel))
    except ValueError:
        return None
    if rel == ".":
        return ""
    if rel.startswith(".."):
        return None
    return rel


def _alias_prefixes(subpath: str) -> list[str]:
    """All path-segment-aligned prefixes of `subpath`, longest first.

    Includes the empty string at the end so a repo-root alias (subpath="")
    matches as the last resort. Path-segment alignment is the load-bearing
    property — `catalog/flawless-inventory` is NOT a prefix of
    `catalog/flawless-inventory-archive` (sibling) but IS a prefix of
    `catalog/flawless-inventory/sub` (child).
    """
    if not subpath:
        return [""]
    parts = subpath.split("/")
    return ["/".join(parts[:n]) for n in range(len(parts), 0, -1)] + [""]


def _resolve_alias(
    aliases: list[RepoAlias], remote: str, subpath: str,
) -> Optional[str]:
    """Walk subpath prefixes longest-first; return the first matching alias name.

    The (remote, subpath) tuple is the join key — both must match exactly. Two
    aliases with the same prefix but different remotes do not interfere.
    """
    for prefix in _alias_prefixes(subpath):
        for entry in aliases:
            if entry.get("remote") == remote and entry.get("subpath") == prefix:
                return entry.get("name")
    return None


def derive_repo(override: Optional[str], cwd: Optional[str] = None) -> str:
    """Resolve <repo> per repo-derivation.md.

    Contract: override (normalized) → alias mapping → git origin → cwd basename.
    The alias step (added with the monorepo-subpath mapping) sits between the
    git lookup and its bare result so every consumer that already calls
    `repo name` picks up alias resolution transparently.
    """
    if override:
        return validate_repo_name(normalize_override(override))
    from_git = _repo_from_git(cwd)
    if from_git is not None:
        aliased = _maybe_alias(from_git, cwd)
        if aliased is not None:
            return aliased
        return from_git
    cwd = cwd or os.getcwd()
    return validate_repo_name(os.path.basename(os.path.abspath(cwd)))


def _maybe_alias(remote: str, cwd: Optional[str]) -> Optional[str]:
    """Resolve a monorepo-subpath alias for (remote, cwd), or None.

    Returns the alias name (validated) when the global config maps the
    `(remote, subpath)` tuple. None for every miss — no global config, empty
    aliases list, cwd outside the git toplevel, no matching prefix. Imported
    lazily so a `naming.py` consumer that doesn't need alias resolution
    (e.g. a unit test stubbing `_repo_from_git`) doesn't pay the cost.
    """
    # Local import: global_config -> storage -> frontmatter. Importing at
    # module top would form a wider dependency surface for callers that never
    # touch the global config.
    from plan_keeper.global_config import load_global_config

    toplevel = _git_toplevel(cwd)
    if toplevel is None:
        return None
    subpath = _subpath_from_toplevel(toplevel, cwd or os.getcwd())
    if subpath is None:
        return None
    try:
        config = load_global_config()
    except PlanKeeperCliError as e:
        # derive_repo's contract is to always return a name (so `plan-save`
        # doesn't crash on a corrupted config). But silently routing plans to
        # the bare remote — the documented prior-incident class from CLAUDE.md
        # ("missing structural comma survived 19 iterations") — is exactly the
        # failure mode this codebase is allergic to. A one-line stderr warning
        # preserves the contract while making the corruption visible on the
        # very next `plan-save` / `plan-do` / dispatch.
        print(
            f"warning: ignoring malformed global config ({e}); "
            "falling back to bare remote",
            file=sys.stderr,
        )
        return None
    aliases = config.get("aliases") or []
    if not aliases:
        return None
    name = _resolve_alias(aliases, remote, subpath)
    if name is None:
        return None
    try:
        return validate_repo_name(name)
    except PlanKeeperCliError as e:
        # A shape-valid config can still resolve to an alias name that
        # fails validate_repo_name (e.g., "../oops" or "team/app" written
        # in by hand). Treat that as the same failure class as a malformed
        # config: warn on stderr and fall back to the bare remote so a
        # single bad row doesn't poison every command that touches
        # derive_repo. See the malformed-config branch above.
        print(
            f"warning: ignoring invalid alias name in global config ({e}); "
            "falling back to bare remote",
            file=sys.stderr,
        )
        return None


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
