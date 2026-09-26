#!/usr/bin/env python3
"""churn_boundaries.py - find the components a repo talks to across a boundary.

The churn skill's placement lens reads this list. It asks whether a review
cluster's behavior belongs in another component. That component can be a
backend, a frontend, an external API, or a library.

Discovery reads only local files. They are the repo's agent docs, its
dependency manifests, env var names, HTTP client base URLs, and API schemas.
Its one subprocess is a local `git remote get-url`. It never makes a network
call, and it never reads a real `.env` file.

Every entry is a Boundary. Entries for one component merge, and the list is
sorted. Two runs give byte-identical output when the tree, the sibling
checkouts, and the installed dependencies are unchanged.

The entry point is discover(). `pr_churn_cli.py boundaries` calls it and
writes the result to boundaries.json. The sections below run in the order
discover() calls them: repos, libraries, services, and schemas.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, TypedDict

# tomllib is stdlib from Python 3.11. macOS still ships 3.9 as python3, and the
# churn CLI's `collect` imports this module, so its absence must not crash.
try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

Kind = Literal["repo", "service", "library", "schema"]
Direction = Literal["provider", "consumer", "unknown"]


class Boundary(TypedDict):
    """One component the repo talks to. Keys keep this order in the output."""
    name: str
    kind: Kind
    direction: Direction
    local_path: str | None
    gh_slug: str | None
    doc_url: str | None
    describes: str | None  # schemas only: the service the schema describes
    # "file:line" locations that named the component. A generated client
    # directory is named by its path alone, with no line.
    sources: list[str]


class Provider(TypedDict):
    """One entry of churn_known_providers.json."""
    host: str  # an fnmatch pattern, so *.sentry.io matches every subdomain
    name: str
    doc_url: str


# (directory, sorted file names), as walk() yields them.
Tree = list[tuple[Path, list[str]]]


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
SENTENCE_END = re.compile(r"(?<=[.!?])\s")
PROVIDER_WORDS = re.compile(
    r"\b(backend|api|server|service|upstream|depends on|source of truth)\b", re.IGNORECASE)
CONSUMER_WORDS = re.compile(
    r"\b(frontend|front-end|web app|mobile app|downstream|consumed by|consumers?|calls this"
    r"|clients?|client repos?)\b",
    re.IGNORECASE)
REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
GO_REQUIRE = re.compile(
    r"^(?:require\s+)?([A-Za-z0-9._~/-]+\.[A-Za-z0-9._~/-]+)\s+v[0-9]\S*(\s*//\s*indirect)?")
PROVIDERS_FILE = Path(__file__).with_name("churn_known_providers.json")

# Pruned by walk(), so both the code scan and the schema scan skip them.
# Their hosts are fakes or other people's services, and vendored trees hold
# thousands of them.
SKIP_DIRS = {"__mocks__", "__pycache__", "__tests__", "android", "build", "coverage", "dist",
             "e2e", "env", "fixtures", "ios", "mocks", "node_modules", "Pods", "site-packages",
             "test", "tests", "vendor", "venv"}
TEST_FILE = re.compile(r"\.(?:test|spec)\.[a-z]+$|^test_.*\.py$|_test\.(?:py|go)$")
SOURCE_SUFFIXES = {".cjs", ".go", ".js", ".jsx", ".kt", ".mjs", ".py", ".rb", ".swift", ".ts", ".tsx"}
# Env templates only. A real `.env` can hold secrets and is never read.
ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}

ENV_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]*_(?:URL|ENDPOINT|HOST)\b")
# In source files an env name counts only on a line that reads configuration.
# Otherwise every constant such as SERVER_URL = re.compile(...) is a service.
# The last branch is a settings-class field: `SUPABASE_URL: str`.
ENV_READ = re.compile(
    r"process\.env|import\.meta\.env|os\.environ|getenv\(|os\.Getenv|ENV\[|expoConfig"
    r"|\bsettings\.|\bconfig\.|^\s*[A-Z][A-Z0-9_]*\s*:\s*[A-Za-z]")
ENV_SUFFIXES = ("_API_URL", "_BASE_URL", "_API_ENDPOINT", "_ENDPOINT", "_API_HOST", "_HOST", "_URL")
# Framework prefixes that expose a variable to client code. TEST_ names a test
# copy of a real service, so TEST_SUPABASE_URL names supabase.
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
# Two-part public suffixes: the name is the label before them, so
# api.acme.co.uk names acme.
TWO_PART_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.nz", "co.jp",
                     "com.br", "co.in", "com.mx", "co.za"}
# Shared hosting domains: every tenant is a different service, so the name is
# the full host.
SHARED_HOSTS = {"amazonaws.com", "herokuapp.com", "vercel.app", "netlify.app", "onrender.com",
                "azurewebsites.net", "cloudfunctions.net", "appspot.com", "workers.dev",
                "github.io", "fly.dev", "pages.dev", "run.app"}
OPENAPI_FILE = re.compile(r"^(?:openapi|swagger)\.(?:json|ya?ml)$")
GRAPHQL_FILE = re.compile(r"\.(?:graphql|gql)$")
GENERATED_DIRS = {"__generated__", "generated", "openapi-client"}
SERVERS_KEY = re.compile(r"""^\s*["']?servers["']?\s*:""")
SERVER_URL = re.compile(r"""^\s*-?\s*["']?url["']?\s*:\s*["']?(https?://[^\s"',]+)""")
# A server URL must sit this close below the `servers` key. Other `url`
# keys (contact, license, externalDocs) are not servers.
SERVER_URL_WINDOW = 5
# Swagger 2.0 names its server with a top-level `host`, not `servers`.
SWAGGER_HOST = re.compile(r"""^\s{0,2}["']?host["']?\s*:\s*["']?([A-Za-z0-9.-]+)""")


def make_entry(name: str, kind: Kind, source: str, direction: Direction = "provider", *,
               local_path: str | None = None, gh_slug: str | None = None,
               doc_url: str | None = None) -> Boundary:
    """A Boundary with one source. Direction defaults to provider, because this
    repo calls its libraries, services, and schemas. Only repos_from_docs reads
    a direction from text. schemas() sets `describes` after it reads servers."""
    return Boundary(name=name, kind=kind, direction=direction, local_path=local_path,
                    gh_slug=gh_slug, doc_url=doc_url, describes=None, sources=[source])


def read_text(path: Path) -> str | None:
    """File text, or None when unreadable or larger than MAX_FILE_BYTES.
    Bad bytes are replaced, so a binary file cannot crash the scan."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def source_order(source: str) -> tuple[str, int]:
    """Sort key for "file:line" so that line 9 sorts before line 10."""
    path, _, line = source.rpartition(":")
    return (path, int(line)) if path and line.isdigit() else (source, 0)


# --- repos from agent docs ---------------------------------------------------

def blocks(text: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Split markdown into (heading, [(line number, line), ...]) blocks.

    A block is one paragraph, one list item, or one table row. It ends at a
    blank line, a heading, a table row, or the next list item. A link and
    its description on a continuation line stay in one block.
    """
    out: list[tuple[str, list[tuple[int, str]]]] = []
    heading, lines = "", []
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


def direction_of(block_text: str, heading: str) -> Direction:
    """provider, consumer, or unknown, from the words around a link.

    The block's first sentence decides first. A link is usually followed by
    what the component is ("the backend", "the iOS client"). Later
    sentences often name the other side ("adapting the client to it..."). The
    whole block, then the heading, decide only when the text before them names
    neither direction, or both.
    """
    block_text = PATH_TOKEN.sub(" ", block_text)
    first_sentence = SENTENCE_END.split(block_text.strip(), maxsplit=1)[0]
    for text in (first_sentence, block_text, heading):
        text = PATH_TOKEN.sub(" ", text)
        provider = bool(PROVIDER_WORDS.search(text))
        consumer = bool(CONSUMER_WORDS.search(text))
        if provider != consumer:
            return "provider" if provider else "consumer"
    return "unknown"


def origin_url(repo: Path) -> str | None:
    """The origin remote's URL, or None when there is none. A local git call."""
    try:
        proc = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None


def github_slug(url: str | None) -> str | None:
    """owner/repo, lowercased, when `url` is a GitHub remote. Else None."""
    m = GITHUB_REMOTE.search(url or "")
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


def own_slug(repo: Path) -> str | None:
    """owner/repo of the origin remote when it is on GitHub, else None."""
    return github_slug(origin_url(repo))


def local_checkout(repo: Path, home: Path, slug: str) -> str | None:
    """A git checkout of `slug` next to the repo or under ~/dev, or None.
    A checkout whose origin names another repo, on GitHub or elsewhere, is
    skipped. A checkout with no origin is accepted, since nothing says it is a
    different repo."""
    name = slug.rsplit("/", 1)[-1]
    for candidate in (repo.parent / name, home / "dev" / name):
        if not (candidate / ".git").exists():
            continue
        url = origin_url(candidate)
        if url is None or github_slug(url) == slug.lower():
            return str(candidate)
    return None


def repos_from_docs(repo: Path, home: Path) -> list[Boundary]:
    """Sibling repos named by GitHub links or ~/ paths in the agent docs.
    `repo` must be resolved, so the repo never lists itself."""
    found: list[Boundary] = []
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
                    found.append(make_entry(slug, "repo", source, direction, gh_slug=slug,
                                            local_path=local_checkout(repo, home, slug)))
                for m in HOME_PATH.finditer(line):
                    path = home / m.group(1).rstrip(".")  # a sentence may end on the path
                    if not (path / ".git").exists() or path.resolve() == repo:
                        continue
                    origin = own_slug(path)
                    found.append(make_entry(origin or path.name, "repo", source, direction,
                                            local_path=str(path), gh_slug=origin))
    return found


# --- libraries from manifests ------------------------------------------------

def as_table(value: object) -> dict[str, object]:
    """`value` when it is a JSON object or TOML table, else an empty one.
    Manifests are untrusted input: `project = "x"` is valid TOML."""
    return value if isinstance(value, dict) else {}


def line_of(text: str, needle: str, after: str | None = None) -> int:
    """1-based line of the first `needle` at or below the first line holding
    `after`. Line 1 when it is not found."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if after in line), 0) if after else 0
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return i + 1
    return 1


def npm_libraries(repo: Path) -> list[Boundary]:
    """`dependencies` from package.json. Dev dependencies are not boundaries."""
    text = read_text(repo / "package.json")
    if text is None:
        return []
    try:
        deps = as_table(as_table(json.loads(text)).get("dependencies"))
    except json.JSONDecodeError:
        return []
    out: list[Boundary] = []
    for name in sorted(deps):
        path = repo / "node_modules" / name
        line = line_of(text, json.dumps(name), after='"dependencies"')
        out.append(make_entry(name, "library", f"package.json:{line}",
                              local_path=str(path) if path.is_dir() else None,
                              doc_url=f"https://www.npmjs.com/package/{name}"))
    return out


def site_package(repo: Path, name: str) -> str | None:
    """The package's directory in the repo's .venv, or None."""
    module = name.lower().replace("-", "_")
    hits = sorted((repo / ".venv" / "lib").glob(f"python*/site-packages/{module}"))
    return str(hits[0]) if hits else None


def python_libraries(repo: Path) -> list[Boundary]:
    """PEP 621 `project.dependencies` and Poetry dependencies from pyproject.toml."""
    text = read_text(repo / "pyproject.toml")
    if text is None or tomllib is None:
        return []
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    names: set[str] = set()
    specs = as_table(data.get("project")).get("dependencies")
    for spec in specs if isinstance(specs, list) else []:
        m = REQUIREMENT_NAME.match(spec.strip()) if isinstance(spec, str) else None
        if m:
            names.add(m.group(0))
    poetry = as_table(as_table(as_table(data.get("tool")).get("poetry")).get("dependencies"))
    names.update(name for name in poetry if name.lower() != "python")
    return [make_entry(name, "library", f"pyproject.toml:{line_of(text, name, after='dependencies')}",
                       local_path=site_package(repo, name),
                       doc_url=f"https://pypi.org/project/{name}/")
            for name in sorted(names, key=str.lower)]


def go_libraries(repo: Path) -> list[Boundary]:
    """Direct `require` lines from go.mod. `// indirect` lines are skipped."""
    text = read_text(repo / "go.mod")
    if text is None:
        return []
    out: list[Boundary] = []
    in_block = False
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
        gh = GITHUB_LINK.match(module)
        out.append(make_entry(module, "library", f"go.mod:{n}", doc_url=f"https://pkg.go.dev/{module}",
                              gh_slug=f"{gh.group(1)}/{gh.group(2)}" if gh else None))
    return out


def libraries_from_manifests(repo: Path) -> list[Boundary]:
    return npm_libraries(repo) + python_libraries(repo) + go_libraries(repo)


# --- services from code ------------------------------------------------------

def load_providers() -> list[Provider]:
    """The known-provider table. It ships with the plugin, so a test checks its
    shape instead of this function."""
    providers: list[Provider] = json.loads(PROVIDERS_FILE.read_text())
    return providers


def walk(repo: Path) -> Tree:
    """(directory, sorted file names) for each directory discovery reads.
    Hidden directories and SKIP_DIRS are pruned. The order is fixed."""
    tree: Tree = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        tree.append((Path(root), sorted(files)))
    return tree


def scanned_files(tree: Tree) -> Iterator[Path]:
    """Source files and env templates, in walk order."""
    for root, files in tree:
        for name in files:
            if name in ENV_TEMPLATES or (Path(name).suffix in SOURCE_SUFFIXES
                                         and not TEST_FILE.search(name)
                                         and not name.endswith(".min.js")):
                yield root / name


def env_service_name(token: str) -> str:
    """PAYMENTS_API_URL -> payments. A bare API_URL or BASE_URL -> api."""
    for prefix in ENV_PREFIXES:
        token = token.removeprefix(prefix)
    for suffix in ENV_SUFFIXES:
        if token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    name = token.lower().replace("_", "-")
    return "api" if name in ("", "api", "base") else name


def is_public_host(host: str) -> bool:
    return bool(REAL_TLD.search(host)) and not LOCAL_HOST.search(host)


def host_service(host: str, providers: list[Provider]) -> tuple[str, str | None]:
    """(name, doc_url) for a host. An unknown host is named by the label
    before its public suffix (api.payments.io -> payments). A host on a
    shared hosting domain keeps its full name."""
    host = host.lower()
    for provider in providers:
        if fnmatch.fnmatch(host, provider["host"]):
            return provider["name"], provider["doc_url"]
    labels = host.split(".")
    if any(host == d or host.endswith("." + d) for d in SHARED_HOSTS):
        return host, None
    suffix_size = 2 if ".".join(labels[-2:]) in TWO_PART_SUFFIXES else 1
    return labels[max(len(labels) - suffix_size - 1, 0)], None


def service(name: str, doc_url: str | None, source: str) -> Boundary:
    return make_entry(name, "service", source, doc_url=doc_url)


def services_from_code(repo: Path, tree: Tree, providers: list[Provider]) -> list[Boundary]:
    """Services named by env var names, and by URLs on HTTP client lines."""
    doc_by_name = {p["name"]: p["doc_url"] for p in providers}
    found: list[Boundary] = []
    for path in scanned_files(tree):
        text = read_text(path)
        env_file = path.name in ENV_TEMPLATES
        # Most files match neither pattern. Checking the whole text once
        # skips the per-line work for them.
        if text is None or not (env_file or ENV_TOKEN.search(text) or CLIENT_CALL.search(text)):
            continue
        rel = path.relative_to(repo).as_posix()
        for n, line in enumerate(text.splitlines(), 1):
            for token in ENV_TOKEN.findall(line) if env_file or ENV_READ.search(line) else []:
                name = env_service_name(token)
                found.append(service(name, doc_by_name.get(name), f"{rel}:{n}"))
            # A URL counts on an HTTP client line. It also counts on a line that
            # names a *_URL constant, as a config module does.
            if env_file or CLIENT_CALL.search(line) or ENV_TOKEN.search(line):
                for host in URL_HOST.findall(line):
                    if is_public_host(host):
                        found.append(service(*host_service(host, providers), f"{rel}:{n}"))
    return found


# --- schemas -----------------------------------------------------------------

def openapi_servers(path: Path, rel: str, providers: list[Provider]) -> list[Boundary]:
    """The services an OpenAPI file names, in file order. They come from the
    `servers` list, or from a Swagger 2.0 top-level `host`."""
    found: list[Boundary] = []
    servers_line = None
    for n, line in enumerate((read_text(path) or "").splitlines(), 1):
        if SERVERS_KEY.match(line):
            servers_line = n
            continue
        swagger = SWAGGER_HOST.match(line)
        if swagger and is_public_host(swagger.group(1)):
            found.append(service(*host_service(swagger.group(1), providers), f"{rel}:{n}"))
            continue
        m = SERVER_URL.match(line)
        if not m or servers_line is None or n - servers_line > SERVER_URL_WINDOW:
            continue
        host = URL_HOST.match(m.group(1))  # None for a hostless URL such as https:///v1
        if host and is_public_host(host.group(1)):
            found.append(service(*host_service(host.group(1), providers), f"{rel}:{n}"))
    return found


def schemas(repo: Path, tree: Tree, providers: list[Provider]) -> list[Boundary]:
    """OpenAPI and GraphQL schema files, and generated client directories.
    An OpenAPI server URL also yields the service the schema describes."""
    found: list[Boundary] = []
    for root, files in tree:
        rel_dir = root.relative_to(repo).as_posix()
        if root.name in GENERATED_DIRS:
            found.append(make_entry(rel_dir, "schema", rel_dir, local_path=str(root)))
        for file_name in files:
            path = root / file_name
            rel = path.relative_to(repo).as_posix()
            if GRAPHQL_FILE.search(file_name):
                found.append(make_entry(rel, "schema", f"{rel}:1", local_path=str(path)))
            elif OPENAPI_FILE.search(file_name):
                entry = make_entry(rel, "schema", f"{rel}:1", local_path=str(path))
                servers = openapi_servers(path, rel, providers)
                entry["describes"] = servers[0]["name"] if servers else None
                found += [entry, *servers]
    return found


# --- merge -------------------------------------------------------------------

def merge_key(entry: Boundary) -> tuple[Kind, str]:
    """Repos with a slug merge on the full slug, so acme/api and other/api stay
    apart. fold_checkouts() handles repos found only as a checkout path."""
    if entry["kind"] == "repo" and entry["gh_slug"]:
        return ("repo", entry["gh_slug"].lower())
    return (entry["kind"], entry["name"].lower())


def fold_checkouts(merged: dict[tuple[Kind, str], Boundary]) -> None:
    """Fold each slug-less repo into the one slug repo with its last path part.
    With two or more candidates, the checkout stays its own entry."""
    for key, entry in list(merged.items()):
        if entry["kind"] != "repo" or entry["gh_slug"]:
            continue
        matches = [m for m in merged.values() if m["kind"] == "repo" and m["gh_slug"]
                   and m["gh_slug"].rsplit("/", 1)[-1].lower() == entry["name"].lower()]
        if len(matches) != 1:
            continue
        target = matches[0]
        target["local_path"] = target["local_path"] or entry["local_path"]
        target["direction"] = merge_direction(target["direction"], entry["direction"])
        target["sources"] += entry["sources"]
        del merged[key]


def merge_direction(a: Direction, b: Direction) -> Direction:
    if a == b or b == "unknown":
        return a
    if a == "unknown":
        return b
    return "unknown"  # the sources disagree


def merge(entries: list[Boundary]) -> list[Boundary]:
    """One entry per component, sorted. The first non-null value of a field
    wins. Discovery order is fixed, so the result is fixed too."""
    merged: dict[tuple[Kind, str], Boundary] = {}
    for e in entries:
        key = merge_key(e)
        if key not in merged:
            merged[key] = Boundary(**{**e, "sources": list(e["sources"])})
            continue
        m = merged[key]
        m["local_path"] = m["local_path"] or e["local_path"]
        m["gh_slug"] = m["gh_slug"] or e["gh_slug"]
        m["doc_url"] = m["doc_url"] or e["doc_url"]
        m["describes"] = m["describes"] or e["describes"]
        m["direction"] = merge_direction(m["direction"], e["direction"])
        m["sources"] += e["sources"]
    fold_checkouts(merged)
    for m in merged.values():
        m["sources"] = sorted(set(m["sources"]), key=source_order)
    return sorted(merged.values(), key=lambda e: (e["kind"], e["name"].lower(), e["name"]))


def skipped_sources(repo: str | Path) -> list[str]:
    """Sources that discover() could not read on this Python, one note each.
    The caller reports them, so a missing kind is never mistaken for none."""
    if tomllib is None and (Path(repo) / "pyproject.toml").is_file():
        return ["pyproject.toml: reading it needs Python 3.11 or later"]
    return []


def discover(repo: str | Path, home: Path | None = None) -> list[Boundary]:
    """Every boundary component of `repo`, merged and sorted. `home` defaults
    to the user's home directory."""
    root = Path(repo).resolve()
    home = home or Path.home()
    providers = load_providers()
    tree = walk(root)
    # The order matters: merge() keeps the first non-null value of each field.
    return merge(repos_from_docs(root, home)
                 + libraries_from_manifests(root)
                 + services_from_code(root, tree, providers)
                 + schemas(root, tree, providers))
