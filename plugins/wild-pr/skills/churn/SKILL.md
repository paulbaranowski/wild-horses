---
name: churn
description: Diagnose why a PR keeps going through review rounds without converging, then recommend one way forward - keep patching, refactor in this PR, split the PR, restart with new requirements, or move the change to the backend, frontend, external API, or library that should own it. Use when a PR has had many review rounds, fixes keep producing new findings, or the user asks why a PR won't settle, or runs /wild-pr:churn [pr-number-or-url].
user-invocable: true
argument-hint: "[pr-number-or-url]"
---

# churn - diagnose a PR that won't converge

A PR that needs many review rounds usually has one structural cause under many separate findings. This skill finds that cause from the review history and recommends one way forward. It proposes. It never edits code in any repo, pushes, replies to comments, or applies its own verdict.

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

Then find the components the PR's repo talks to. `<repo dir>` is the current checkout when it holds the PR's commits.

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT}}/scripts/pr_churn_cli.py" boundaries --repo "<repo dir>" --out "<run dir>"
```

The CLI writes `<run dir>/boundaries.json` and prints a count per kind. Its `skipped` list names sources it could not read, such as `pyproject.toml` on Python older than 3.11. Name each one in the report. Each entry is one component: a sibling `repo`, an HTTP `service`, a `library`, or an API `schema`. [references/boundaries.md](references/boundaries.md) describes the fields. If no checkout holds the PR's commits, skip this command and write `[]` to `<run dir>/boundaries.json`. Say in the report that boundary discovery was skipped, because no local checkout holds the PR's commits.

Then gather the rest into the run directory:

1. `gh pr diff <N> --repo <owner>/<repo> > "<run dir>/diff.patch"`.
2. The requirements. Look in `~/plans/<repo>/` and `~/plans/<repo>/done/` for a plan whose title or slug matches the branch or the PR title. Then read the PR body. Write what you found to `<run dir>/requirements.md`, citing each source. When no plan exists, the PR body and the `feat` commit messages are the requirements.
3. The standing rules. Read the Related Repos section, or its equivalent, in the repo's `CLAUDE.md`, `AGENTS.md`, and `ARCHITECTURE.md`. A standing rule says which component should own a fix. An example is "fix it there first rather than working around it here". Quote each one into `requirements.md` under a `## Standing rules` heading, with its `file:line`.

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

**Belongs to.** Give each cluster a **Belongs to** line. It names the component that owns the behavior the fix commits keep rebuilding, or it says "this repo". Examples of such behavior are an order, a version, an atomic write, and an idempotency key. Others are a uniqueness rule, input validation, an authorization check, a display format, and a retry policy. Look up each component a cluster names in `boundaries.json`, and read it.

**Claims about other components.** Each cluster's cause is a hypothesis. A sentence that says another component lacks a field, an order, a guarantee, or a feature must cite evidence. Source code with `file:line` counts. For a component with no readable source, a documentation URL counts. Otherwise end the sentence with `(unverified)`.

**Too few real rounds.** Count the rounds that keep at least one finding after noise is removed. If that count is below 3, print the metrics and the noise list, say the PR is not churning yet, and stop. Dispatch no agents. A round of bot notices, nitpicks, or duplicates is not a review round.

## Step 3 - Diagnose (parallel agents)

Dispatch the six lens agents in [references/lenses.md](references/lenses.md) in one message, so they run in parallel. Use the host's subagent mechanism (in Claude Code, the `Agent` tool with `subagent_type: general-purpose`). Each agent gets its lens prompt verbatim, with the run directory and the repo directory filled in. The repo directory is the current checkout when it holds the PR's commits, and `none` otherwise. Agents never see each other's output.

If one agent fails or returns malformed output, re-dispatch that agent only. If the host cannot run subagents, run the six lenses inline, one after another, and say so in the report.

## Step 4 - Synthesize (main agent)

Merge the lens claims per cluster. When two lenses disagree, keep the claim with the stronger evidence and record the other in the report. Drop any claim with no evidence.

Pick one verdict:

| Verdict                              | Signals                                                                                                                                                                        |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| keep patching                        | Findings per round are falling. Few findings are fix-on-fix. Clusters are unrelated edge cases.                                                                                |
| refactor in this PR                  | One or two clusters share one structural cause. A contained refactor removes the whole class of finding.                                                                       |
| split the PR                         | Clusters map to separate concerns. Each part could converge alone.                                                                                                             |
| restart with new requirements        | One requirement drives most clusters, and relaxing it removes them.                                                                                                            |
| move the change to another component | One or more clusters fight over behavior that another component owns or should own. Using or adding it there removes those clusters, at a cost below the rounds already spent. |

The "move the change to another component" verdict names its target and one of three forms:

- **use what exists:** this PR calls a capability the other component already has. No other component releases anything.
- **change the provider:** a backend, external API, or library gains or exposes the behavior. This PR waits for that release, or ships behind it.
- **change the consumer:** a frontend or other client takes over the behavior, and this PR drops it.

It may combine with another verdict, for example "move the change to another component, then restart with new requirements". Name both verdicts, and the order.

Weigh the claims with these rules:

- A claim that needs work in another component is not weaker for that reason. Compare its cost with the findings and rounds it removes.
- A verified claim outranks an unverified one. This holds on one cluster and when you choose the verdict. An unverified claim never selects the verdict, even when no verified claim competes with it. Report it as a proposal to check first.
- A standing rule in `requirements.md` may prefer the other component. Then a verified claim for that component beats an in-repo workaround that removes the same findings. This holds even when the workaround costs less.
- "Use what exists" beats "change the provider" when both remove the same findings. It needs no release in another component.

Name the runner-up and state why it lost.

## Step 5 - Report and save

Write the report in this shape:

1. **Verdict** and a one-paragraph reason.
2. **Churn numbers:** rounds, findings per round, the fix-on-fix share, hot files, and unposted findings.
3. **Clusters:** each with its findings, the lens claims, and the diagnosis.
4. **Proposed changes:** each requirement change names the requirement and the cost of keeping it. It also names what relaxing it removes, and what the user loses. Each change in another component names the target and its direction. It also names what exists today (`file:line` or URL), what is missing, and the release order.
5. **Runner-up verdict** and why it lost.
6. **Excluded noise:** a count per reviewer.

Every claim cites evidence: a comment URL, a commit SHA, or `file:line`. Every sentence that says another component lacks something cites its evidence, or says `(unverified)`. Save the report with the `plan-keeper:plan-save` skill as `Kind: design`. If plan-keeper is not installed, write it to `<run dir>/report.md`. Print the verdict and the saved path.

## Never

- **Don't edit code, commit, push, or reply to review comments, in this repo or any other.** The verdict is a proposal for the user.
- **Don't ask the user questions before the verdict.** Requirement changes go in the report as proposals.
- **Don't count noise as churn.** A round of bot notices is not a review round.
- **Don't diagnose from finding titles alone.** Read the bodies, the commit messages, and the diff.
- **Don't claim another component lacks a capability without citing its source or its documentation.** Write `(unverified)` when you could read neither.
- **Don't run a code review.** New defects are out of scope. `/wild-pr:review` does that.
