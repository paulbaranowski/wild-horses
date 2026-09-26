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
> - `boundaries.json` - the components this repo talks to: sibling repos, HTTP services, libraries, and API schemas. Each entry says how to read it.
>
> The repository is at `<repo dir>`. Run `git -C <repo dir> show <sha>` to see what a fix commit changed. If `<repo dir>` is `none`, read files with `gh api repos/<owner>/<repo>/contents/<path>?ref=<sha>` instead.
>
> To read another component, use its `local_path` first. Next try `gh api repos/<gh_slug>/contents/<path>`, and last its `doc_url`.
>
> Answer only your lens question, below, for each cluster where it applies. Skip a cluster your lens has nothing to say about.
>
> - **Don't propose a fix for a single finding.** Propose changes that remove a whole cluster, or say that none exists.
> - **Don't state a claim without evidence.** Cite a finding id, a commit SHA, or `file:line` for every claim.
> - **Don't edit files, commit, or post anything.**
> - **Don't claim another component lacks something without evidence.** Cite its `file:line` or a documentation URL, or end the sentence with `(unverified)`.

## state-ownership

> **Lens: state ownership.** Do several code paths write the same state with no single owner? Look for findings about races, ordering, stale reads, or one write undoing another. For each such cluster, name the state, and list every writer with `file:line`. Then say what single owner would make the races impossible. Examples of an owner are one locked transition function, a state machine, a queue, a database constraint, or a version or sequence that another component assigns. Say how many of the cluster's findings that owner would have prevented.

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

## placement

> **Lens: placement.** For each cluster, which component should own the behavior it keeps fixing? Read `boundaries.json`, then the source or documentation of each component the cluster touches. Check providers (backends, external APIs, libraries) and consumers (frontends, other clients) alike. For each cluster that belongs somewhere else, state:
>
> - the owning component and its direction;
> - whether the owner already provides the behavior (**use what exists**), or would need to gain it (**change the provider** or **change the consumer**), citing `file:line` or a documentation URL for what exists today;
> - what the change removes from this PR, and which findings it would have prevented;
> - what it costs: a migration, the release order, other clients of that component, or a new dependency;
> - any standing rule in `requirements.md` that covers the boundary, quoted.
>
> Mark a claim unverified when you could read neither the source nor the documentation. If every cluster belongs in this repo, say so and stop.

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
>       "confidence": "high | medium | low",
>       "verified": true,
>       "target": "<placement only: the owning component's name from boundaries.json>",
>       "form": "<placement only: use what exists | change the provider | change the consumer>"
>     }
>   ]
> }
> ```
>
> Return at most 5 claims, strongest first. An empty `claims` list is a valid answer.
>
> Set `verified` to false when any statement about another component has no `file:line` or documentation URL. Only the placement lens sets `target` and `form`. Other lenses leave them out.
