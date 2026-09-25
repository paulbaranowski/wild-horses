# Lens agents

`SKILL.md` Step 3 dispatches one agent per lens, all in parallel. Each prompt is the shared preamble, then that lens's section, then the output contract. Send all three verbatim, with `<run dir>` and `<repo dir>` replaced by literal paths.

## Shared preamble

> You are diagnosing why a pull request keeps going through review rounds without converging. You are not reviewing it for new bugs.
>
> Read these files in `<run dir>`:
>
> - `timeline.json` - commits, review rounds, and every finding with its full body.
> - `triage.md` - noise already excluded, unposted findings, each finding marked new or fix-on-fix, and the root-cause clusters.
> - `requirements.md` - what the PR had to satisfy, with sources.
> - `diff.patch` - the PR's current diff.
>
> The repository is at `<repo dir>`. Run `git -C <repo dir> show <sha>` to see what a fix commit changed. If `<repo dir>` is `none`, read files with `gh api repos/<owner>/<repo>/contents/<path>?ref=<sha>` instead.
>
> Answer only your lens question, below, for each cluster where it applies. Skip a cluster your lens has nothing to say about.
>
> - **Don't propose a fix for a single finding.** Propose changes that remove a whole cluster, or say that none exists.
> - **Don't state a claim without evidence.** Cite a finding id, a commit SHA, or `file:line` for every claim.
> - **Don't edit files, commit, or post anything.**

## state-ownership

> **Lens: state ownership.** Do several code paths write the same state with no single owner? Look for findings about races, ordering, stale reads, or one write undoing another. For each such cluster, name the state, and list every writer with `file:line`. Then say what single owner would make the races impossible. Examples of an owner are one locked transition function, a state machine, a queue, or a database constraint. Say how many of the cluster's findings that owner would have prevented.

## encapsulation

> **Lens: encapsulation.** Do callers repeat an invariant that one type or function should enforce? Look for the same check, guard, or re-read added at several call sites across fix commits. For each such cluster, name the invariant and list the call sites. Say which type or function should own it, and what its interface would be. Say which findings the change would have prevented.

## requirements

> **Lens: requirements.** Which requirements force the complexity behind the clusters? Read `requirements.md` and trace each cluster back to the requirement that makes it necessary. For each such requirement, state:
>
> - the requirement, quoted with its source;
> - the clusters it drives;
> - a relaxed version of it;
> - what relaxing it removes, as code paths, states, or findings;
> - what the user or product loses.
>
> Treat a requirement as fixed only when a source says why it must hold.

## scope

> **Lens: scope.** Does the PR mix concerns that could ship apart? Map each cluster to the concern it belongs to. If the clusters fall into separate concerns, propose split lines. Name each part, what it contains, the order to ship the parts in, and which clusters each part carries. Say whether each part could converge alone. If the PR is one concern, say so and stop.

## simplification

> **Lens: simplification.** Could a smaller design meet the same requirements? Describe the smallest design you can find that satisfies `requirements.md`. Say what it deletes from the current diff and which clusters disappear with it. Say what it costs, and which requirement it fails, if any. If the current design is already the smallest, say so and stop.

## Output contract

> Return JSON only, no prose around it:
>
> ```json
> {
>   "lens": "<lens name>",
>   "claims": [
>     {
>       "cluster": "<cluster name from triage.md>",
>       "claim": "<one sentence: what is wrong or what could change>",
>       "evidence": ["<finding id, commit SHA, or file:line>"],
>       "proposal": "<the change, in two sentences or fewer>",
>       "removes": ["<finding ids or code paths this change removes>"],
>       "cost": "<what the change costs or what the user loses>",
>       "confidence": "high | medium | low"
>     }
>   ]
> }
> ```
>
> Return at most 5 claims, strongest first. An empty `claims` list is a valid answer.
