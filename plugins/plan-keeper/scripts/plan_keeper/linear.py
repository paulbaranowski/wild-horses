"""Linear GraphQL client: viewer/teams/projects/labels/users queries, the
metadata cache refresh, and issue create/update used by the push flow.
"""
import sys
from typing import Callable, Optional

from plan_keeper.config import load_config, save_config
from plan_keeper.dates import _iso_utc_now
from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.http import http_post_json
from plan_keeper.naming import derive_repo

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
LINEAR_DESCRIPTION_LIMIT = 65_000


def linear_viewer(api_key: str) -> dict:
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
    transform_node: Callable[[dict], dict],
) -> list[dict]:
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
    all_nodes: list[dict] = []
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


def linear_teams(api_key: str) -> list[dict]:
    query = (
        "query Teams($after: String) {"
        "  teams(first: 100, after: $after) {"
        "    nodes { id name }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )
    return _linear_paginated(api_key, query, "teams", lambda n: {"id": n["id"], "name": n["name"]})


def linear_projects(api_key: str) -> list[dict]:
    query = (
        "query Projects($after: String) {"
        "  projects(first: 100, after: $after) {"
        "    nodes { id name teams(first: 10) { nodes { id } } }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )
    return _linear_paginated(
        api_key,
        query,
        "projects",
        lambda n: {
            "id": n["id"],
            "name": n["name"],
            "teamIds": [t["id"] for t in n["teams"]["nodes"]],
        },
    )


def linear_labels(api_key: str) -> list[dict]:
    query = (
        "query Labels($after: String) {"
        "  issueLabels(first: 100, after: $after) {"
        "    nodes { id name team { id } }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )
    return _linear_paginated(
        api_key,
        query,
        "issueLabels",
        lambda n: {
            "id": n["id"],
            "name": n["name"],
            "teamId": n["team"]["id"] if n.get("team") else None,
        },
    )


def linear_users(api_key: str) -> list[dict]:
    query = (
        "query Users($after: String) {"
        "  users(first: 100, after: $after) {"
        "    nodes { id name email }"
        "    pageInfo { endCursor hasNextPage }"
        "  }"
        "}"
    )
    return _linear_paginated(
        api_key,
        query,
        "users",
        lambda n: {"id": n["id"], "name": n["name"], "email": n["email"]},
    )


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
    if defaults.get("teamId") and defaults["teamId"] not in team_ids:
        warnings.append(
            f"defaults.teamId={defaults['teamId']!r} ({defaults.get('teamName', '?')!r}) "
            "no longer exists in Linear"
        )
    project_ids = {p["id"] for p in projects}
    if defaults.get("projectId") and defaults["projectId"] not in project_ids:
        warnings.append(
            f"defaults.projectId={defaults['projectId']!r} ({defaults.get('projectName', '?')!r}) "
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


def linear_create_issue(api_key: str, input_dict: dict) -> dict:
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


def _push_linear(section: dict, title: str, description: str, meta: dict, force_new: bool) -> dict:
    api_key = section["apiKey"]
    defaults = section["defaults"]
    existing = (meta.get("Linear Ticket") or "").strip()
    if existing and not force_new:
        return _push_linear_update(api_key, existing, title, description)
    input_dict = {
        "title": title,
        "description": description,
        "teamId": defaults["teamId"],
    }
    if defaults.get("projectId"):
        input_dict["projectId"] = defaults["projectId"]
    if defaults.get("assigneeId"):
        input_dict["assigneeId"] = defaults["assigneeId"]
    if defaults.get("labelIds"):
        input_dict["labelIds"] = list(defaults["labelIds"])
    issue = linear_create_issue(api_key, input_dict)
    return {
        "action": "create",
        "system": "linear",
        "id": issue["identifier"],
        "url": issue["url"],
        "title": issue["title"],
    }


def _push_linear_update(api_key: str, identifier: str, title: str, description: str) -> dict:
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
