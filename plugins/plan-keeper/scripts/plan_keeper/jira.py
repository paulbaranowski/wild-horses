"""Jira REST client: viewer/projects/components/users/issuetypes queries, the
ADF helper, the metadata cache refresh, and issue create/update used by push.
"""
import base64
import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlencode

from plan_keeper.config import load_config, save_config
from plan_keeper.dates import _iso_utc_now
from plan_keeper.errors import PlanKeeperCliError
from plan_keeper.http import HTTP_TIMEOUT, http_get_json
from plan_keeper.naming import derive_repo


def refresh_jira_cache(site: str, email: str, api_token: str) -> list[str]:
    """Fetch all Jira metadata and write into config['jira']['cache'].

    Returns a list of warning strings (e.g., when defaults reference keys/IDs
    that aren't in the new cache). Empty list on clean refresh.
    """
    projects = jira_projects(site, email, api_token)
    all_components: list[dict] = []
    all_users: list[dict] = []
    all_issuetypes: list[dict] = []
    for p in projects:
        all_components.extend(jira_components(site, email, api_token, p["key"]))
        all_users.extend(jira_users(site, email, api_token, p["key"]))
        all_issuetypes.extend(jira_issuetypes(site, email, api_token, p["id"]))
    repo = derive_repo(None)
    config = load_config(repo)
    section = config.setdefault("jira", {})
    section["cache"] = {
        "refreshedAt": _iso_utc_now(),
        "projects": projects,
        "components": all_components,
        "users": all_users,
        "issueTypes": all_issuetypes,
    }
    save_config(repo, config)
    warnings: list[str] = []
    defaults = section.get("defaults", {})
    project_keys = {p["key"] for p in projects}
    if defaults.get("projectKey") and defaults["projectKey"] not in project_keys:
        warnings.append(
            f"defaults.projectKey={defaults['projectKey']!r} no longer exists in Jira"
        )
    component_ids = {c["id"] for c in all_components}
    for cid in defaults.get("componentIds", []):
        if cid not in component_ids:
            warnings.append(f"defaults.componentIds contains {cid!r} which is no longer in Jira")
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    return warnings


_JIRA_SITE_RE = re.compile(r"^[A-Za-z0-9.\-]+(?::\d+)?$")


def _validate_jira_site(site: str) -> str:
    """Reject anything that isn't a bare hostname (optionally with :port).

    Inputs like `https://herds.atlassian.net`, `herds.atlassian.net/path`,
    or `herds.atlassian.net@evil.test` would break URL construction or
    redirect the Basic-auth header to the wrong host. Accept only
    `<host>` or `<host>:<port>`.
    """
    if not site or not isinstance(site, str):
        raise PlanKeeperCliError("jira site must be a non-empty string", code=2)
    if not _JIRA_SITE_RE.match(site):
        raise PlanKeeperCliError(
            f"jira site must be a bare hostname (no scheme, path, userinfo, or whitespace); got {site!r}",
            code=2,
        )
    return site


def _jira_auth_header(email: str, api_token: str) -> str:
    raw = f"{email}:{api_token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def jira_viewer(site: str, email: str, api_token: str) -> dict:
    url = f"https://{site}/rest/api/3/myself"
    return http_get_json(url, {"Authorization": _jira_auth_header(email, api_token)})


def _jira_paginated(
    site: str, email: str, api_token: str,
    path: str, query_params: dict[str, str],
) -> list[dict]:
    """Walk Jira's startAt/maxResults pagination and return all `values`."""
    all_values: list[dict] = []
    start_at = 0
    page_size = 50
    while True:
        params = {**query_params, "startAt": str(start_at), "maxResults": str(page_size)}
        url = f"https://{site}/rest/api/3/{path}?{urlencode(params)}"
        resp = http_get_json(url, {"Authorization": _jira_auth_header(email, api_token)})
        page_values = resp.get("values", [])
        all_values.extend(page_values)
        if "isLast" in resp:
            if resp["isLast"]:
                break
        elif len(page_values) < page_size:
            break
        start_at += page_size
    return all_values


def jira_projects(site: str, email: str, api_token: str) -> list[dict]:
    raw = _jira_paginated(site, email, api_token, "project/search", {})
    return [{"key": p["key"], "id": p["id"], "name": p["name"]} for p in raw]


def jira_components(site: str, email: str, api_token: str, project_key: str) -> list[dict]:
    url = f"https://{site}/rest/api/3/project/{project_key}/components"
    raw = http_get_json(url, {"Authorization": _jira_auth_header(email, api_token)})
    # Endpoint returns a flat list, not a paginated object.
    if not isinstance(raw, list):
        raise PlanKeeperCliError(f"unexpected Jira components response: {raw!r}", code=5)
    return [{"id": c["id"], "name": c["name"], "projectKey": project_key} for c in raw]


def jira_users(site: str, email: str, api_token: str, project_key: str) -> list[dict]:
    all_users: list[dict] = []
    start_at = 0
    page_size = 50
    while True:
        url = (
            f"https://{site}/rest/api/3/user/assignable/multiProjectSearch?"
            + urlencode({
                "projectKeys": project_key,
                "startAt": str(start_at),
                "maxResults": str(page_size),
            })
        )
        raw = http_get_json(url, {"Authorization": _jira_auth_header(email, api_token)})
        if not isinstance(raw, list):
            raise PlanKeeperCliError(f"unexpected Jira users response: {raw!r}", code=5)
        all_users.extend(raw)
        if len(raw) < page_size:
            break
        start_at += page_size
    return [
        {"accountId": u["accountId"], "name": u["displayName"],
         "email": u.get("emailAddress", "")}
        for u in all_users
    ]


def jira_issuetypes(site: str, email: str, api_token: str, project_id: str) -> list[dict]:
    url = (
        f"https://{site}/rest/api/3/issuetype/project?"
        + urlencode({"projectId": project_id})
    )
    raw = http_get_json(url, {"Authorization": _jira_auth_header(email, api_token)})
    if not isinstance(raw, list):
        raise PlanKeeperCliError(f"unexpected Jira issuetypes response: {raw!r}", code=5)
    return [{"id": t["id"], "name": t["name"], "projectId": project_id} for t in raw]


def _adf_paragraph(text: str) -> dict:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        ],
    }


def jira_create_issue(
    site: str, email: str, api_token: str, fields: dict,
) -> dict:
    url = f"https://{site}/rest/api/3/issue"
    req = urllib.request.Request(
        url,
        data=json.dumps({"fields": fields}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": _jira_auth_header(email, api_token),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PlanKeeperCliError(f"Jira auth failure ({e.code})", code=3)
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        raise PlanKeeperCliError(f"Jira HTTP {e.code}: {body[:200]}", code=5)
    except urllib.error.URLError as e:
        raise PlanKeeperCliError(f"Jira network error: {e.reason}", code=4)


def jira_update_issue(
    site: str, email: str, api_token: str, key: str, fields: dict,
) -> None:
    url = f"https://{site}/rest/api/3/issue/{key}"
    req = urllib.request.Request(
        url,
        data=json.dumps({"fields": fields}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": _jira_auth_header(email, api_token),
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT):
            return  # 204 No Content on success
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PlanKeeperCliError(f"Jira auth failure ({e.code})", code=3)
        if e.code == 404:
            raise PlanKeeperCliError(f"Jira ticket {key} not found", code=5)
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        raise PlanKeeperCliError(f"Jira HTTP {e.code}: {body[:200]}", code=5)
    except urllib.error.URLError as e:
        raise PlanKeeperCliError(f"Jira network error: {e.reason}", code=4)


def _push_jira(section: dict, title: str, description: str, meta: dict, force_new: bool) -> dict:
    site = _validate_jira_site(section["site"])
    email = section["email"]
    token = section["apiToken"]
    defaults = section["defaults"]
    existing = (meta.get("Jira Ticket") or "").strip()
    adf = _adf_paragraph(description)
    if existing and not force_new:
        key = existing
        jira_update_issue(
            site, email, token, key,
            {"summary": title, "description": adf},
        )
        return {
            "action": "update",
            "system": "jira",
            "id": key,
            "url": f"https://{site}/browse/{key}",
            "title": title,
        }
    fields = {
        "project": {"key": defaults["projectKey"]},
        "summary": title,
        "description": adf,
        "issuetype": {"name": defaults.get("issueType", "Task")},
    }
    if defaults.get("componentIds"):
        fields["components"] = [{"id": cid} for cid in defaults["componentIds"]]
    if defaults.get("assigneeAccountId"):
        fields["assignee"] = {"accountId": defaults["assigneeAccountId"]}
    if defaults.get("labels"):
        fields["labels"] = list(defaults["labels"])
    created = jira_create_issue(site, email, token, fields)
    key = created["key"]
    return {
        "action": "create",
        "system": "jira",
        "id": key,
        "url": f"https://{site}/browse/{key}",
        "title": title,
    }


def _resolve_jira_project_id(
    site: str, email: str, api_token: str, project_key: str,
) -> str:
    """Look up the numeric Jira project id for a given key.

    `jira_issuetypes` calls `/issuetype/project?projectId=<id>` which requires
    the numeric id, not the human-readable key. Other per-project endpoints
    (`jira_components`, `jira_users`) accept the key directly. This helper
    bridges the gap by fetching projects and matching on key.
    """
    for p in jira_projects(site, email, api_token):
        if p["key"] == project_key:
            return p["id"]
    raise PlanKeeperCliError(
        f"Jira project key {project_key!r} not found", code=2,
    )
