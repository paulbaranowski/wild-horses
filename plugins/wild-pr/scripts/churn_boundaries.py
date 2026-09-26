#!/usr/bin/env python3
"""churn_boundaries.py - find the components a repo talks to across a boundary.

The churn skill's placement lens reads this list. It asks whether the behavior
a review cluster keeps fixing belongs in another component: a backend, a
frontend, an external API, or a library.

Discovery reads only local files: the repo's agent docs, its dependency
manifests, env var names, HTTP client base URLs, and API schema files. It
never makes a network call, and it never reads a real `.env` file.

Every entry has the keys in FIELDS. Entries for one component merge, and the
list is sorted, so two runs over one tree give byte-identical output.
"""

import os
import re
import subprocess
from pathlib import Path

FIELDS = ("name", "kind", "direction", "local_path", "gh_slug", "doc_url", "describes", "sources")

# Files larger than this are skipped: bundles, lockfiles, and data dumps.
MAX_FILE_BYTES = 1_000_000

DOC_FILES = ("CLAUDE.md", "AGENTS.md", "ARCHITECTURE.md")
GITHUB_LINK = re.compile(r"github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)")
GITHUB_REMOTE = re.compile(r"github\.com[:/]([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")
# GitHub path segments that name a site page, not an owner.
GITHUB_NON_OWNERS = {"about", "apps", "features", "login", "marketplace", "orgs",
                     "settings", "sponsors", "topics"}
HOME_PATH = re.compile(r"~/((?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+)")
HEADING = re.compile(r"^#{1,6}\s")
LIST_ITEM = re.compile(r"^\s{0,3}(?:[-*+]|[0-9]+\.)\s")
TABLE_ROW = re.compile(r"^\s*\|")
# A token with a slash or a tilde is a link, a path, or a slug. Its words
# ("acme/api") say nothing about direction, so they are removed first.
PATH_TOKEN = re.compile(r"\S*[/~]\S*")
PROVIDER_WORDS = re.compile(
    r"\b(backend|api|server|service|upstream|depends on|source of truth)\b", re.IGNORECASE)
CONSUMER_WORDS = re.compile(
    r"\b(frontend|front-end|web app|mobile app|downstream|consumed by|consumers?|calls this"
    r"|clients|client repos?)\b",
    re.IGNORECASE)


def empty(name, kind, direction="unknown"):
    """A new entry with every field in FIELDS set."""
    return {"name": name, "kind": kind, "direction": direction, "local_path": None,
            "gh_slug": None, "doc_url": None, "describes": None, "sources": []}


def read_text(path):
    """File text, or None when unreadable or larger than MAX_FILE_BYTES.
    Bad bytes are replaced, so a binary file cannot crash the scan."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def source_order(source):
    """Sort key for "file:line" so that line 9 sorts before line 10."""
    path, _, line = source.rpartition(":")
    return (path, int(line)) if path and line.isdigit() else (source, 0)


# --- repos from agent docs ---------------------------------------------------

def blocks(text):
    """Split markdown into (heading, [(line number, line), ...]) blocks.

    A block is one paragraph, one list item, or one table row. It ends at a
    blank line, a heading, a table row, or the next list item. A link and
    its description on a continuation line stay in one block.
    """
    out, heading, lines = [], "", []
    for n, line in enumerate(text.splitlines(), 1):
        starts_block = (not line.strip() or HEADING.match(line) or LIST_ITEM.match(line)
                        or TABLE_ROW.match(line))
        if lines and starts_block:
            out.append((heading, lines))
            lines = []
        if HEADING.match(line):
            heading = line
        elif TABLE_ROW.match(line):
            out.append((heading, [(n, line)]))
        elif line.strip():
            lines.append((n, line))
    if lines:
        out.append((heading, lines))
    return out


def direction_of(block_text, heading):
    """provider, consumer, or unknown, from the words around a link.

    The block decides first. The heading decides only when the block names
    neither direction, or both.
    """
    for text in (block_text, heading):
        text = PATH_TOKEN.sub(" ", text)
        provider = bool(PROVIDER_WORDS.search(text))
        consumer = bool(CONSUMER_WORDS.search(text))
        if provider != consumer:
            return "provider" if provider else "consumer"
    return "unknown"


def own_slug(repo):
    """owner/repo of the origin remote, lowercased, or None. A local git call."""
    try:
        proc = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    m = GITHUB_REMOTE.search(proc.stdout.strip())
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


def local_checkout(repo, home, name):
    """A git checkout named `name` next to the repo or under ~/dev, or None."""
    for candidate in (repo.parent / name, home / "dev" / name):
        if (candidate / ".git").exists():
            return str(candidate)
    return None


def repos_from_docs(repo, home):
    """Sibling repos named by GitHub links or ~/ paths in the agent docs."""
    found = []
    skip = own_slug(repo)
    for doc in DOC_FILES:
        text = read_text(repo / doc)
        if text is None:
            continue
        for heading, lines in blocks(text):
            direction = direction_of("\n".join(line for _, line in lines), heading)
            for n, line in lines:
                source = f"{doc}:{n}"
                for m in GITHUB_LINK.finditer(line):
                    owner = m.group(1)
                    name = m.group(2).rstrip(".").removesuffix(".git")
                    slug = f"{owner}/{name}"
                    if owner.lower() in GITHUB_NON_OWNERS or slug.lower() == skip:
                        continue
                    entry = empty(slug, "repo", direction)
                    entry["gh_slug"] = slug
                    entry["local_path"] = local_checkout(repo, home, name)
                    entry["sources"] = [source]
                    found.append(entry)
                for m in HOME_PATH.finditer(line):
                    path = home / m.group(1)
                    if not (path / ".git").exists() or path.resolve() == repo:
                        continue
                    entry = empty(path.name, "repo", direction)
                    entry["local_path"] = str(path)
                    entry["sources"] = [source]
                    found.append(entry)
    return found


# --- merge -------------------------------------------------------------------

def merge_key(entry):
    """Repos merge on their last path part, so `acme/api` and `~/dev/api` meet."""
    name = entry["name"]
    if entry["kind"] == "repo":
        name = name.rsplit("/", 1)[-1]
    return (entry["kind"], name.lower())


def merge_direction(a, b):
    if a == b or b == "unknown":
        return a
    if a == "unknown":
        return b
    return "unknown"  # the sources disagree


def merge(entries):
    """One entry per component, sorted. The first non-null value of a field
    wins. Discovery order is fixed, so the result is fixed too."""
    merged = {}
    for e in entries:
        key = merge_key(e)
        if key not in merged:
            merged[key] = {**e, "sources": list(e["sources"])}
            continue
        m = merged[key]
        if "/" in e["name"] and "/" not in m["name"]:
            m["name"] = e["name"]
        for field in ("local_path", "gh_slug", "doc_url", "describes"):
            m[field] = m[field] or e[field]
        m["direction"] = merge_direction(m["direction"], e["direction"])
        m["sources"] += e["sources"]
    for m in merged.values():
        m["sources"] = sorted(set(m["sources"]), key=source_order)
    return sorted(merged.values(), key=lambda e: (e["kind"], e["name"].lower(), e["name"]))


def discover(repo, home=None):
    """Every boundary component of `repo`, merged and sorted."""
    repo = Path(repo).resolve()
    home = Path(home) if home else Path.home()
    return merge(repos_from_docs(repo, home))
