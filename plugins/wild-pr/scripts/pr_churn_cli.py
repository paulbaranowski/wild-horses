#!/usr/bin/env python3
"""pr_churn_cli.py - evidence collector backing the churn skill.

One subcommand:

    collect [pr]   fetch a PR's review history, write timeline.json to --out,
                   print the churn metrics as JSON on stdout

Stdlib only. GitHub access is via the `gh` CLI. Errors go to stdout as
{"error": ...} with exit 1, so the skill reads `.error` from the same JSON it
parses. Usage errors exit 2 (argparse).

The CLI computes only what needs no judgment: which review round each finding
belongs to, findings per round, and which files the fix commits keep touching.
Deciding what is noise, what a later fix caused, and what the root causes are
is the skill's job.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter

GH_TIMEOUT_SECONDS = 120

# A file is hot when at least this many fix commits touch it, or at least this
# many findings anchor to it.
HOT_FILE_THRESHOLD = 3

GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      number title url body state createdAt baseRefName headRefName
      additions deletions changedFiles
      author { login }
      commits(first: 100) {
        pageInfo { hasNextPage }
        nodes { commit { oid committedDate messageHeadline messageBody } }
      }
      reviews(first: 100) {
        pageInfo { hasNextPage }
        nodes { author { login __typename } state submittedAt body url commit { oid } }
      }
      reviewThreads(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          isResolved isOutdated path line originalLine
          comments(first: 50) {
            pageInfo { hasNextPage }
            nodes { author { login __typename } body createdAt url originalCommit { oid } }
          }
        }
      }
      comments(first: 100) {
        pageInfo { hasNextPage }
        nodes { author { login __typename } body createdAt url }
      }
    }
  }
}
"""

# Replies that pr-babysit posts. They answer findings; they are not findings.
SENTINEL_MARKERS = ("pr-babysit:addressed v1 ", "cb-babysit:addressed v1 ",
                    "babysit-pr:addressed v1 ")

# Severity labels that review bots write into a finding body. First match wins.
BOT_LABEL_PATTERNS = (
    (re.compile(r"\*\*(Critical|High|Medium|Low) Severity\*\*"), None),  # Cursor Bugbot
    (re.compile(r"!\[(P[0-9]) Badge\]"), None),                         # Codex
    (re.compile(r"_\U0001F534 Critical_"), "critical"),                 # CodeRabbit
    (re.compile(r"_\U0001F7E0 Major_"), "major"),
    (re.compile(r"_\U0001F7E1 Minor_"), "minor"),
    (re.compile(r"_\U0001F535 Trivial_"), "trivial"),
    (re.compile(r"\bnit(pick)?\b", re.IGNORECASE), "nitpick"),
)

CONVENTIONAL_TYPE = re.compile(r"^([a-z]+)(?:\([^)]*\))?!?:")
PR_URL = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/([0-9]+)")


# --- gh plumbing -------------------------------------------------------------

class CollectError(Exception):
    """Renders as {"error": ...} on stdout, exit 1."""


def gh_json(args):
    """Run `gh <args>` and parse stdout as JSON. Raise CollectError on failure."""
    try:
        proc = subprocess.run(["gh", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=GH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as e:
        raise CollectError(f"gh timed out after {GH_TIMEOUT_SECONDS}s: gh {' '.join(args[:2])}") from e
    if proc.returncode != 0:
        raise CollectError((proc.stderr or proc.stdout or "").strip())
    if not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise CollectError(f"gh returned non-JSON output: gh {' '.join(args[:2])}") from e


def resolve_target(arg):
    """Return (owner, repo, number) for a PR number, a PR URL, or None (current branch)."""
    if arg:
        m = PR_URL.search(arg)
        if m:
            return m.group(1), m.group(2), int(m.group(3))
        if not re.fullmatch(r"#?[0-9]+", arg):
            raise CollectError(f"Invalid PR argument: {arg}")
        number = int(arg.lstrip("#"))
    else:
        pr = gh_json(["pr", "view", "--json", "number"])
        number = (pr or {}).get("number")
        if not number:
            raise CollectError("No PR found for current branch. Provide a PR number or URL.")
    info = gh_json(["repo", "view", "--json", "owner,name"]) or {}
    owner = (info.get("owner") or {}).get("login")
    name = info.get("name")
    if not owner or not name:
        raise CollectError("Could not determine the repository from the gh CLI.")
    return owner, name, int(number)


def fetch_commit_files(owner, repo, oid):
    """Every file a commit touched. The REST endpoint pages its file list,
    so --slurp collects every page into one JSON array."""
    pages = gh_json(["api", "--paginate", "--slurp", f"repos/{owner}/{repo}/commits/{oid}"]) or []
    return [f["filename"] for page in pages for f in page.get("files") or []]


# --- pure helpers ------------------------------------------------------------

def is_sentinel(body):
    return any(marker in (body or "") for marker in SENTINEL_MARKERS)


def bot_label(body):
    for pattern, label in BOT_LABEL_PATTERNS:
        m = pattern.search(body or "")
        if m:
            return label or m.group(1).lower()
    return None


def body_title(body):
    """First heading or bold line of a finding body, with markup stripped."""
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.DOTALL)
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"</?su[bp]>", "", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines:
        if line.startswith("#") or line.startswith("**"):
            title = line.lstrip("#").strip().strip("*").strip()
            if title and not re.fullmatch(r"(Critical|High|Medium|Low) Severity", title):
                return title[:160]
    return lines[0][:160] if lines else ""


def commit_type(headline):
    m = CONVENTIONAL_TYPE.match(headline or "")
    return m.group(1) if m else None


def _login(node):
    return ((node or {}).get("author") or {}).get("login") or "ghost"


def _is_bot(node):
    author = (node or {}).get("author") or {}
    login = author.get("login") or ""
    return author.get("__typename") == "Bot" or login.endswith("[bot]")


def _nodes(conn):
    return (conn or {}).get("nodes") or []


# --- timeline ----------------------------------------------------------------

def build_timeline(pr, commit_files):
    """Shape the raw GraphQL pullRequest node into the churn timeline.

    `commit_files` maps a commit oid to the files it touched. Every finding is
    tagged with the review round it arrived in. A round is the set of review
    activity against one reviewed commit. Rounds are numbered in commit order.
    """
    pr_author = (pr.get("author") or {}).get("login")

    commits = []
    for node in _nodes(pr.get("commits")):
        c = node["commit"]
        commits.append({
            "oid": c["oid"],
            "short": c["oid"][:7],
            "date": c["committedDate"],
            "headline": c["messageHeadline"],
            "body": c.get("messageBody") or "",
            "type": commit_type(c["messageHeadline"]),
            "files": commit_files.get(c["oid"], []),
        })
    position = {c["oid"]: i for i, c in enumerate(commits)}

    def commit_at(created_at):
        """Last commit made at or before a timestamp: the fallback anchor."""
        best = None
        for c in commits:
            if c["date"] <= created_at:
                best = c["oid"]
        return best

    findings = []
    for thread in _nodes(pr.get("reviewThreads")):
        comments = [c for c in _nodes(thread.get("comments")) if not is_sentinel(c.get("body"))]
        if not comments:
            continue
        root = comments[0]
        reviewed = (root.get("originalCommit") or {}).get("oid")
        findings.append({
            "kind": "thread",
            "author": _login(root),
            "is_bot": _is_bot(root),
            "is_pr_author": _login(root) == pr_author,
            "created_at": root["createdAt"],
            "url": root.get("url"),
            "path": thread.get("path"),
            "line": thread.get("line") or thread.get("originalLine"),
            "resolved": thread.get("isResolved", False),
            "outdated": thread.get("isOutdated", False),
            "label": bot_label(root.get("body")),
            "title": body_title(root.get("body")),
            "body": root.get("body") or "",
            "replies": [{"author": _login(c), "created_at": c["createdAt"], "body": c.get("body") or ""}
                        for c in comments[1:]],
            "reviewed_commit": reviewed if reviewed in position else commit_at(root["createdAt"]),
        })

    for review in _nodes(pr.get("reviews")):
        body = (review.get("body") or "").strip()
        if not body or is_sentinel(body):
            continue
        reviewed = (review.get("commit") or {}).get("oid")
        findings.append({
            "kind": "review_body",
            "author": _login(review),
            "is_bot": _is_bot(review),
            "is_pr_author": _login(review) == pr_author,
            "created_at": review["submittedAt"],
            "url": review.get("url"),
            "label": bot_label(body),
            "title": body_title(body),
            "body": body,
            "reviewed_commit": reviewed if reviewed in position else commit_at(review["submittedAt"]),
        })

    for comment in _nodes(pr.get("comments")):
        body = (comment.get("body") or "").strip()
        if not body or is_sentinel(body):
            continue
        findings.append({
            "kind": "conversation",
            "author": _login(comment),
            "is_bot": _is_bot(comment),
            "is_pr_author": _login(comment) == pr_author,
            "created_at": comment["createdAt"],
            "url": comment.get("url"),
            "label": bot_label(body),
            "title": body_title(body),
            "body": body,
            "reviewed_commit": commit_at(comment["createdAt"]),
        })

    # Only reviews open a round. A conversation comment (a bot's "couldn't
    # run" notice, an "@codex review" request) joins the latest round at or
    # before its commit, so it never inflates the round count.
    reviewed_oids = sorted({f["reviewed_commit"] for f in findings
                            if f["kind"] != "conversation" and f["reviewed_commit"]},
                           key=position.__getitem__)
    round_of = {oid: i + 1 for i, oid in enumerate(reviewed_oids)}

    def round_for(oid):
        if oid is None:
            return None
        rounds_before = [round_of[r] for r in reviewed_oids if position[r] <= position[oid]]
        return rounds_before[-1] if rounds_before else None

    findings.sort(key=lambda f: f["created_at"])
    for i, f in enumerate(findings):
        f["id"] = f"F{i + 1}"
        f["round"] = round_of.get(f["reviewed_commit"]) or round_for(f["reviewed_commit"])

    rounds = []
    previous = -1
    for oid in reviewed_oids:
        idx = position[oid]
        rounds.append({
            "round": round_of[oid],
            "reviewed_commit": oid,
            "reviewed_at": commits[idx]["date"],
            # The commits pushed since the previous round: what this round reviewed.
            "commits_since_previous": [c["oid"] for c in commits[previous + 1: idx + 1]],
        })
        previous = idx

    truncated = [name for name in ("commits", "reviews", "reviewThreads", "comments")
                 if ((pr.get(name) or {}).get("pageInfo") or {}).get("hasNextPage")]
    if any(((t.get("comments") or {}).get("pageInfo") or {}).get("hasNextPage")
           for t in _nodes(pr.get("reviewThreads"))):
        truncated.append("threadComments")

    return {
        "pr": {
            "number": pr["number"],
            "title": pr["title"],
            "url": pr["url"],
            "body": pr.get("body") or "",
            "state": pr.get("state"),
            "author": pr_author,
            "base": pr.get("baseRefName"),
            "head": pr.get("headRefName"),
            "created_at": pr.get("createdAt"),
            "additions": pr.get("additions"),
            "deletions": pr.get("deletions"),
            "changed_files": pr.get("changedFiles"),
        },
        "truncated": truncated,
        "commits": commits,
        "rounds": rounds,
        "findings": findings,
    }


def compute_metrics(timeline):
    """The churn numbers that need no judgment, derived from a timeline."""
    commits = timeline["commits"]
    findings = timeline["findings"]
    threads = [f for f in findings if f["kind"] == "thread"]

    per_round = []
    for r in timeline["rounds"]:
        in_round = [f for f in threads if f["round"] == r["round"]]
        per_round.append({
            "round": r["round"],
            "reviewed_commit": r["reviewed_commit"][:7],
            "commits_since_previous": len(r["commits_since_previous"]),
            "findings": len(in_round),
            "review_bodies": sum(1 for f in findings
                                 if f["kind"] == "review_body" and f["round"] == r["round"]),
            "by_author": dict(Counter(f["author"] for f in in_round)),
            "by_label": dict(Counter(f["label"] or "unlabeled" for f in in_round)),
        })

    # Fix commits are the ones pushed after the first review round.
    oids = [c["oid"] for c in commits]
    if timeline["rounds"]:
        start = oids.index(timeline["rounds"][0]["reviewed_commit"]) + 1
        end = oids.index(timeline["rounds"][-1]["reviewed_commit"]) + 1
    else:
        start = end = len(commits)
    fix_commits = commits[start:]

    touched = Counter(path for c in fix_commits for path in set(c["files"]))
    anchored = Counter(f["path"] for f in threads if f.get("path"))
    hot = [
        {"path": path, "fix_commits": touched[path], "findings": anchored[path]}
        for path in set(touched) | set(anchored)
        if touched[path] >= HOT_FILE_THRESHOLD or anchored[path] >= HOT_FILE_THRESHOLD
    ]
    hot.sort(key=lambda h: (-(h["fix_commits"] + h["findings"]), h["path"]))

    return {
        "pr": {k: timeline["pr"][k] for k in ("number", "title", "url", "author", "changed_files",
                                                "additions", "deletions")},
        "truncated": timeline["truncated"],
        "rounds": len(timeline["rounds"]),
        "findings_per_round": [r["findings"] for r in per_round],
        "per_round": per_round,
        "commits": {
            "total": len(commits),
            "after_first_review": len(fix_commits),
            # Pushed after the last posted review: fixes no posted review saw.
            "after_last_review": len(commits) - end,
            "by_type": dict(Counter(c["type"] or "none" for c in fix_commits)),
        },
        "findings": {
            "threads": len(threads),
            "unresolved": sum(1 for f in threads if not f["resolved"]),
            "review_bodies": sum(1 for f in findings if f["kind"] == "review_body"),
            "conversation": sum(1 for f in findings if f["kind"] == "conversation"),
            "by_author": dict(Counter(f["author"] for f in threads)),
        },
        "hot_files": hot,
    }


# --- collect subcommand ------------------------------------------------------

def cmd_collect(args):
    try:
        if shutil.which("gh") is None:
            raise CollectError("gh CLI not found. Install from https://cli.github.com")
        owner, repo, number = resolve_target(args.pr)
        response = gh_json(["api", "graphql", "-f", f"query={GRAPHQL_QUERY}",
                            "-f", f"owner={owner}", "-f", f"repo={repo}", "-F", f"pr={number}"])
        pr = (((response or {}).get("data") or {}).get("repository") or {}).get("pullRequest")
        if pr is None:
            raise CollectError(f"PR #{number} not found in {owner}/{repo}, or not accessible.")
        commit_files = {node["commit"]["oid"]: fetch_commit_files(owner, repo, node["commit"]["oid"])
                        for node in _nodes(pr.get("commits"))}

        timeline = build_timeline(pr, commit_files)
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "timeline.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(timeline, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

        metrics = compute_metrics(timeline)
        metrics["timeline"] = path
        print(json.dumps(metrics, indent=2))
        return 0
    except (CollectError, OSError) as e:
        print(json.dumps({"error": str(e)}))
        return 1


def build_parser():
    p = argparse.ArgumentParser(prog="pr_churn_cli.py", description="churn skill evidence collector")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="write timeline.json and print churn metrics (JSON)")
    c.add_argument("pr", nargs="?", default=None, help="PR number or URL (default: current branch)")
    c.add_argument("--out", required=True, help="directory to write timeline.json into")
    c.set_defaults(func=cmd_collect)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
