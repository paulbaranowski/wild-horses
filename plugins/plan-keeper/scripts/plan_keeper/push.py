"""The `push` subcommand backend: read a plan file, extract its title, compose
the ticket description, validate the repo's config, and dispatch to Linear or
Jira. Imported by cli.cmd_push and called directly from tests.
"""
from pathlib import Path

from plan_keeper.config import load_config
from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.frontmatter import parse_frontmatter
from plan_keeper.jira import _push_jira
from plan_keeper.linear import LINEAR_DESCRIPTION_LIMIT, _push_linear
from plan_keeper.naming import derive_repo, derive_repo_full


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
    # derive_repo_full() and derive_repo(None) below take no explicit cwd, so the
    # resolved repo — and therefore which .plankeeper.json config load_config reads
    # for credentials — depends on os.getcwd() and the git remote origin, resolved
    # via subprocess. Push targets the repo of the process's working directory, not
    # the repo the plan file lives under.
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
            f"run /plan-{name} setup to configure",
            code=2,
        )
    if name == "linear":
        if not section.get("apiKey"):
            raise PlanKeeperCliError(
                "linear config missing apiKey — run /plan-linear setup", code=2,
            )
        if not defaults.get("teamId"):
            raise PlanKeeperCliError(
                "linear config defaults missing teamId — run /plan-linear setup",
                code=2,
            )
    elif name == "jira":
        for field in ("site", "email", "apiToken"):
            if not section.get(field):
                raise PlanKeeperCliError(
                    f"jira config missing {field} — run /plan-jira setup",
                    code=2,
                )
        if not defaults.get("projectKey"):
            raise PlanKeeperCliError(
                "jira config defaults missing projectKey — run /plan-jira setup",
                code=2,
            )
