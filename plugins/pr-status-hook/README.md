# pr-status-hook

Print the branch's pull-request link the moment `gh` knows it, plus push and dirty-tree state at every turn end. Both read real `git` and `gh` output, never the agent's account of what it did.

## Install

```text
/plugin install pr-status-hook@wild-horses
```

No command. Both hooks fire automatically once installed.

## Bundled hooks

| Hook             | Event                 | Job                                                       |
| ---------------- | --------------------- | --------------------------------------------------------- |
| `pr_announce.py` | `PostToolUse` on Bash | Print the PR link as soon as a `gh pr` command reveals it |
| `pr_status.py`   | `Stop`                | Report PR, push, and dirty-tree state as the turn ends    |

**Cursor:** hooks register via `hooks/cursor-hooks.json` (`postToolUse` on `Shell`, and `stop`). Same scripts; the matcher and event spelling differ from Claude's `Bash` and `Stop`.

`pr_hook_common.py` holds what the two must not disagree on. That covers reading the harness payload and running a command. It also covers asking `gh` for the pull request, spelling the link, and choosing the output channel.

## Invariants

These are the design requirements. Each one exists because breaking it produced a real failure.

**1. The user sees the link without the agent emitting any text.**
The banner is a `systemMessage`, which the harness prints whatever the agent does next. Prose telling the agent to print a link is not a guarantee. It is vacuous when the agent emits no messages. That is exactly what happens between `gh pr create` and a long babysit loop.

**2. The link prints when it becomes knowable, not at turn end.**
A `Stop` hook never runs on a user interrupt, and cannot run while a turn is still blocked. `/wild-pr` blocks inside `gh pr checks --watch` for up to 600 seconds per pass. So turn end is the one moment that a long run may never reach.

**3. `pr_announce.py` never reads the PR cache.**
It runs at the instant `gh pr create` changes the answer. A cached "no PR" would suppress the one banner this plugin exists to guarantee. It only writes the cache, so `pr_status.py` can reuse that answer instead of asking `gh` again.

**4. The link shows whatever state the pull request is in, labelled.**
A PR that merges mid-session must not make its own link vanish. That is the moment you most want to click it. Both banners label a non-open state, through `pr_link` in the shared module. An unlabelled link reads as an open PR. So one hook must not print bare while the other labels.

The link is the guaranteed part, not the label. State comes from the same cached answer. So a PR that merges can keep reading as open for up to `PR_CACHE_TTL_SECONDS`. A new commit changes the key and asks `gh` again.

**5. One run is bounded by one shared deadline, not by per-call timeouts.**
Per-call budgets sum. `pr_announce.py` makes three calls at 5 or 10 seconds each, reaching 20 seconds, which is the wrapper timeout exactly. A hook the wrapper kills prints nothing, which is the one outcome these scripts exist to avoid. Every runner in a run shares `TOTAL_BUDGET_SECONDS`, so adding a call cannot break the bound.

**6. Neither hook depends on `jq`.**
The shell version failed silent without it: no output, exit 0, no error. Silent is the one failure mode a status hook cannot afford. That is why these are Python and not shell.

**7. State lives in a private directory, never `TMPDIR`.**
On Linux `TMPDIR` is often `/tmp`, which is world-writable. Both the cache and the rate-limit marker use paths derived from a branch name and a commit sha. A local user who guesses one could plant a URL the banner then prints. Both live under `XDG_STATE_HOME` at mode `0700`. Both open with `O_NOFOLLOW`, so a planted symlink cannot redirect a read or a write. A directory that cannot be made usable yields no state rather than an error, per the next invariant.

**8. Nothing here blocks or fails a tool call.**
Every gate returns quietly. That covers a missing binary, an unreadable marker, and a malformed payload. It also covers an unwritable cache, and a state directory that cannot be created. Losing state costs a `gh` call at every turn end. It also drops the rate limit. Every matching `gh pr` call then announces. Raising would cost the banner itself, plus a traceback after every turn end.

## Facts that are easy to get wrong

Four things cost real debugging time. Read them before changing a query or a gate.

- **`gh pr view` reports the branch's most recent PR, whatever its state.** It is not an open-PR query. Read the state; do not assume it.
- **`gh pr view` resolves by branch name against the remote, not by local tracking config.** So a branch with no upstream can still have an open PR, after `git branch --unset-upstream` or a recreated local branch. "Never pushed" is not a safe reason to skip the lookup.
- **`PostToolUse` fires after the tool returns, never before.** Matching `gh pr checks` restates the link when a long CI wait _ends_. Only `gh pr create` puts it up early enough to survive an interrupt during that wait.
- **A `Stop` hook does not run when the user interrupts.** That gap is the reason `pr_announce.py` exists.

## Cost

`gh pr view` measures around 500 ms, and `pr_status.py` runs at every turn end. Two things hold that down. One `git status --porcelain=v2 --branch` call replaces three separate `git` calls. A PR answer is also cached per repository, branch, and commit. A turn end that reuses one costs about 70 ms instead of about 525 ms.

`pr_announce.py` also rate-limits itself to one banner per branch per five minutes, so a babysit loop cannot spam the transcript.

## Tests

```text
python3 -m unittest discover -s plugins/pr-status-hook/scripts -p 'test_*.py'
```

`characterize.sh` builds one repository state per case it lists, and records what the hook prints for each into `golden-banners.txt`. It began as proof that the Python port matched the shell script it replaced, which it did byte for byte. It is kept for a second reason. It is the only test here that uses real git repositories rather than a fake runner.

To change a banner on purpose, run `characterize.sh` and commit the new recording with the code change. The fixture diff is then the reviewable statement of what users will see differently.

That harness has caught three of its own blind spots, so treat its claims as testable rather than given. It once re-dumped JSON in a way that canonicalized escaping. It captured output through command substitution, which strips trailing newlines. And it compared decoded text rather than bytes.
