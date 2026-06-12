"""Linear GraphQL client: viewer/teams/projects/labels/users queries, the
metadata cache refresh, and issue create/update used by the push flow.
"""
import sys
from typing import Callable, Optional, TypeVar

from plan_keeper.config import load_config, save_config
from plan_keeper.dates import _iso_utc_now
from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.http import http_post_json
from plan_keeper.naming import derive_repo
from plan_keeper.types import (
    JsonObject,
    LinearIssueInput,
    LinearLabel,
    LinearProject,
    LinearSection,
    LinearTeam,
    LinearUser,
)

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
LINEAR_DESCRIPTION_LIMIT = 65_000

# Row type produced by a paginated query's transform_node callable.
_Row = TypeVar("_Row")


def linear_viewer(api_key: str) -> JsonObject:
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
    transform_node: Callable[[JsonObject], _Row],
) -> list[_Row]:
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
    all_nodes: list[_Row] = []
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


def linear_teams(api_key: str) -> list[LinearTeam]:
    """Return all Linear teams as ``{id, name}`` rows."""
    query = (
        "query Teams($after: String) {"
        "  teams(first: 100, after: $after) {"
        "    nodes { id name }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )

    def _team(n: JsonObject) -> LinearTeam:
        return {"id": n["id"], "name": n["name"]}

    return _linear_paginated(api_key, query, "teams", _team)


def linear_projects(api_key: str) -> list[LinearProject]:
    """Return all Linear projects as ``{id, name, teamIds}`` rows."""
    query = (
        "query Projects($after: String) {"
        "  projects(first: 100, after: $after) {"
        "    nodes { id name teams(first: 10) { nodes { id } } }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )

    def _project(n: JsonObject) -> LinearProject:
        return {
            "id": n["id"],
            "name": n["name"],
            "teamIds": [t["id"] for t in n["teams"]["nodes"]],
        }

    return _linear_paginated(api_key, query, "projects", _project)


def linear_labels(api_key: str) -> list[LinearLabel]:
    """Return all Linear issue labels as ``{id, name, teamId}`` rows (teamId None when workspace-level)."""
    query = (
        "query Labels($after: String) {"
        "  issueLabels(first: 100, after: $after) {"
        "    nodes { id name team { id } }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )

    def _label(n: JsonObject) -> LinearLabel:
        return {
            "id": n["id"],
            "name": n["name"],
            "teamId": n["team"]["id"] if n.get("team") else None,
        }

    return _linear_paginated(api_key, query, "issueLabels", _label)


def linear_users(api_key: str) -> list[LinearUser]:
    """Return all Linear users as ``{id, name, email}`` rows."""
    query = (
        "query Users($after: String) {"
        "  users(first: 100, after: $after) {"
        "    nodes { id name email }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )

    def _user(n: JsonObject) -> LinearUser:
        return {"id": n["id"], "name": n["name"], "email": n["email"]}

    return _linear_paginated(api_key, query, "users", _user)


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
    # setdefault inserts {"linear": {}} when absent AND returns the same object
    # now stored in config; the in-place mutation below (and at save_config) thus
    # persists. Rewriting as config.get("linear", {}) would mutate a throwaway
    # default that is never written back.
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
    team_id = defaults.get("teamId")
    if team_id and team_id not in team_ids:
        warnings.append(
            f"defaults.teamId={team_id!r} ({defaults.get('teamName', '?')!r}) "
            "no longer exists in Linear"
        )
    project_ids = {p["id"] for p in projects}
    project_id = defaults.get("projectId")
    if project_id and project_id not in project_ids:
        warnings.append(
            f"defaults.projectId={project_id!r} ({defaults.get('projectName', '?')!r}) "
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


def linear_create_issue(api_key: str, input_dict: LinearIssueInput) -> JsonObject:
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


def _push_linear(
    section: LinearSection, title: str, description: str,
    meta: dict[str, str], force_new: bool,
) -> JsonObject:
    """Create or update a Linear issue for this plan.

    Updates the existing ticket when ``meta`` carries a "Linear Ticket" id and
    ``force_new`` is False; otherwise creates a new issue from the configured
    defaults.
    """
    # apiKey/defaults/teamId are validated present by _validate_config_for_push
    # before this runs; the TypedDict still marks every key optional because a
    # half-configured section is a valid on-disk state. Read through .get and
    # narrow so a hand-edited config surfaces a clean error, not a KeyError.
    api_key = section.get("apiKey")
    defaults = section.get("defaults", {})
    if not api_key:
        raise PlanKeeperCliError("linear config missing apiKey", code=2)
    existing = (meta.get("Linear Ticket") or "").strip()
    if existing and not force_new:
        return _push_linear_update(api_key, existing, title, description)
    team_id = defaults.get("teamId")
    input_dict: LinearIssueInput = {
        "title": title,
        "description": description,
    }
    if team_id:
        input_dict["teamId"] = team_id
    project_id = defaults.get("projectId")
    if project_id:
        input_dict["projectId"] = project_id
    assignee_id = defaults.get("assigneeId")
    if assignee_id:
        input_dict["assigneeId"] = assignee_id
    label_ids = defaults.get("labelIds")
    if label_ids:
        input_dict["labelIds"] = list(label_ids)
    issue = linear_create_issue(api_key, input_dict)
    return {
        "action": "create",
        "system": "linear",
        "id": issue["identifier"],
        "url": issue["url"],
        "title": issue["title"],
    }


def _push_linear_update(api_key: str, identifier: str, title: str, description: str) -> JsonObject:
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
