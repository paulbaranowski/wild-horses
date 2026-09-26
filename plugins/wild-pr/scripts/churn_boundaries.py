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

import fnmatch
import json
import os
import re
import subprocess
import tomllib
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
REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
GO_REQUIRE = re.compile(
    r"^(?:require\s+)?([A-Za-z0-9._~/-]+\.[A-Za-z0-9._~/-]+)\s+v[0-9]\S*(\s*//\s*indirect)?")
PROVIDERS_FILE = Path(__file__).with_name("churn_known_providers.json")

# Pruned during the code scan. Their hosts are fakes or other people's
# services, and vendored trees hold thousands of them.
SKIP_DIRS = {"__mocks__", "__pycache__", "__tests__", "android", "build", "coverage", "dist",
             "e2e", "env", "fixtures", "ios", "mocks", "node_modules", "Pods", "site-packages",
             "test", "tests", "vendor", "venv"}
TEST_FILE = re.compile(r"\.(?:test|spec)\.[a-z]+$|^test_.*\.py$|_test\.(?:py|go)$")
SOURCE_SUFFIXES = {".cjs", ".go", ".js", ".jsx", ".kt", ".mjs", ".py", ".rb", ".swift", ".ts", ".tsx"}
# Env templates only. A real `.env` can hold secrets and is never read.
ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}

ENV_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]*_(?:URL|ENDPOINT|HOST)\b")
ENV_SUFFIXES = ("_API_URL", "_BASE_URL", "_API_ENDPOINT", "_ENDPOINT", "_API_HOST", "_HOST", "_URL")
ENV_PREFIXES = ("EXPO_PUBLIC_", "NEXT_PUBLIC_", "NUXT_PUBLIC_", "REACT_APP_", "VITE_", "TEST_")
URL_HOST = re.compile(r"https?://([A-Za-z0-9.-]+)")
# A URL counts only on a line that makes an HTTP call or sets a base URL.
CLIENT_CALL = re.compile(
    r"fetch\(|axios|httpx\.|requests\.|aiohttp|createClient\(|base_?url|new URL\(|\bky\.|\bgot\("
    r"|urllib|http\.(?:Get|Post|NewRequest)|URLSession", re.IGNORECASE)
LOCAL_HOST = re.compile(
    r"^(?:localhost|0\.0\.0\.0|127\.|10\.|192\.168\.)"
    r"|\.(?:local|localhost|test|example|invalid|internal)$"
    r"|(?:^|\.)example\.(?:com|net|org)$", re.IGNORECASE)
REAL_TLD = re.compile(r"\.[A-Za-z]{2,}$")


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


# --- libraries from manifests ------------------------------------------------

def line_of(text, needle, after=None):
    """1-based line of the first `needle` at or below the first line holding
    `after`. Line 1 when it is not found."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if after in line), 0) if after else 0
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return i + 1
    return 1


def library(name, source, local_path=None, doc_url=None, gh_slug=None):
    entry = empty(name, "library", "provider")
    entry.update(local_path=local_path, doc_url=doc_url, gh_slug=gh_slug, sources=[source])
    return entry


def npm_libraries(repo):
    """`dependencies` from package.json. Dev dependencies are not boundaries."""
    text = read_text(repo / "package.json")
    if text is None:
        return []
    try:
        deps = json.loads(text).get("dependencies") or {}
    except (json.JSONDecodeError, AttributeError):
        return []
    out = []
    for name in sorted(deps):
        path = repo / "node_modules" / name
        line = line_of(text, json.dumps(name), after='"dependencies"')
        out.append(library(name, f"package.json:{line}",
                           local_path=str(path) if path.is_dir() else None,
                           doc_url=f"https://www.npmjs.com/package/{name}"))
    return out


def site_package(repo, name):
    """The package's directory in the repo's .venv, or None."""
    module = name.lower().replace("-", "_")
    hits = sorted((repo / ".venv" / "lib").glob(f"python*/site-packages/{module}"))
    return str(hits[0]) if hits else None


def python_libraries(repo):
    """PEP 621 `project.dependencies` and Poetry dependencies from pyproject.toml."""
    text = read_text(repo / "pyproject.toml")
    if text is None:
        return []
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    names = set()
    for spec in (data.get("project") or {}).get("dependencies") or []:
        m = REQUIREMENT_NAME.match(spec.strip()) if isinstance(spec, str) else None
        if m:
            names.add(m.group(0))
    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    names.update(name for name in poetry if name.lower() != "python")
    return [library(name, f"pyproject.toml:{line_of(text, name, after='dependencies')}",
                    local_path=site_package(repo, name),
                    doc_url=f"https://pypi.org/project/{name}/")
            for name in sorted(names, key=str.lower)]


def go_libraries(repo):
    """Direct `require` lines from go.mod. `// indirect` lines are skipped."""
    text = read_text(repo / "go.mod")
    if text is None:
        return []
    out, in_block = [], False
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_block = True
            continue
        if in_block and stripped == ")":
            in_block = False
            continue
        if not (in_block or stripped.startswith("require ")):
            continue
        m = GO_REQUIRE.match(stripped)
        if not m or m.group(2):
            continue
        module = m.group(1)
        gh = GITHUB_LINK.match(module) if module.startswith("github.com/") else None
        out.append(library(module, f"go.mod:{n}", doc_url=f"https://pkg.go.dev/{module}",
                           gh_slug=f"{gh.group(1)}/{gh.group(2)}" if gh else None))
    return out


def libraries_from_manifests(repo):
    return npm_libraries(repo) + python_libraries(repo) + go_libraries(repo)


# --- services from code ------------------------------------------------------

def load_providers(path=PROVIDERS_FILE):
    """The known-provider table. Raise ValueError when an entry is malformed."""
    table = json.loads(Path(path).read_text())
    for entry in table:
        if (not isinstance(entry, dict) or set(entry) != {"host", "name", "doc_url"}
                or not all(isinstance(v, str) for v in entry.values())):
            raise ValueError(f"bad provider entry in {path}: {entry!r}")
    return table


def walk(repo):
    """(directory, sorted file names) for each directory discovery reads.
    Hidden directories and SKIP_DIRS are pruned. The order is fixed."""
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        yield Path(root), sorted(files)


def scanned_files(repo):
    """Source files and env templates, in walk order."""
    for root, files in walk(repo):
        for name in files:
            if name in ENV_TEMPLATES or (Path(name).suffix in SOURCE_SUFFIXES
                                         and not TEST_FILE.search(name)
                                         and not name.endswith(".min.js")):
                yield root / name


def env_service_name(token):
    """PAYMENTS_API_URL -> payments. A bare API_URL or BASE_URL -> api."""
    for prefix in ENV_PREFIXES:
        token = token.removeprefix(prefix)
    for suffix in ENV_SUFFIXES:
        if token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    name = token.lower().replace("_", "-")
    return "api" if name in ("", "api", "base") else name


def is_public_host(host):
    return bool(REAL_TLD.search(host)) and not LOCAL_HOST.search(host)


def host_service(host, providers):
    """(name, doc_url) for a host. Unknown hosts are named by their
    second-to-last label: api.payments.io -> payments."""
    host = host.lower()
    for provider in providers:
        if fnmatch.fnmatch(host, provider["host"]):
            return provider["name"], provider["doc_url"]
    return host.split(".")[-2], None


def service(name, doc_url, source):
    entry = empty(name, "service", "provider")
    entry.update(doc_url=doc_url, sources=[source])
    return entry


def services_from_code(repo, providers):
    """Services named by env var names, and by URLs on HTTP client lines."""
    doc_by_name = {p["name"]: p["doc_url"] for p in providers}
    found = []
    for path in scanned_files(repo):
        text = read_text(path)
        if text is None:
            continue
        rel = path.relative_to(repo).as_posix()
        env_file = path.name in ENV_TEMPLATES
        for n, line in enumerate(text.splitlines(), 1):
            source = f"{rel}:{n}"
            for token in ENV_TOKEN.findall(line):
                name = env_service_name(token)
                found.append(service(name, doc_by_name.get(name), source))
            if env_file or CLIENT_CALL.search(line):
                for host in URL_HOST.findall(line):
                    if is_public_host(host):
                        found.append(service(*host_service(host, providers), source))
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


def discover(repo, home=None, providers=None):
    """Every boundary component of `repo`, merged and sorted."""
    repo = Path(repo).resolve()
    home = Path(home) if home else Path.home()
    providers = load_providers() if providers is None else providers
    return merge(repos_from_docs(repo, home)
                 + libraries_from_manifests(repo)
                 + services_from_code(repo, providers))
