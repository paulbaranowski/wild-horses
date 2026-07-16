#!/usr/bin/env python3
"""pr_babysit_cli.py - single-file CLI backing the pr-babysit skill.

Consolidates the former six bash scripts into five subcommands:

    review        (was unresolvedPrComments.sh)  fetch + shape PR review data
    failed-logs   (was fetchFailedLogs.sh)       stream failed-check logs
    commit-push   (was commitAndPush.sh)         stage named files, commit, push
    reply         (was postSentinelReply.sh)     threaded review-thread reply
    comment       (was postSentinelPrComment.sh) top-level PR comment

Stdlib only (argparse, json, hashlib, subprocess, re). GitHub access is via the
`gh` CLI, exactly as the bash did. Every stdout/stderr/exit-code contract the
skill parses is preserved:

  - `review` writes errors as {"error": ...} to STDOUT (not stderr) and exits 1,
    because the skill reads `.error` out of the same JSON it parses.
  - the other four write errors as {"error": ...} to stderr; usage errors exit 2,
    runtime/infra errors exit 1.

The sentinel literals below are dedupe-matching tokens against comments already
posted on GitHub. Do NOT edit them (including the embedded wild-horses@0.2.1);
changing a byte breaks dedupe on every existing PR.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

# --- Sentinel constants (formerly _sentinel.sh; frozen matching tokens) ------

SENTINEL_PREFIX = "pr-babysit:addressed v1 "
LEGACY_CB_SENTINEL_PREFIX = "cb-babysit:addressed v1 "
LEGACY_BABYSIT_PR_SENTINEL_PREFIX = "babysit-pr:addressed v1 "
SENTINEL = "<sub>\U0001F916 <code>pr-babysit:addressed v1 wild-horses@0.2.1</code></sub>"

# Bot author allowlist - fallback for GitHub Apps that post via a User-type
# service account (GraphQL __typename misses those). Union with the __typename
# == "Bot" signal, never an intersection.
BOTS = [
    "coderabbitai", "coderabbitai[bot]",
    "mendral-app", "mendral-app[bot]",
    "dependabot", "dependabot[bot]",
    "github-actions", "github-actions[bot]",
    "github-advanced-security", "github-advanced-security[bot]",
    "renovate", "renovate[bot]", "renovate-bot",
    "pre-commit-ci", "pre-commit-ci[bot]",
    "codecov", "codecov[bot]",
    "sonarcloud", "sonarcloud[bot]",
]

SECURITY_LOGINS = ("github-advanced-security", "github-advanced-security[bot]")


# --- gh plumbing -------------------------------------------------------------

# Wall-clock cap for a single `gh` call so a stuck network or auth prompt can't
# wedge an unattended pr-babysit loop forever. Only `gh` calls are timed; local
# git operations (commit hooks, large pushes) legitimately run long and are left
# to complete on their own.
GH_TIMEOUT_SECONDS = 120


class GhError(Exception):
    """A `gh` invocation failed; message is the combined stderr/stdout."""


def _run(args, *, capture=True, stdin=None, timeout=None):
    return subprocess.run(
        args,
        input=stdin,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        timeout=timeout,
    )


def gh_text(args, *, stdin=None):
    """Run `gh <args>`, return stdout. Raise GhError on non-zero exit or timeout."""
    try:
        proc = _run(["gh", *args], stdin=stdin, timeout=GH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise GhError(f"gh timed out after {GH_TIMEOUT_SECONDS}s: gh {' '.join(args[:2])}")
    if proc.returncode != 0:
        raise GhError((proc.stderr or proc.stdout or "").strip())
    return proc.stdout


def gh_json(args, *, stdin=None):
    """Run `gh <args>` and parse stdout as JSON."""
    out = gh_text(args, stdin=stdin)
    return json.loads(out) if out.strip() else None


def have(cmd):
    return shutil.which(cmd) is not None


# --- pure helpers ------------------------------------------------------------

def jq_or(*vals):
    """Mimic jq's left-associative `a // b // ...`: the first value that is
    neither None nor False, else the final argument (which jq returns verbatim,
    so `x // false` yields False, not None)."""
    for v in vals:
        if v is not None and v is not False:
            return v
    return vals[-1] if vals else None


def normalize_body(body):
    """Collapse every run of ASCII whitespace to one space, then trim.

    Byte-faithful to the bash `tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//'`
    (C-locale [:space:] is exactly [ \\t\\n\\r\\f\\v]).
    """
    return re.sub(r"[ \t\n\r\f\v]+", " ", body).strip(" ")


def fingerprint_body(body):
    """First 16 hex chars of sha256(normalize(body))."""
    return hashlib.sha256(normalize_body(body).encode("utf-8")).hexdigest()[:16]


def is_bot(login, typename):
    return typename == "Bot" or (login in BOTS)


def is_sentinel_body(body):
    body = body or ""
    return (
        SENTINEL_PREFIX in body
        or LEGACY_CB_SENTINEL_PREFIX in body
        or LEGACY_BABYSIT_PR_SENTINEL_PREFIX in body
    )


def ensure_sentinel(body):
    """Append SENTINEL on its own paragraph unless a sentinel is already present."""
    if is_sentinel_body(body):
        return body
    return f"{body}\n\n{SENTINEL}"


def _author_login(node):
    author = node.get("author") or {}
    return jq_or(author.get("login"), "deleted-user")


def _author_typename(node):
    author = node.get("author") or {}
    return author.get("__typename")


def _node_is_bot(node):
    author = node.get("author") or {}
    return is_bot(author.get("login") or "", author.get("__typename") or "")


def _node_is_sentinel(node):
    return is_sentinel_body(node.get("body") or "")


# --- review: the big transform (formerly the jq in unresolvedPrComments.sh) ---

def derive_thread(node):
    """Shape one unresolved reviewThread node into the emitted thread object."""
    raw_comments = (node.get("comments") or {}).get("nodes") or []
    first = raw_comments[0] if raw_comments else None

    comments = [
        {
            "id": c.get("id"),
            "databaseId": c.get("databaseId"),
            "author": _author_login(c),
            "authorType": _author_typename(c),
            "body": c.get("body"),
            "createdAt": c.get("createdAt"),
            "file": c.get("path"),
            "line": jq_or(c.get("line"), c.get("originalLine")),
            "isBabysitSentinel": _node_is_sentinel(c),
            "isKnownBot": _node_is_bot(c),
        }
        for c in raw_comments
    ]

    def max_created(pred):
        stamps = [c.get("createdAt") for c in raw_comments if pred(c)]
        stamps = [s for s in stamps if s is not None]
        return max(stamps) if stamps else None

    last_sentinel = max_created(_node_is_sentinel)
    last_human = max_created(
        lambda c: (not _node_is_sentinel(c)) and (not _node_is_bot(c)))
    last_bot = max_created(
        lambda c: (not _node_is_sentinel(c)) and _node_is_bot(c))

    if last_sentinel is None:
        post_bot = []
        post_human = []
    else:
        post_bot = [
            {
                "id": c.get("id"),
                "createdAt": c.get("createdAt"),
                "author": _author_login(c),
                "authorType": _author_typename(c),
                "body": c.get("body"),
            }
            for c in raw_comments
            if _node_is_bot(c) and not _node_is_sentinel(c)
            and (c.get("createdAt") or "") > last_sentinel
        ]
        post_human = [
            {
                "id": c.get("id"),
                "createdAt": c.get("createdAt"),
                "author": _author_login(c),
                "body": c.get("body"),
            }
            for c in raw_comments
            if (not _node_is_bot(c)) and not _node_is_sentinel(c)
            and (c.get("createdAt") or "") > last_sentinel
        ]

    if last_sentinel is None:
        state = "active"
    elif len(post_human) > 0:
        state = "active"
    elif len(post_bot) > 0:
        state = "uncertain"
    else:
        state = "addressed"

    return {
        "threadId": node.get("id"),
        "isResolved": node.get("isResolved"),
        "replyToCommentDatabaseId": (first.get("databaseId") if first else None),
        "file": (first.get("path") if first else None),
        "line": (jq_or(first.get("line"), first.get("originalLine")) if first else None),
        "commentsTruncated": jq_or((node.get("comments") or {}).get("pageInfo", {}).get("hasNextPage"), False),
        "comments": comments,
        "lastBabysitSentinelAt": last_sentinel,
        "lastHumanCommentAt": last_human,
        "lastBotCommentAt": last_bot,
        "postSentinelBotComments": post_bot,
        "postSentinelHumanComments": post_human,
        "activityState": state,
    }


def _pull_request(response):
    return (((response or {}).get("data") or {}).get("repository") or {}).get("pullRequest")


def _derive_threads(response):
    pr = _pull_request(response) or {}
    nodes = (pr.get("reviewThreads") or {}).get("nodes") or []
    return [derive_thread(n) for n in nodes if n.get("isResolved") is False]


def _all_unresolved(threads):
    """Flattened non-sentinel comments from every non-addressed thread."""
    out = []
    for t in threads:
        if t["activityState"] == "addressed":
            continue
        for c in t["comments"]:
            if c["isBabysitSentinel"]:
                continue
            out.append({
                "author": c["author"],
                "body": c["body"],
                "createdAt": c["createdAt"],
                "file": c["file"],
                "line": c["line"],
            })
    return out


def extract_alert_numbers(response):
    """Code-scanning alert numbers referenced by security-bot comments."""
    threads = _derive_threads(response)
    nums = set()
    for c in _all_unresolved(threads):
        if c["author"] in SECURITY_LOGINS:
            m = re.search(r"/code-scanning/([0-9]+)", c["body"] or "")
            if m:
                nums.add(m.group(1))
    return sorted(nums)


def transform_review(response, pr_number, owner, repo, fixed_alert_ids):
    """Build the full review JSON document. Pure; `fixed_alert_ids` is the set
    of code-scanning alert numbers already resolved (looked up by the caller)."""
    pr = _pull_request(response) or {}
    fixed = set(fixed_alert_ids or [])

    threads = _derive_threads(response)
    all_unresolved = _all_unresolved(threads)

    def not_fixed_alert(c):
        if c["author"] in SECURITY_LOGINS:
            m = re.search(r"/code-scanning/([0-9]+)", c["body"] or "")
            if m and m.group(1) in fixed:
                return False
        return True

    unresolved_comments = [c for c in all_unresolved if not_fixed_alert(c)]

    # Raw review-body comments from known bots, each fingerprinted.
    review_bodies = []
    for r in (pr.get("reviews") or {}).get("nodes") or []:
        body = r.get("body") or ""
        if body == "":
            continue
        if not _node_is_bot(r):
            continue
        review_bodies.append({
            "author": _author_login(r),
            "authorType": _author_typename(r),
            "createdAt": r.get("createdAt"),
            "body": r.get("body"),
            "fingerprint": fingerprint_body(r.get("body") or ""),
        })

    # All top-level issue comments, each fingerprinted.
    issue_comments = []
    for c in (pr.get("comments") or {}).get("nodes") or []:
        issue_comments.append({
            "id": c.get("id"),
            "databaseId": c.get("databaseId"),
            "author": _author_login(c),
            "authorType": _author_typename(c),
            "body": c.get("body"),
            "createdAt": c.get("createdAt"),
            "url": c.get("url"),
            "isBabysitSentinel": _node_is_sentinel(c),
            "isKnownBot": _node_is_bot(c),
            "fingerprint": fingerprint_body(c.get("body") or ""),
        })

    prior_sentinels = [c for c in issue_comments if c["isBabysitSentinel"]]
    prior_blob = "\n".join(c["body"] or "" for c in prior_sentinels)

    active_issue_comments = [
        c for c in issue_comments
        if (not c["isBabysitSentinel"])
        and (not c["isKnownBot"])
        and (c["fingerprint"] not in prior_blob)
    ]

    active_threads = [t for t in threads if t["activityState"] != "addressed"]
    uncertain_threads = [t for t in threads if t["activityState"] == "uncertain"]

    truncated = []
    if (pr.get("reviewThreads") or {}).get("pageInfo", {}).get("hasNextPage"):
        truncated.append("reviewThreads")
    if any(t["commentsTruncated"] for t in threads):
        truncated.append("thread-comments")
    if (pr.get("reviews") or {}).get("pageInfo", {}).get("hasNextPage"):
        truncated.append("reviews")
    if (pr.get("comments") or {}).get("pageInfo", {}).get("hasNextPage"):
        truncated.append("issueComments")

    return {
        "activeIssueComments": active_issue_comments,
        "activeThreads": active_threads,
        "issueComments": issue_comments,
        "owner": owner,
        "prNumber": pr_number,
        "priorBabysitSentinels": prior_sentinels,
        "repo": repo,
        "reviewBodyComments": review_bodies,
        "sentinel": SENTINEL,
        "threads": threads,
        "title": pr.get("title"),
        "totalActiveIssueComments": len(active_issue_comments),
        "totalActiveThreads": len(active_threads),
        "totalReviewBodyComments": len(review_bodies),
        "totalUncertainThreads": len(uncertain_threads),
        "totalUnresolvedComments": len(unresolved_comments),
        "truncated": truncated,
        "uncertainThreads": uncertain_threads,
        "unresolvedComments": unresolved_comments,
        "url": pr.get("url"),
    }


GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      title
      url
      reviewThreads(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          id
          isResolved
          comments(first: 100) {
            pageInfo { hasNextPage }
            nodes {
              id
              databaseId
              body
              path
              line
              originalLine
              createdAt
              author {
                login
                __typename
              }
            }
          }
        }
      }
      reviews(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          body
          author { login __typename }
          createdAt
        }
      }
      comments(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          id
          databaseId
          body
          createdAt
          url
          author { login __typename }
        }
      }
    }
  }
}"""


# --- review subcommand -------------------------------------------------------

class ReviewError(Exception):
    """review-path error; renders as {"error": ...} on STDOUT, exit 1."""


def _repo_info():
    try:
        info = gh_json(["repo", "view", "--json", "owner,name"])
    except GhError as e:
        raise ReviewError(
            "Could not determine repository. Are you in a git repo with a GitHub remote?") from e
    owner = ((info or {}).get("owner") or {}).get("login") or ""
    name = (info or {}).get("name") or ""
    if not owner or not name:
        raise ReviewError("Failed to parse repository info from gh CLI output.")
    return owner, name


def _resolve_pr_number(arg):
    if arg:
        if not re.fullmatch(r"[0-9]+", arg):
            raise ReviewError(f"Invalid PR number: {arg}")
        return int(arg)
    try:
        pr = gh_json(["pr", "view", "--json", "number"])
    except GhError as e:
        raise ReviewError("No PR found for current branch. Provide PR number as argument.") from e
    num = (pr or {}).get("number")
    if not num:
        raise ReviewError("No PR found for current branch. Provide PR number as argument.")
    return int(num)


def cmd_review(args):
    try:
        if not have("gh"):
            raise ReviewError("gh CLI not found. Install from https://cli.github.com")
        try:
            gh_text(["api", "user", "--jq", ".login"])
        except GhError as e:
            raise ReviewError("Not authenticated with GitHub. Run: gh auth login") from e

        pr_number = _resolve_pr_number(args.pr)
        owner, repo = _repo_info()

        try:
            response = gh_json([
                "api", "graphql",
                "-f", f"query={GRAPHQL_QUERY}",
                "-f", f"owner={owner}",
                "-f", f"repo={repo}",
                "-F", f"pr={pr_number}",
            ])
        except GhError as e:
            raise ReviewError(f"GraphQL query failed: {e}") from e

        if _pull_request(response) is None:
            repository = ((response or {}).get("data") or {}).get("repository")
            if repository is None:
                raise ReviewError(f"Repository {owner}/{repo} not found or not accessible.")
            raise ReviewError(f"PR #{pr_number} not found or not accessible.")

        # Resolve which referenced code-scanning alerts are already fixed.
        fixed = set()
        for n in extract_alert_numbers(response):
            try:
                alert = gh_json(["api", f"repos/{owner}/{repo}/code-scanning/alerts/{n}"])
            except GhError:
                continue
            if ((alert or {}).get("most_recent_instance") or {}).get("state") == "fixed":
                fixed.add(n)

        doc = transform_review(response, pr_number, owner, repo, fixed)
        print(json.dumps(doc, indent=2))
        return 0
    except ReviewError as e:
        # review errors go to STDOUT (the skill reads .error from the JSON).
        print(json.dumps({"error": str(e)}))
        return 1


# --- failed-logs subcommand --------------------------------------------------

def _normalized_bucket(entry):
    b = entry.get("bucket") or ""
    c = (entry.get("conclusion") or "").lower()
    s = (entry.get("state") or "").lower()
    if b == "fail" or c == "failure" or s == "failure" or s == "error":
        return "fail"
    return ""


def partition_failing_checks(rollup):
    """Return (run_ids, external_lines) for the failing entries in a rollup.

    run_ids: sorted-unique GitHub Actions run ids (from actions/runs/<id> URLs).
    external_lines: `# --- external check: <name> (<url|no URL>) ---` lines for
    every failing check that is not a GitHub Actions run.
    """
    failing = [
        (jq_or(e.get("name"), "unknown"), e.get("detailsUrl") or "")
        for e in (rollup or [])
        if _normalized_bucket(e) == "fail"
    ]
    run_ids = []
    external_lines = []
    for name, url in failing:
        m = re.match(r"^https://github\.com/[^/]+/[^/]+/actions/runs/([0-9]+)", url)
        if m:
            run_ids.append(m.group(1))
        else:
            external_lines.append(
                f"# --- external check: {name} ({url or 'no URL'}) ---")
    run_ids = sorted(set(run_ids))
    return run_ids, external_lines, bool(failing)


def cmd_failed_logs(args):
    # Validate the argument BEFORE any environment probe, so malformed input is
    # a deterministic exit 2 regardless of whether gh is installed/authenticated
    # (otherwise a bad PR number returns 1 on an unauthenticated runner).
    if args.pr is not None and not re.fullmatch(r"[0-9]+", args.pr):
        print(json.dumps({"error": f"invalid PR number: {args.pr}"}), file=sys.stderr)
        return 2

    if not have("gh"):
        print(json.dumps({"error": "gh CLI not found"}), file=sys.stderr)
        return 1
    try:
        gh_text(["api", "user", "--jq", ".login"])
    except GhError:
        print(json.dumps({"error": "not authenticated with GitHub - run: gh auth login"}),
              file=sys.stderr)
        return 1

    if args.pr is not None:
        view = ["pr", "view", args.pr, "--json", "statusCheckRollup", "--jq", ".statusCheckRollup"]
        fail_msg = f"could not fetch PR {args.pr}"
    else:
        view = ["pr", "view", "--json", "statusCheckRollup", "--jq", ".statusCheckRollup"]
        fail_msg = "no PR for current branch"
    try:
        rollup = gh_json(view)
    except GhError:
        print(json.dumps({"error": fail_msg}), file=sys.stderr)
        return 1

    run_ids, external_lines, any_failing = partition_failing_checks(rollup)

    if not any_failing:
        print("# pr-babysit: no failing checks")
        return 0

    print("# pr-babysit: failing checks")

    for run_id in run_ids:
        try:
            jobs = gh_json(["run", "view", run_id, "--json", "jobs"])
        except GhError as e:
            # Never swallow a retrieval failure: the workflow diagnoses CI from
            # this output, so a silent skip would read as "no logs to inspect"
            # when the truth is "logs could not be fetched". Surface it.
            print("")
            print(f"# --- run={run_id}: ERROR fetching jobs ({e}) ---")
            continue
        for job in (jobs or {}).get("jobs", []):
            if job.get("conclusion") != "failure":
                continue
            job_id = job.get("databaseId")
            print("")
            print(f"# --- run={run_id} job={job_id} ---")
            # Stream the failed-step logs straight through.
            try:
                sys.stdout.write(gh_text(["run", "view", "--job", str(job_id), "--log-failed"]))
            except GhError as e:
                print(f"# (ERROR: could not fetch logs for job {job_id}: {e})")

    if external_lines:
        print("")
        for line in external_lines:
            print(line)
        print("")
        print("# (no inline logs available for external checks - investigate via the URLs above;")
        print("#  treat these like \"External checks with no inspectable logs\" in step 5's guidance)")

    return 0


# --- commit-push subcommand --------------------------------------------------

def cmd_commit_push(args):
    message = args.message
    files = args.files
    if not message:
        print(json.dumps({"error": "commit message cannot be empty"}), file=sys.stderr)
        return 2
    if not files:
        print(json.dumps({"error": "Usage: commit-push <message> <file1> [file2 ...]"}),
              file=sys.stderr)
        return 2
    for cmd in ("git", "gh"):
        if not have(cmd):
            print(json.dumps({"error": f"{cmd} not found"}), file=sys.stderr)
            return 1

    for path in files:
        exists = os.path.exists(path)
        tracked = _run(["git", "ls-files", "--error-unmatch", "--", path]).returncode == 0
        if not exists and not tracked:
            print(json.dumps({"error": f"path not found and not tracked: {path}"}),
                  file=sys.stderr)
            return 1

    add = _run(["git", "add", "--", *files])
    if add.returncode != 0:
        print(json.dumps({"error": (add.stderr or "git add failed").strip()}), file=sys.stderr)
        return 1

    if _run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        print(json.dumps({"error": "nothing staged after git add - check the listed files "
                                   "actually have changes"}), file=sys.stderr)
        return 1

    commit = _run(["git", "commit", "--no-gpg-sign", "-m", message], capture=False)
    if commit.returncode != 0:
        print(json.dumps({"error": "git commit failed (hook or signing error)"}), file=sys.stderr)
        return 1

    push = _run(["git", "push"], capture=False)
    if push.returncode != 0:
        print(json.dumps({"error": "git push failed"}), file=sys.stderr)
        return 1

    rev = _run(["git", "rev-parse", "HEAD"])
    if rev.returncode != 0:
        print(json.dumps({"error": "git rev-parse HEAD failed after push"}), file=sys.stderr)
        return 1
    sha = (rev.stdout or "").strip()

    try:
        info = gh_json(["repo", "view", "--json", "owner,name"])
    except GhError:
        print(json.dumps({"error": "could not determine repository"}), file=sys.stderr)
        return 1
    owner = ((info or {}).get("owner") or {}).get("login")
    repo = (info or {}).get("name")
    if not owner or not repo:
        print(json.dumps({"error": "failed to parse owner/repo from gh output"}), file=sys.stderr)
        return 1

    print(f"sha={sha}")
    print(f"url=https://github.com/{owner}/{repo}/commit/{sha}")
    return 0


# --- reply subcommand --------------------------------------------------------

REPLY_MUTATION = """
mutation($threadId: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: { pullRequestReviewThreadId: $threadId, body: $body }) {
    comment {
      id
      databaseId
      url
    }
  }
}"""


def _read_body(args):
    """Body comes from stdin when --body is '-' or omitted, else from the flag."""
    if args.body is not None and args.body != "-":
        return args.body
    return sys.stdin.read()


def cmd_reply(args):
    thread_id = args.thread_id
    body = _read_body(args)
    if not thread_id:
        print(json.dumps({"error": "thread-id is required"}), file=sys.stderr)
        return 2
    if not body:
        print(json.dumps({"error": "body is required"}), file=sys.stderr)
        return 2

    body = ensure_sentinel(body)
    try:
        # `-f`/`--raw-field` passes the value literally: it does NOT do gh's
        # `@filename` / `@-` interpretation (that is only `-F`/`--field`), so a
        # leading `@` in an @mention reply is posted verbatim, as intended.
        result = gh_json([
            "api", "graphql",
            "-f", f"query={REPLY_MUTATION}",
            "-f", f"threadId={thread_id}",
            "-f", f"body={body}",
        ])
    except GhError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1

    url = ((((result or {}).get("data") or {})
            .get("addPullRequestReviewThreadReply") or {})
           .get("comment") or {}).get("url")
    if not url:
        print(json.dumps({"error": "reply posted but no URL returned",
                          "raw": result}), file=sys.stderr)
        return 1
    print(url)
    return 0


# --- comment subcommand ------------------------------------------------------

def cmd_comment(args):
    pr_number = args.pr
    body = _read_body(args)
    if not re.fullmatch(r"[0-9]+", pr_number or ""):
        print(json.dumps({"error": f"Invalid PR number: {pr_number}"}), file=sys.stderr)
        return 2
    if not body:
        print(json.dumps({"error": "body is required"}), file=sys.stderr)
        return 2

    body = ensure_sentinel(body)
    try:
        info = gh_json(["repo", "view", "--json", "owner,name"])
    except GhError:
        print(json.dumps({"error": "Could not determine repository."}), file=sys.stderr)
        return 1
    owner = ((info or {}).get("owner") or {}).get("login")
    repo = (info or {}).get("name")
    if not owner or not repo:
        print(json.dumps({"error": "Failed to parse repository info from gh output."}),
              file=sys.stderr)
        return 1

    try:
        # `-f`/`--raw-field` is literal (no `@filename` interpretation), so a
        # leading `@` in the body is posted verbatim.
        result = gh_json([
            "api", f"repos/{owner}/{repo}/issues/{pr_number}/comments",
            "--method", "POST",
            "-f", f"body={body}",
        ])
    except GhError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1

    url = (result or {}).get("html_url")
    if not url:
        print(json.dumps({"error": "comment posted but no URL returned",
                          "raw": result}), file=sys.stderr)
        return 1
    print(url)
    return 0


# --- argument parsing --------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="pr_babysit_cli.py",
                                description="pr-babysit helper CLI")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("review", help="fetch + shape PR review data (JSON on stdout)")
    r.add_argument("pr", nargs="?", default=None, help="PR number (default: current branch)")
    r.set_defaults(func=cmd_review)

    fl = sub.add_parser("failed-logs", help="stream failed-check logs")
    fl.add_argument("pr", nargs="?", default=None, help="PR number (default: current branch)")
    fl.set_defaults(func=cmd_failed_logs)

    cp = sub.add_parser("commit-push", help="stage named files, commit, push")
    cp.add_argument("message", help="commit message")
    cp.add_argument("files", nargs="*", help="explicit files to stage")
    cp.set_defaults(func=cmd_commit_push)

    rp = sub.add_parser("reply", help="threaded review-thread reply (body via stdin)")
    rp.add_argument("thread_id", help="PullRequestReviewThread GraphQL id")
    rp.add_argument("--body", default="-", help="reply body, or '-' to read stdin (default)")
    rp.set_defaults(func=cmd_reply)

    cm = sub.add_parser("comment", help="top-level PR comment (body via stdin)")
    cm.add_argument("pr", help="PR number")
    cm.add_argument("--body", default="-", help="comment body, or '-' to read stdin (default)")
    cm.set_defaults(func=cmd_comment)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
