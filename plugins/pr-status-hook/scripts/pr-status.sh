#!/usr/bin/env bash
# Stop hook: report PR existence + push status from real git/gh state.
# Stays silent unless there is something worth reporting.
set -uo pipefail

input=""
if [[ ! -t 0 ]]; then
  input=$(cat)
fi
hook_event=$(echo "$input" | jq -r '.hook_event_name // empty' 2>/dev/null || true)

# Only meaningful inside a git repo.
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
case "$branch" in HEAD|main|master|"") exit 0 ;; esac

# Uncommitted changes (count of porcelain lines).
dirty=$(git status --porcelain 2>/dev/null | grep -c .)

# Unpushed commits ahead of upstream. Empty string => no upstream configured.
ahead=$(git rev-list --count '@{u}..HEAD' 2>/dev/null || true)

# Open PR for this branch (empty if none / gh unavailable).
pr_url=$(gh pr view --json url --jq .url 2>/dev/null || true)

# Nothing interesting -> stay silent.
if [ -z "$pr_url" ] && [ -n "$ahead" ] && [ "$ahead" = "0" ] && [ "$dirty" = "0" ]; then
  exit 0
fi

# Build the banner.
if [ -n "$pr_url" ]; then
  msg="PR $pr_url"
else
  msg="No PR for branch '$branch'"
fi

if [ -z "$ahead" ]; then
  msg="$msg · ⚠ branch has no upstream (never pushed)"
elif [ "$ahead" != "0" ]; then
  msg="$msg · ⚠ $ahead commit(s) NOT pushed"
else
  msg="$msg · ✓ all commits pushed"
fi

if [ "$dirty" != "0" ]; then
  msg="$msg · ✎ $dirty file(s) uncommitted"
fi

# Cursor stop hooks surface banners on stderr; Claude uses systemMessage JSON.
if [[ "$hook_event" == "stop" ]]; then
  echo "$msg" >&2
  exit 0
fi

# Emit as a user-facing banner; keep stdout out of the transcript otherwise.
jq -n --arg m "$msg" '{systemMessage: $m, suppressOutput: true}'
exit 0
