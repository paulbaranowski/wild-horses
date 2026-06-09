"""`crew install`: wire ~/plans/* into a groundcrew config with a connection
that survives plan-keeper upgrades.

The patch is sentinel-wrapped and idempotent: one managed region inside the
config's ``sources:`` array is inserted on first run and replaced in place on
every re-run. The command strings call the resolved absolute ``plan-keeper``
binary directly, so dispatch never depends on groundcrew's runtime ``$PATH``
and the wiring doesn't rot when the plugin version bumps (brew relinks the
binary in place on upgrade).

plan-keeper does not touch ``workspace.knownRepositories`` — registering the
repos groundcrew may dispatch into is left to the config owner.

The pure patcher (:func:`build_patched_config`) is separated from the IO/process
orchestration (:func:`run_crew_install`) so the anchoring + idempotency logic is
unit-testable without a real groundcrew install on the machine.
"""
import difflib
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

# Default config location when neither --config nor $GROUNDCREW_CONFIG is set.
_DEFAULT_CONFIG_REL = Path(".config") / "groundcrew" / "crew.config.ts"

# Type of the injected `crew doctor` runner: takes the config path, returns
# (exit_code, combined_output).
DoctorRunner = Callable[[Path], "tuple[int, str]"]


def resolve_config_path(config_arg: Optional[str], env: "dict[str, str]",
                        home: Path) -> Path:
    """Resolve the groundcrew config path: ``--config`` > ``$GROUNDCREW_CONFIG``
    > ``~/.config/groundcrew/crew.config.ts``.

    Env and home are passed in (not read off ``os.environ``/``Path.home()``
    here) so the resolution is a pure function the tests can pin.
    """
    if config_arg:
        return Path(config_arg).expanduser()
    env_path = env.get("GROUNDCREW_CONFIG", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return home / _DEFAULT_CONFIG_REL


def _render_source_region(pk: str) -> str:
    """The shell-source object injected into ``sources:``.

    ``${id}`` is groundcrew's token (substituted into the argv before the
    command runs); it is intentionally literal here, not a Python value.
    """
    return (
        '      { kind: "shell", name: "plankeeper",\n'
        "        commands: {\n"
        f'          verify: "{pk} crew fetch >/dev/null",\n'
        f'          fetch: "{pk} crew fetch",\n'
        f'          resolveOne: "{pk} crew get ${{id}}",\n'
        f'          markInProgress: "{pk} crew start ${{id}}",\n'
        f'          markInReview: "{pk} crew review ${{id}}" }} }},'
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


def _managed_blocks_text(pk: str) -> str:
    """The managed ``sources`` region, labeled, for the manual-paste safety valve."""
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
    patched = build_patched_config(original, pk)

    if patched is None:
        # Safety valve: no place to anchor `sources`. Write nothing; hand the
        # user the exact block to paste themselves.
        print(
            f"plan-keeper: could not locate a `sources:` array or an "
            f"`export default {{` object in {config_path} — nothing was written.\n",
            file=out,
        )
        print(_managed_blocks_text(pk), file=out)
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
