---
name: churn
description: Diagnose why a PR keeps going through review rounds without converging, then recommend one way forward - keep patching, refactor in this PR, split the PR, or restart with relaxed requirements. Use when a PR has had many review rounds, fixes keep producing new findings, or the user asks why a PR won't settle, or runs /wild-pr:churn [pr-number-or-url].
user-invocable: true
argument-hint: "[pr-number-or-url]"
---

# churn - diagnose a PR that won't converge

A PR that needs many review rounds usually has one structural cause under many separate findings. This skill finds that cause from the review history and recommends one way forward. It proposes. It never edits code, pushes, replies to comments, or applies its own verdict.

## Invocation

- `/wild-pr:churn` - the PR for the current branch.
- `/wild-pr:churn <number>` - that PR in the current repo.
- `/wild-pr:churn <url>` - that PR in any repo the `gh` CLI can read.

## Step 1 - Collect

Make a fresh run directory, then collect. Use literal paths in every later Bash call, because a shell variable does not survive to the next call.

```bash
mktemp -d "${TMPDIR:-/tmp}/wild-pr-churn.XXXXXX"
python3 "${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT}}/scripts/pr_churn_cli.py" collect [pr] --out "<run dir>"
```

The CLI writes `<run dir>/timeline.json` and prints the churn metrics as JSON. Stop and show the user `.error` if the output has one.

- **Timeline:** every commit (with the files it touched), every round, and every finding. A finding is a review thread, a non-empty review body, or a conversation comment. Each carries an `id`, a `round`, a bot `label`, a `title`, and the full `body`.
- **Round:** all posted review activity against one reviewed commit. `commits_since_previous` lists what that round reviewed.
- **Metrics:** `findings_per_round`, fix commits by type, `after_last_review`, and `hot_files`.

Then gather the rest into the run directory:

1. `gh pr diff <N> --repo <owner>/<repo> > "<run dir>/diff.patch"`.
2. The requirements. Look in `~/plans/<repo>/` and `~/plans/<repo>/done/` for a plan whose title or slug matches the branch or the PR title. Then read the PR body. Write what you found to `<run dir>/requirements.md`, citing each source. When no plan exists, the PR body and the `feat` commit messages are the requirements.

**Too few rounds.** If `rounds` is below 3, print the metrics, say the PR is not churning yet, and stop. Dispatch no agents. Step 2 applies the same check again after it removes noise.

**Truncated.** If `truncated` is not empty, say which history is partial in the report.

## Step 2 - Triage (main agent)

Read `timeline.json` in full. Write `<run dir>/triage.md` with three parts.

**Noise.** List each finding that is not churn, with a reason. Noise is:

- a bot status notice, such as a usage limit or a skipped run;
- a review request such as `@codex review`;
- a review body or summary comment that restates inline findings;
- a duplicate of an earlier finding;
- a nitpick with no behavior change.

**Unposted findings.** A fix commit that answers no posted finding came from a review that was never posted, such as a local review. Read its message and body with `git show` or `gh api`. Add it as a finding with the id `C<short sha>`. Count `after_last_review` commits this way too.

**Findings table.** For every remaining finding, record the id, the round, and whether it is **new** or **fix-on-fix**. A finding is fix-on-fix when it targets code that an earlier fix commit changed to answer an earlier finding. Check this against the commit files and diffs, not the titles.

**Clusters.** Group findings by root cause, not by file and not by reviewer. Name each cluster by the shared thing, for example "concurrent writers to the connection row". Give each a one-sentence statement and its finding ids. A finding that shares a cause with no other finding forms its own cluster.

**Too few real rounds.** Count the rounds that keep at least one finding after noise is removed. If that count is below 3, print the metrics and the noise list, say the PR is not churning yet, and stop. Dispatch no agents. A round of bot notices, nitpicks, or duplicates is not a review round.

## Step 3 - Diagnose (parallel agents)

Dispatch the five lens agents in [references/lenses.md](references/lenses.md) in one message, so they run in parallel. Use the host's subagent mechanism (in Claude Code, the `Agent` tool with `subagent_type: general-purpose`). Each agent gets its lens prompt verbatim, with the run directory and the repo directory filled in. The repo directory is the current checkout when it holds the PR's commits, and `none` otherwise. Agents never see each other's output.

If one agent fails or returns malformed output, re-dispatch that agent only. If the host cannot run subagents, run the five lenses inline, one after another, and say so in the report.

## Step 4 - Synthesize (main agent)

Merge the lens claims per cluster. When two lenses disagree, keep the claim with the stronger evidence and record the other in the report. Drop any claim with no evidence.

Pick one verdict:

| Verdict                       | Signals                                                                                                  |
| ----------------------------- | -------------------------------------------------------------------------------------------------------- |
| keep patching                 | Findings per round are falling. Few findings are fix-on-fix. Clusters are unrelated edge cases.          |
| refactor in this PR           | One or two clusters share one structural cause. A contained refactor removes the whole class of finding. |
| split the PR                  | Clusters map to separate concerns. Each part could converge alone.                                       |
| restart with new requirements | One requirement drives most clusters, and relaxing it removes them.                                      |

Name the runner-up and state why it lost.

## Step 5 - Report and save

Write the report in this shape:

1. **Verdict** and a one-paragraph reason.
2. **Churn numbers:** rounds, findings per round, the fix-on-fix share, hot files, and unposted findings.
3. **Clusters:** each with its findings, the lens claims, and the diagnosis.
4. **Proposed requirement changes:** each with the requirement, the cost of keeping it, what relaxing it removes, and what the user loses.
5. **Runner-up verdict** and why it lost.
6. **Excluded noise:** a count per reviewer.

Every claim cites evidence: a comment URL, a commit SHA, or `file:line`. Save the report with the `plan-keeper:plan-save` skill as `Kind: design`. If plan-keeper is not installed, write it to `<run dir>/report.md`. Print the verdict and the saved path.

## Never

- **Don't edit code, commit, push, or reply to review comments.** The verdict is a proposal for the user.
- **Don't ask the user questions before the verdict.** Requirement changes go in the report as proposals.
- **Don't count noise as churn.** A round of bot notices is not a review round.
- **Don't diagnose from finding titles alone.** Read the bodies, the commit messages, and the diff.
- **Don't run a code review.** New defects are out of scope. `/wild-pr:review` does that.
