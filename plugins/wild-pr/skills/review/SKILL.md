---
name: review
description: Code review of a diff, branch, or PR, with findings posted as anchored PR comments. Use when the user asks to review a diff, branch, or PR, asks to check a change against its ticket/spec/PRD, or runs /wild-pr:review [pr-number-or-url] [--effort low|high] [--report].
user-invocable: true
disable-model-invocation: true
argument-hint: "[pr-number-or-url] [--effort low|high] [--report]"
---

# PR Review

Review a diff against one rubric and filter to the few findings worth raising. Gate with the user, then optionally post as anchored PR comments. `--report` replaces the gates with a findings report for an agent caller. Two engines share everything except how the rubric is applied:

- **low** - one reviewer subagent, single pass. Default.
- **high** - parallel reviewer subagents, one debate round, moderator filter. For large or high-stakes diffs.

## Invocation

- `/wild-pr:review` - review the current branch. This resolves the branch's open PR if any, and otherwise diffs against the default branch.
- `/wild-pr:review <pr-number-or-url>` - review that PR without checking it out; forces reviewer mode. Accepts a bare number (current repo) or full GitHub URL (identifies owner/repo).
- `--effort low|high` - pick the engine explicitly. Phrases also select: "quick"/"fast" → low; "deep"/"thorough"/"multi-perspective" → high.
- `--report` - non-interactive: stop after Synthesize and return the findings to the caller. No user gates, no posting, no implementing. For agent callers.

**Effort auto-select** (no flag, no phrase): `high` when the diff exceeds 20 changed files or 600 changed lines, else `low`. Before reviewing, print one line: `Effort: <low|high> (<N> files, <M> lines; override with --effort <other>)`. The line lets the user interrupt.

## Scope

### Path A - PR-argument fast path (argument provided)

- Parse the argument: bare integer → `gh repo view --json nameWithOwner --jq .nameWithOwner`; URL → extract `<owner>/<repo>` and `<N>`.
- Do **not** check out the PR branch.
- Mode is **locked to reviewer**.

Gather in parallel:

```bash
gh pr view <N> --repo <owner>/<repo> --json number,url,title,body,baseRefName,author,headRefOid,files
gh pr diff <N> --repo <owner>/<repo>
gh api user --jq .login
```

If `pr.author.login == viewer.login`, still proceed in reviewer mode, but flag this in Summary. The flag lets the user switch to a current-branch run on their checkout if they meant to implement.

### Path B - current-branch path (no argument)

- Open PR for current branch → review that PR. Base = `baseRefName`, diff = `git diff $base...HEAD`. Context = PR title + body.
- No PR → diff vs default branch (`main`, fall back to `master`). Context = `git log --format='%h %s%n%n%b' $base..HEAD`.
- **Never** review uncommitted working-tree changes. Empty diff → stop and report.

Determine **mode**:

- PR exists and `pr.author.login == viewer.login` → **author mode**.
- PR exists and authors differ → **reviewer mode**.
- No PR → **author mode**.

**Persistence:** both efforts persist for subagents into a fresh per-run directory: `RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pr-review.XXXXXX")`. The fresh directory means concurrent sessions never clobber each other. Write diff → `$RUN_DIR/diff.patch`, context → `$RUN_DIR/context.md`, changed files → `$RUN_DIR/files.txt`. Write metadata (PR number/url/base/author, viewer, head SHA, owner/repo, mode, `context_ref`) → `$RUN_DIR/meta.json`.

## Freshness preflight (mandatory before reading code)

Stale local state produces false-positive findings. Before reviewing, verify the ref you'll read for context is current.

Run on the **primary repo** (the one containing the diff):

```bash
git fetch origin "$base" --quiet
git rev-parse --abbrev-ref HEAD
git status --porcelain
git rev-list --left-right --count "HEAD...origin/${base}"
git log -1 --format='%h %ci' "origin/${base}"
```

Decide `context_ref`:

- **Path A (reviewer):** `context_ref = origin/${base}`. The worktree may be used only when `HEAD == origin/${base}` AND clean AND fetch succeeded.
- **Path B (author):** `context_ref = origin/${base}`. The local feature branch IS the diff; pre-PR context comes from `origin/${base}`.

Stop and ask the user when:

1. `git fetch` failed (offline, auth).
2. Path B: working tree is dirty AND dirty paths overlap the diff's changed-file list or anything you'll need to read.
3. Path B: `HEAD` is behind `origin/${base}` (any non-zero "behind") - findings may target code the base has already changed.

Warn template (substitute verified state):

> Freshness check for `<owner>/<repo>` at `<worktree-path>`:
>
> - on branch `<HEAD-branch>`.
> - `<N>` ahead, `<M>` behind `origin/<base>` (last: `<short-sha> <iso-date>`).
> - working tree: `<clean | dirty: N file(s)>`.
>
> Reading from this worktree may produce findings based on stale state.
> Reply: `proceed` (use worktree, accept the risk), `use-origin` (read context via `git show origin/<base>:<path>` - recommended), or `stop`.

**Never** run `git checkout`, `stash`, `reset`, or other state-modifying git on the user's behalf. The skill warns and asks; the user resolves local state. `git fetch` is allowed (read-only).

On Path A, local branch state never blocks the review - all reads go through origin refs, no checkout needed. When `HEAD` differs from `origin/${base}` or the tree is dirty, fetch the PR head to a local ref:

```bash
git fetch origin "pull/<N>/head:refs/remotes/origin/pr-<N>" --quiet
```

Then read PR-head content via `git show origin/pr-<N>:<path>` and base context via `git show origin/<base>:<path>`. No checkout needed.

### Reading code

When `context_ref = origin/<base>` (or `origin/pr-<N>` for PR head):

- Read via `git show "${context_ref}:<path>"` (whole files) or `git grep -n <pattern> "${context_ref}" -- <paths>` (search).
- Avoid reading the worktree filesystem for tracked content - it may be stale.
- Worktree reads are OK only for files brand-new in the diff (untracked at `context_ref`). Note "verified against: worktree" in the finding.

When `context_ref = worktree (stale, user accepted risk)`:

- Worktree reads are OK but every CRITICAL/MAJOR finding is downgraded to MINOR unless evidence is internal to the diff itself. Tag each finding with "verified against: worktree-stale".

## Diff classification (pick which lenses apply)

Walk the changed-file list. Activate lenses that match:

- **Security** triggers on: `routes/`, `controllers/`, `middleware*/`, files matching `auth*`/`*permission*`/`*acl*`/`*token*`/`*session*`, response serializers, OpenAPI/contract definitions, new API endpoint files.
- **Database** triggers on: `migrations/`, `*.sql`, files matching `schema*`, Mongoose/Prisma model files (`models/`, `*.model.ts`, `*.schema.ts`), repository/DAL files, query builders.
- **Frontend** triggers on: `*.tsx`, `*.jsx`, `*.css`, `*.scss`, `pages/`, `components/`, `hooks/`, or anything importing from `react`, `@tanstack/react-query`, or a design-system package.
- **Spec** triggers when a spec source exists. Look in order. (1) Issue/ticket references in the PR body or commit messages (`#123`, `Closes #45`, Linear/Jira keys). Fetch these via `gh` or the tracker. (2) A path the user passed as an argument. (3) A plan/PRD file under `docs/`, `specs/`, or `.scratch/` matching the branch or feature name. Nothing found → skip the lens and note "no spec available" in Summary.

Always-on lenses: **Engineering**, **Minimalism**, **Conventions**, **AntiSlop**.

## Review

The rubric lives in [references/review-rubric.md](references/review-rubric.md). It holds the severity ladder, `failure_mode` contract, do-not-raise list, NIT gate, caps, `suggested_fix` schema, and every lens checklist. It is binding for both engines.

### Low effort

Dispatch **one** reviewer subagent for an independent read, keeping the bulk content out of your context. Its prompt carries four things. The persisted file paths. The absolute path to references/review-rubric.md, with the instruction to read it in full. The active lens list. And the context-read contract from multi-agent.md §Dispatch mechanics, with `<context_ref>` substituted. Finding ids use an `R` prefix in place of roster letters. The subagent walks the diff and applies every active lens. The walk is done only when it has read every hunk under each active lens. Exhaustive reading, selective output. It returns _the smallest number of high-signal findings_ in the Round 1 output shape (multi-agent.md §Round 1). If the subagent cannot investigate an item with confidence, it flags the item instead of guessing. If the host cannot run subagents, do that same single pass yourself inline.

### High effort

Follow [references/multi-agent.md](references/multi-agent.md). Dispatch one reviewer agent per active lens in parallel; each reads only its own rubric section. Run one debate round, then moderator-filter. Return here for Synthesize.

## Filter (before synthesis)

Low effort: apply to the reviewer subagent's candidates. High effort: the moderator filter in multi-agent.md extends this list - apply that version.

1. **Drop findings with empty or hypothetical `failure_mode`.** "A future caller might…", "in case someone…" → drop.
2. **Drop do-not-raise matches.** For every finding, ask: does it match a do-not-raise item (rubric §Admission)? If it matches a `slop:` tag and you can't write a concrete, product-specific cost in one sentence - drop it.
3. **Apply the NIT gate.** NITs that don't meet it → drop. Kept NITs stay internal, hidden by default.
4. **Merge near-duplicates** under one finding (note which lens(es) surfaced it).
5. **Apply hard caps.** 6 actionable, 8 NITs retained; rest summarized as "N additional items omitted; ask for the full list."

Track dropped items in Withdrawn (one-liner each) so the user can see what was filtered.

## Synthesize

### Plain language

Write everything below that a human reads in plain language: the Summary, each finding's Point and Why-it-matters, and Disagreement one-liners. Four rules, adapted from ASD-STE100 Simplified Technical English:

- Keep each sentence to 20 words or fewer. Split a long sentence in two; never delete a word the reader must rebuild.
- Use active voice. Passive is fine when the actor is unknown or irrelevant.
- Use one word for one idea. Pick a word, then use only that word for it.
- Never use a figure of speech where a real name exists. Name the function, module, or table instead.

Real identifiers and real domain terms stay. Code spans, severity tags, and `file:lines` anchors are not prose; leave them exactly as the format specifies.

### Summary

One short paragraph: what the change does and your overall recommendation (ship / ship with changes / do not ship). When the Spec lens ran, add one line with its verdict: requirements met, missing, or diverging. Keep it separate, so a standards-clean diff can't mask a spec miss (and vice versa). When it was skipped, add "no spec available". Do not state the mode, engine, lenses applied, or convention sources consulted - that metadata is noise.

### Actionable

Up to 6 items. Format each:

- **[SEVERITY] Title** - `file:lines`
- **Point:** one sentence. Spec findings quote the spec line they're grounded in.
- **Why it matters:** the `failure_mode`.
- **Suggested fix (before → after):** two stacked fenced code blocks - first `// Before` (current code), then `// After` (replacement). Same language tag for both. For a pure deletion, write "_Delete these lines._" and show only `Before`. For a pure addition, show only `After` prefixed with `// Add after line <N>`. Omit when structural; explain in prose.
- **Lens(es):** which lens(es) surfaced this (high effort: which agents raised/agreed, by name).

Order by severity (CRITICAL → MAJOR → MINOR), then by file. Number items 1..N - these ids drive the gates.

### Disagreements (high effort only)

Items where agents substantively disagreed and did not converge. One sentence per side, attributed by agent name, then the moderator's call with a one-line reason.

### Nits

Do **not** print by default. Print only: _"N nit(s) available (M from convention audit)."_ Offer a "show the N NIT(s) first" option in gate 1. If N is 0, omit this section.

### Withdrawn

Terse one-liners of items dropped by the filter. Transparency only.

## Report mode (--report)

Stop after Synthesize: print the Summary, the Actionable list, and the retained NITs as one-liners, then end. The caller is an agent; the hide-nits default is for humans. No user gates, no posting, no implementing; the caller triages every finding itself and owns any fixes.

Earlier interactive checkpoints resolve to their safe defaults instead of prompting. The freshness preflight's stop-and-ask resolves as `use-origin`. On fetch failure, return an error to the caller instead of findings.

## User gate 1 - select items

Ask via the host's structured picker (`AskUserQuestion` in Claude Code). Hosts without one get a numbered text prompt with the same options. Render each actionable finding as an option, plus a trailing "Show the N NIT(s) first" option when N > 0. Set `multiSelect: true`. Do not proceed until the user answers.

Option-label format: `<id> - <short title> (<SEVERITY>)`. The `description` is the one-sentence failure_mode, not the fix.

- **Zero findings selected** → end with a one-line "no findings selected - nothing to do".
- **Only "Show the N NIT(s) first"** → print the NITs (one-liners with `file:lines` and `[CONVENTION]`/`[SPEC]` tags), then re-ask this gate.
- **Findings selected** → proceed to gate 2 (print NITs first if the toggle was also checked).

## Branch on mode

Gate 2 is mandatory in both modes - never auto-apply fixes and never auto-post reviews.

### Author mode

**Plan.** For the selected ids only, produce an ordered implementation plan. List steps, files touched per step, tests to add/update, and verification commands. Do **not** edit files yet.

**User gate 2 (author)** - structured picker, `multiSelect: false`, options:

- `Implement locally` - apply the plan against the checkout.
- `Post as review` - post anchored comments on your own PR, then stop.
- `Both` - post the review first, then execute the plan.
- `Edit the plan` - revise, re-ask this gate.
- `Cancel` - stop.

**Execute.** Track each step with the host's task tracker. Apply the plan, run verification, report results. If a step reveals a new substantive issue not in the selected items, stop and ask before expanding scope.

### Reviewer mode

Skip the plan step - you are not implementing someone else's code.

**User gate 2 (reviewer)** - structured picker, `multiSelect: false`, options:

- `Post` - submit a single COMMENT review with the selected anchored comments.
- `Edit` - ask which to drop or refine, then re-ask.
- `Cancel` - stop.

## Posting an anchored PR review (both modes)

Check the review prose before you post it. Resolve the `plain-language` CLI once, in its own Bash call:

```bash
root="${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-}}"
cli="$root/../plain-language/scripts/plain_language_cli.py"
if [ ! -f "$cli" ]; then
  cli=$(ls -d "$root"/../../plain-language/[0-9]*/scripts/plain_language_cli.py 2>/dev/null | sort -V | tail -1)
fi
if [ -f "$cli" ]; then (cd "$(dirname "$cli")" && printf '%s/%s\n' "$(pwd -P)" "$(basename "$cli")"); else echo ABSENT; fi
```

The first path is the dev checkout, where plugins are siblings. The second is the install cache, which adds a version directory. `sort -V` picks the highest of the numeric version directories. The `cd`/`pwd -P` step prints an absolute path with no `..` segments, which the approval hook needs to match.

`ABSENT` means the plugin is not installed. It can also mean `${CLAUDE_PLUGIN_ROOT}` was not substituted in this context. Before you skip, try `Glob "**/plain-language/scripts/plain_language_cli.py"`, then `Glob "**/plain-language/*/scripts/plain_language_cli.py"`. Use the first match. Skip the check only when both find nothing. Then say so in the final message and post as usual.

When the path resolves, write the review body and every anchored comment body into `$RUN_DIR/review-body.md`. That is the per-run directory from the Persistence rule, so concurrent sessions never overwrite each other. Then scan that file once:

```bash
python3 "<resolved cli path>" scan "<RUN_DIR>/review-body.md"
```

One scan covers every finding. Use literal paths in each scan. A shell variable does not survive to the next Bash call, so `$cli` and `$RUN_DIR` are both empty there.

Rewrite every `long-sentence` and `em-dash` hit, then scan again. Stop when both reach zero, or after 5 passes. Report any violation that survives 5 passes, and post anyway. Never drop a `file:line` anchor, a severity, or a tag to meet the cap. The other six kinds are candidates you judge, and they never gate posting.

This check runs on the posting path only. `--report` mode stops before posting and returns findings to an agent caller, so no human reads that prose.

On approval, submit a **single** review via the GitHub Reviews API (`event: COMMENT` - never `APPROVE`/`REQUEST_CHANGES`). Anchor every selected item as an inline comment on its diff line. Never use loose issue comments.

Follow [references/posting-pr-review.md](references/posting-pr-review.md) exactly. It has the `gh api` call, the payload shape, the three-block review body, and the per-comment body budget and format. After posting, print the review `html_url` and a one-line count of posted comments. Include the fallback general-notes count if any.
