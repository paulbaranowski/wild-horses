"""Single chokepoint for outbound JSON HTTP, shared by the Linear and Jira
clients. Maps urllib exceptions to PlanKeeperCliError with the documented
exit codes so callers never leak a raw stack trace.
"""
import json
import urllib.error
import urllib.request

from plan_keeper.errors import PlanKeeperCliError

HTTP_TIMEOUT = 30


def http_post_json(
    url: str,
    payload: dict,
    headers: dict[str, str],
) -> dict:
    """POST a JSON body, return the decoded JSON response.

    Single chokepoint for all outbound HTTP. Maps urllib exceptions to
    PlanKeeperCliError with the documented exit codes:
        3 — 401/403 auth failures
        4 — DNS/connection/timeout failures
        5 — non-2xx HTTP responses with the body in the error message
    """
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PlanKeeperCliError(f"auth failure ({e.code}): {e.reason}", code=3)
        raise PlanKeeperCliError(f"HTTP {e.code}: {e.reason}", code=5)
    except urllib.error.URLError as e:
        raise PlanKeeperCliError(f"network error: {e.reason}", code=4)
    except Exception as e:
        raise PlanKeeperCliError(f"unexpected error: {e}", code=5)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise PlanKeeperCliError(f"non-JSON response: {e}; body={body[:200]!r}", code=5)


def http_get_json(url: str, headers: dict[str, str]) -> dict:
    """GET a URL, return the decoded JSON response. Same error mapping as http_post_json."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PlanKeeperCliError(f"auth failure ({e.code}): {e.reason}", code=3)
        raise PlanKeeperCliError(f"HTTP {e.code}: {e.reason}", code=5)
    except urllib.error.URLError as e:
        raise PlanKeeperCliError(f"network error: {e.reason}", code=4)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise PlanKeeperCliError(f"non-JSON response: {e}; body={body[:200]!r}", code=5)
