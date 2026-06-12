"""`crew install`: wire ~/plans/* into a groundcrew config with a connection
that survives plan-keeper upgrades.

groundcrew configs come in two shapes and ``crew install`` patches both:

* **TS/JS** (``crew.config.ts``, what ``crew init`` writes) is patched by string
  surgery. The managed region is bracketed by comment sentinels and sits flush
  inside the ``sources:`` array's ``[``: inserted on first run, replaced in
  place on every re-run.
* **JSON** (``crew.config.json``, ``.crewrc``) has no comments, so sentinels are
  impossible. It is patched by parse → upsert the ``plankeeper`` entry into the
  ``sources`` array (matched by ``name``) → re-serialize. Finding the entry by
  name is what makes re-runs idempotent: the existing block is replaced in
  place, never blindly appended.

Format is decided by content, not extension: a TS config (``export default
{…}``) never parses as JSON and a JSON config always does, so ``json.loads``
succeeding is an unambiguous discriminator (and it also catches extensionless
``.crewrc`` configs).

The command strings call the resolved absolute ``plan-keeper`` binary directly,
so dispatch never depends on groundcrew's runtime ``$PATH`` and the wiring
doesn't rot when the plugin version bumps (brew relinks the binary in place on
upgrade).

plan-keeper does not touch ``workspace.knownRepositories`` — registering the
repos groundcrew may dispatch into is left to the config owner.

The pure patchers (:func:`build_patched_config`, :func:`build_patched_json_config`)
are separated from the IO/process orchestration (:func:`run_crew_install`) so the
anchoring + idempotency logic is unit-testable without a real groundcrew install
on the machine.
"""
import difflib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional, TextIO

from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.groundcrew import _collect_crew_issues
from plan_keeper.storage import write_atomic

# A managed region is bracketed by these comment sentinels. Only the
# ``sources:`` array carries one; it sits flush inside that array's `[`.
SENTINEL_START = "/* plan-keeper:managed:start */"
SENTINEL_END = "/* plan-keeper:managed:end */"

# The shell source's ``name``; the upsert key for the JSON patcher and the
# label groundcrew shows.
_SOURCE_NAME = "plankeeper"

# Directory holding the XDG groundcrew config, relative to home.
_XDG_CONFIG_DIR_REL = Path(".config") / "groundcrew"

# Candidate config filenames under the XDG dir, highest priority first. Mirrors
# groundcrew's own XDG fallback order (see groundcrew ``config.ts``
# ``XDG_FALLBACK_NAMES``) so ``crew install`` resolves the same file ``crew``
# would load — including a ``crew.config.json``, which is why the bare
# ``crew.config.ts`` default missed it before.
_XDG_CONFIG_NAMES = (
    "crew.config.ts",
    "crew.config.mjs",
    "crew.config.js",
    "crew.config.json",
    "config.ts",
)

# Type of the injected `crew doctor` runner: takes the config path, returns
# (exit_code, combined_output).
DoctorRunner = Callable[[Path], "tuple[int, str]"]


def resolve_config_path(config_arg: Optional[str], env: "dict[str, str]",
                        home: Path) -> Path:
    """Resolve the groundcrew config path: ``--config`` > ``$GROUNDCREW_CONFIG``
    > the first existing ``~/.config/groundcrew/`` config among
    :data:`_XDG_CONFIG_NAMES`.

    Searching the candidate names (not hardcoding ``crew.config.ts``) is what
    lets a no-arg ``crew install`` find a ``crew.config.json``. When none of the
    candidates exist, the canonical ``crew.config.ts`` path is returned so the
    caller's "not found; run ``crew init``" error points at the conventional
    location.

    Env and home are passed in (not read off ``os.environ``/``Path.home()``
    here) so the resolution is a pure function the tests can pin.
    """
    if config_arg:
        return Path(config_arg).expanduser()
    env_path = env.get("GROUNDCREW_CONFIG", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    base = home / _XDG_CONFIG_DIR_REL
    for name in _XDG_CONFIG_NAMES:
        candidate = base / name
        if candidate.exists():
            return candidate
    return base / "crew.config.ts"


def _source_commands(pk: str) -> "dict[str, str]":
    """The shell-source ``commands`` map, the single source of truth shared by
    the TS and JSON renderers.

    ``${id}`` is groundcrew's token (substituted into the argv before the
    command runs); it is intentionally literal here, not a Python value.
    """
    return {
        "verify": f"{pk} crew fetch >/dev/null",
        "fetch": f"{pk} crew fetch",
        "resolveOne": f"{pk} crew get ${{id}}",
        "markInProgress": f"{pk} crew start ${{id}}",
        "markInReview": f"{pk} crew review ${{id}}",
        # markDone is terminal — unlike the start/review legs (an in-place
        # Status rewrite), it archives the plan: file-meta set --status done
        # relocates it into done/ and stamps Completed on. We reuse that tested
        # engine (addressed by the plan's Plan-keeper Ticket, which is exactly
        # groundcrew's ${id}) instead of a bespoke `crew done`. --on-collision
        # suffix keeps the unattended hook non-blocking and non-destructive: a
        # same-name plan already in done/ is suffixed, never overwritten, and
        # the leg never fails the dispatch.
        "markDone": f"{pk} file-meta set --ticket ${{id}} --status done "
                    f"--on-collision suffix",
    }


def _render_source_object(pk: str) -> dict:
    """The shell-source as a plain dict, for the JSON patcher and JSON safety
    valve."""
    return {"kind": "shell", "name": _SOURCE_NAME, "commands": _source_commands(pk)}


def _render_source_region(pk: str) -> str:
    """The shell-source object injected into a TS ``sources:`` array."""
    cmds = _source_commands(pk)
    lines = ",\n".join(f'          {key}: "{val}"' for key, val in cmds.items())
    return (
        f'      {{ kind: "shell", name: "{_SOURCE_NAME}",\n'
        "        commands: {\n"
        f"{lines} }} }},"
    )


def _find_active_array_open(config: str, array_name: str) -> Optional[int]:
    """Index just after the ``[`` of the first NON-line-commented
    ``<array_name>: [``, or None if every occurrence is commented out / absent.

    ``crew init`` ships the ``sources:`` array commented out (the built-in
    Linear adapter is implicit), so a naive regex would anchor inside a ``//``
    comment and emit broken TS. A match is treated as commented when a ``//``
    precedes it on its own line — enough for the configs ``crew init``
    generates, without a full TS parse.
    """
    for match in re.finditer(rf"\b{re.escape(array_name)}\s*:\s*\[", config):
        line_start = config.rfind("\n", 0, match.start()) + 1
        if "//" in config[line_start:match.start()]:
            continue
        return match.end()
    return None


def _upsert_managed_region(config: str, array_name: str, body: str) -> Optional[str]:
    """Insert (or, on re-run, replace) a sentinel-wrapped ``body`` flush inside
    the first active ``<array_name>: [``. Returns the patched config, or None if
    no active array opening can be located.

    Idempotency hinges on placement: the managed region is always written flush
    against the array's opening ``[``. So detecting a prior install is purely
    positional — if the first non-whitespace content after ``[`` is our
    ``SENTINEL_START``, replace through the matching ``SENTINEL_END``; otherwise
    insert a fresh region. The "only whitespace between ``[`` and the sentinel"
    guard is what keeps the two arrays' identical sentinels from being confused:
    an unmanaged array has either real entries or a ``]`` flush after ``[``, so
    a far-off sentinel belonging to the *other* array is never mistaken for this
    array's region.
    """
    insert_at = _find_active_array_open(config, array_name)
    if insert_at is None:
        return None
    region = f"{SENTINEL_START}\n{body}\n{SENTINEL_END}"

    start = config.find(SENTINEL_START, insert_at)
    if start != -1 and config[insert_at:start].strip() == "":
        end = config.find(SENTINEL_END, start)
        if end == -1:
            return None  # malformed: start sentinel without a matching end
        end += len(SENTINEL_END)
        return config[:start] + region + config[end:]
    return config[:insert_at] + "\n" + region + "\n" + config[insert_at:]


def _create_sources_array(config: str, body: str) -> Optional[str]:
    """Add a fresh ``sources: [<region>],`` key just inside ``export default {``.

    The default ``crew init`` config has no *active* ``sources`` array (it's
    commented out), so there's nothing to upsert into — but adding a new key to
    the export object is a known-safe insertion. None if there's no
    ``export default {`` to anchor on (then the caller's safety valve fires).
    """
    match = re.search(r"export\s+default\s*\{", config)
    if match is None:
        return None
    at = match.end()
    block = f"\n  sources: [\n{SENTINEL_START}\n{body}\n{SENTINEL_END}\n  ],"
    return config[:at] + block + config[at:]


def build_patched_config(config: str, pk: str) -> Optional[str]:
    """Patch the managed ``sources`` region into ``config``, returning the new text.

    ``sources`` is upserted into the active array when present, else a fresh
    ``sources`` key is created in the export object (the common case — the
    default config comments ``sources`` out). None signals the caller to fall
    back to the manual-paste safety valve when there is neither an active
    ``sources`` array nor an ``export default {`` object to add one to — and
    also when an active ``sources`` array carries a *malformed* managed region
    (a start sentinel with no matching end), where failing fast beats minting a
    duplicate ``sources`` key.
    """
    sources_body = _render_source_region(pk)
    # _upsert_managed_region returns None for two distinct reasons: there is no
    # active `sources:` array to anchor in, or the array exists but its managed
    # region is malformed (a SENTINEL_START with no matching SENTINEL_END). Only
    # the first warrants creating a fresh `sources` key — falling through on the
    # malformed case would mint a *second* `sources` key beside the broken one.
    # Distinguish them by whether an active array was actually located.
    has_active_sources = _find_active_array_open(config, "sources") is not None
    patched = _upsert_managed_region(config, "sources", sources_body)
    if patched is None:
        if has_active_sources:
            return None  # malformed managed block — fail fast, don't duplicate
        patched = _create_sources_array(config, sources_body)
    return patched


def looks_like_json(config: str) -> bool:
    """Whether ``config`` is a JSON document (so the JSON patcher owns it).

    A TS/JS config (``export default {…}``) never parses as JSON; a JSON config
    always does — so a successful parse is the discriminator. Returns False for a
    JSON value that isn't an object (a bare array/scalar): there's no ``sources``
    key to host, so the JSON patcher would only reject it; routing it to the
    safety valve via the False branch reports the same outcome.
    """
    try:
        return isinstance(json.loads(config), dict)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def build_patched_json_config(config: str, pk: str) -> Optional[str]:
    """Upsert the ``plankeeper`` shell source into a JSON config's ``sources``
    array, returning the re-serialized JSON (2-space indent, trailing newline).

    Idempotent by construction: the source is matched by ``name`` and the
    matching slot is overwritten in place, so re-runs (and a binary-path change)
    converge instead of stacking duplicates. A missing ``sources`` key is
    created; a foreign entry (e.g. ``{"kind": "linear"}``) is left untouched.

    None signals the caller's safety valve: the document isn't a JSON object, or
    its ``sources`` value is present but not an array (a shape we won't silently
    overwrite). Re-serializing reformats the whole file — acceptable for JSON,
    which has no comments to lose and no canonical layout to preserve, and far
    safer than string-surgery on structured data.
    """
    try:
        data = json.loads(config)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    sources = data.get("sources")
    if sources is None:
        sources = []
        data["sources"] = sources
    elif not isinstance(sources, list):
        return None  # malformed: `sources` is present but not an array
    entry = _render_source_object(pk)
    for index, source in enumerate(sources):
        if isinstance(source, dict) and source.get("name") == _SOURCE_NAME:
            sources[index] = entry
            break
    else:
        sources.append(entry)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _managed_blocks_text(pk: str, is_json: bool = False) -> str:
    """The managed ``sources`` block, labeled, for the manual-paste safety valve.

    Format-matched to the config so the user pastes valid syntax: a JSON object
    for a JSON config, the sentinel-wrapped TS region otherwise.
    """
    if is_json:
        obj = json.dumps(_render_source_object(pk), indent=2, ensure_ascii=False)
        return f"# Add this object to your config's `sources` array:\n{obj}"
    return (
        f"# Paste inside your config's `sources: [` array:\n"
        f"{SENTINEL_START}\n{_render_source_region(pk)}\n{SENTINEL_END}"
    )


def default_run_doctor(config_path: Path) -> "tuple[int, str]":
    """Run ``crew doctor`` against ``config_path`` via ``$GROUNDCREW_CONFIG``.

    Returns ``(exit_code, combined_stdout+stderr)``. A missing ``crew`` binary
    surfaces as a non-zero result with an explanatory message rather than an
    uncaught exception, so the caller's rollback path always runs.
    """
    try:
        proc = subprocess.run(
            ["crew", "doctor"],
            env={**os.environ, "GROUNDCREW_CONFIG": str(config_path)},
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return 127, (
            "`crew` not found on PATH — install groundcrew, or run "
            "`crew doctor` yourself against the patched config"
        )
    except subprocess.TimeoutExpired:
        # No `config loaded` marker → run_crew_install treats this as a
        # validation failure and rolls the patch back, which is the safe call
        # when we couldn't confirm the patched config is loadable.
        return 124, (
            "`crew doctor` timed out (>60s) — could not validate the patched "
            "config; run `crew doctor` yourself to investigate"
        )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def run_crew_install(
    config_path: Path,
    *,
    dry_run: bool,
    pk: str,
    run_doctor: DoctorRunner,
    out: TextIO,
) -> int:
    """Patch ``config_path`` to wire ~/plans/* into groundcrew, validating the
    result with ``crew doctor`` and rolling back on failure.

    Collaborators are injected (``pk`` = the resolved binary path, ``run_doctor``
    = the validation hook) so the orchestration is testable without a real
    groundcrew binary. The post-patch fetch check reads the in-process plan tree
    directly (``storage.PLAN_ROOT``), which the tests isolate by patching that
    constant.
    """
    if not config_path.exists():
        raise PlanKeeperCliError(
            f"groundcrew config not found at {config_path}; run `crew init` "
            f"first (or pass --config)",
            code=2,
        )
    original = config_path.read_text(encoding="utf-8")
    # Decide JSON vs TS by content: a TS config never parses as JSON, a JSON one
    # always does — so each patcher only ever sees a config it can handle.
    is_json = looks_like_json(original)
    if is_json:
        patched = build_patched_json_config(original, pk)
    else:
        patched = build_patched_config(original, pk)

    if patched is None:
        # Safety valve: nowhere to put the source. Write nothing; hand the user
        # the exact block to paste themselves, in the config's own format.
        if is_json:
            reason = (
                f"{config_path} is not a JSON object with a patchable "
                f"`sources` array"
            )
        else:
            reason = (
                f"could not locate a `sources:` array or an "
                f"`export default {{` object in {config_path}"
            )
        print(f"plan-keeper: {reason} — nothing was written.\n", file=out)
        print(_managed_blocks_text(pk, is_json=is_json), file=out)
        raise PlanKeeperCliError(
            "config anchor not found; printed the managed block for manual "
            "paste (nothing written)",
            code=2,
        )

    if dry_run:
        print(_format_diff(original, patched, config_path), file=out)
        return 0

    backup = config_path.with_name(config_path.name + ".bak")
    write_atomic(backup, original)
    write_atomic(config_path, patched)

    # `crew doctor`'s exit code lumps config validity together with environment
    # readiness (Linear API key, projectDir existence). We only own config
    # validity — so the rollback gate is whether doctor could *load* the
    # patched config, not whether every host/source check passed. A broken
    # patch fails to load; an unconfigured-but-valid environment still loads.
    rc, doctor_out = run_doctor(config_path)
    if not _doctor_loaded_config(doctor_out):
        write_atomic(config_path, original)  # restore in place; .bak stays too
        raise PlanKeeperCliError(
            f"crew doctor could not load the patched config (exit {rc}); "
            f"restored {config_path} from backup ({backup.name}):\n{doctor_out}",
            code=1,
        )

    plan_count = len(_collect_crew_issues())
    summary = (
        f"plan-keeper: wired the plans source into {config_path}; "
        f"config loads; {plan_count} plan(s) visible to fetch "
        f"(backup: {backup.name})"
    )
    if rc != 0:
        # Config is valid but doctor flagged unrelated environment issues — the
        # plans wiring is in place; tell the user how to review the rest.
        summary += (
            "\nnote: crew doctor reports issues unrelated to the plans source "
            "(e.g. Linear API key, projectDir) — your plans wiring is in place; "
            f"run `crew doctor` to review:\n{doctor_out}"
        )
    print(summary, file=out)
    return 0


def _doctor_loaded_config(doctor_out: str) -> bool:
    """Whether ``crew doctor`` managed to load/parse the config.

    groundcrew prints a ``config loaded`` line on a successful load and a config
    error otherwise. That load result — not doctor's aggregate exit code — is
    the signal that tells a patch that broke the TS apart from an environment
    that merely isn't configured yet (missing Linear key, absent projectDir).
    """
    return "config loaded" in doctor_out.lower()


def _format_diff(original: str, patched: str, config_path: Path) -> str:
    """A unified diff of the patch ``crew install --dry-run`` would apply."""
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"{config_path} (current)",
        tofile=f"{config_path} (patched)",
    )
    return "".join(diff).rstrip("\n")
