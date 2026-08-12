#!/usr/bin/env bash
# Record what a pr-status hook prints across every repository state it handles.
#
# The recording lives in golden-banners.txt and test_pr_status.py replays it.
# To change a banner on purpose, run this and commit the new recording with the
# code change. The fixture diff then shows what users will see differently.
#
# It was written to port pr-status.sh to Python without changing its output, by
# running it against each and comparing. That is why it takes the hook command
# as an argument rather than hard-coding one.
#
# Usage: characterize.sh <path-to-hook-command...>
#   characterize.sh python3 ./pr_status.py
set -uo pipefail

HOOK_CMD=("$@")
[ ${#HOOK_CMD[@]} -gt 0 ] || { echo "usage: characterize.sh <command...>" >&2; exit 2; }

# Resolve to absolute so the temp-repo cd does not break the path.
HOOK_CMD[1]=$(cd "$(dirname "${HOOK_CMD[1]}")" && pwd)/$(basename "${HOOK_CMD[1]}")

WORK=$(mktemp -d "${TMPDIR:-/tmp}/pr-status-char.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

# A fake `gh` so the matrix controls the PR answer. Both hooks ask for url and
# state as one tab-separated line, so the fake emits that shape. An empty
# FAKE_PR_URL means the branch has no pull request at all.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/gh" <<'GH'
#!/usr/bin/env bash
[ -n "${FAKE_PR_URL:-}" ] || exit 1
printf '%s\t%s\n' "${FAKE_PR_STATE:-OPEN}" "$FAKE_PR_URL"
GH
chmod +x "$WORK/bin/gh"
export PATH="$WORK/bin:$PATH"

# Build a repo in one named state and print the hook's output for it.
#
#   $1 case name.
#   $2 branch name.
#   $3 upstream: none, ahead, or level.
#   $4 dirty file count.
#   $5 PR url, or empty for no PR.
#   $6 PR state, defaulting to OPEN.
run_case() {
    local name=$1 branch=$2 upstream=$3 dirty=$4 pr=$5 state=${6:-OPEN}
    local repo="$WORK/repos/$name"
    rm -rf "$repo"; mkdir -p "$repo"; cd "$repo" || return

    # Each case gets its own TMPDIR, because the hook keeps its PR cache and
    # rate-limit marker there. Two cases built in the same second can produce
    # the same commit sha, and a shared cache would let one case answer for
    # another. That would make this recording depend on run order.
    export TMPDIR="$WORK/tmp/$name"
    mkdir -p "$TMPDIR"

    git init -q -b "$branch" .
    git config user.email t@example.com; git config user.name Test
    echo base > base.txt; git add base.txt; git commit -qm base

    if [ "$upstream" != "none" ]; then
        git init -q --bare "$repo.remote"
        git remote add origin "$repo.remote"
        git push -q -u origin "$branch" 2>/dev/null
        if [ "$upstream" = "ahead" ]; then
            echo more > more.txt; git add more.txt; git commit -qm more
        fi
    fi

    local i=0
    while [ "$i" -lt "$dirty" ]; do
        echo "dirt$i" > "dirty$i.txt"; i=$((i + 1))
    done

    echo "### $name"
    for event in Stop stop; do
        local rc=0
        # Redirect to a file rather than capture with $( ), which strips every
        # trailing newline. A port that changed terminal bytes would otherwise
        # be invisible to a recording that claims byte fidelity.
        printf '{"hook_event_name":"%s"}' "$event" \
            | FAKE_PR_URL="$pr" FAKE_PR_STATE="${state:-OPEN}" \
              "${HOOK_CMD[@]}" > "$WORK/out.bin" 2>&1 || rc=$?
        echo "--- event=$event rc=$rc ---"
        normalize "$WORK/out.bin"
    done
    echo
}

# `jq -n` pretty-prints and `json.dumps` does not, so compare the parsed object
# rather than its spacing. Non-JSON output passes through untouched.
#
# Normalizing only spacing is deliberate. An earlier version re-dumped with
# `ensure_ascii=False`, which also canonicalized escaping. That hid a real
# difference. `json.dumps` escaped `·` to `\u00b7` where `jq` wrote it raw.
# So the escaped form is now reported alongside the parsed object.
normalize() {
    python3 - "$1" <<'NORM'
import json, sys
raw = open(sys.argv[1], "rb").read().decode("utf-8")
try:
    parsed = json.loads(raw)
except ValueError:
    print(raw)
else:
    print(json.dumps(parsed, sort_keys=True, ensure_ascii=False))
    non_ascii = [c for c in raw if ord(c) > 127]
    print("raw-non-ascii:", "".join(sorted(set(non_ascii))) or "(none)")
print("raw-trailing:", repr(raw[len(raw.rstrip("\r\n")):]))
NORM
}

#         name                       branch   upstream dirty pr
run_case  no-pr-clean-level          feat/x   level    0     ""
run_case  no-pr-no-upstream          feat/x   none     0     ""
run_case  no-pr-ahead                feat/x   ahead    0     ""
run_case  no-pr-dirty                feat/x   level    2     ""
run_case  no-pr-ahead-dirty          feat/x   ahead    3     ""
run_case  pr-clean-level             feat/x   level    0     "https://github.com/o/r/pull/7"
run_case  pr-no-upstream             feat/x   none     0     "https://github.com/o/r/pull/7"
run_case  pr-ahead                   feat/x   ahead    0     "https://github.com/o/r/pull/7"
run_case  pr-ahead-dirty             feat/x   ahead    1     "https://github.com/o/r/pull/7"
run_case  pr-slashy-branch           a/b/c    level    0     "https://github.com/o/r/pull/7"
run_case  skip-main                  main     level    1     "https://github.com/o/r/pull/7"
run_case  skip-master                master   level    1     "https://github.com/o/r/pull/7"
run_case  pr-merged                  feat/x   level    0     "https://github.com/o/r/pull/7"  MERGED
run_case  pr-closed                  feat/x   ahead    1     "https://github.com/o/r/pull/7"  CLOSED

# Outside a work tree: no git repo at all.
mkdir -p "$WORK/notrepo"; cd "$WORK/notrepo" || exit
echo "### outside-a-work-tree"
for event in Stop stop; do
    rc=0
    printf '{"hook_event_name":"%s"}' "$event" \
        | "${HOOK_CMD[@]}" > "$WORK/out.bin" 2>&1 || rc=$?
    echo "--- event=$event rc=$rc ---"
    normalize "$WORK/out.bin"
done
