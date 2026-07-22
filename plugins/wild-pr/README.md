# wild-pr

Open a pull request with an architecture-first description, review it against a rubric, and babysit it through CI and review feedback.

Install:

```text
/plugin install wild-pr@wild-horses
```

## Skills

| Skill                                          | Invoke                    | What it does                                                                                                                                                                                                        |
| ---------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **[`wild-pr`](skills/wild-pr/)**               | `/wild-pr`                | Opens a PR for the current branch (title/body via `summary-writer`), then runs `babysit` up to three times, owning the loop itself and stopping early on a clean pass.                                              |
| **[`review`](skills/review/)**                 | `/wild-pr:review`         | Reviews a diff, branch, or PR against one rubric (low-effort single agent or high-effort parallel-lens agents with a debate round), gates with the user, optionally posts anchored PR comments.                     |
| **[`babysit`](skills/babysit/)**               | `/wild-pr:babysit`        | One tending pass: fixes high-confidence CI failures, replies to every unresolved review thread and conversation comment with a verdict (Agree/Disagree/Already fixed/Defer), reports `clean`/`progressing`/`stuck`. |
| **[`summary-writer`](skills/summary-writer/)** | `/wild-pr:summary-writer` | Writes a PR title and description that lead with the one structural idea, re-derived from the net diff every run - never a file-by-file changelog.                                                                  |

Skills aren't shown with their plugin namespace in the `/` menu, but the commands above route through the same-named `commands/*.md` passthrough files, which is why they carry the `wild-pr:` prefix.

## How it works

`review`, `babysit`, and `wild-pr` compose - `wild-pr` calls `summary-writer` then `babysit` in sequence within the same session, always reading each dependency's `SKILL.md` fresh rather than paraphrasing it from memory.

`babysit` is backed by a bundled CLI, `scripts/pr_babysit_cli.py`, with subcommands `review` (fetch unresolved threads, automated review bodies, and conversation comments as structured JSON), `failed-logs` (stream failing-check output), `commit-push` (explicit-staging commit + push, never `git add -A`), `reply` (post a threaded reply), and `comment` (post a top-level PR summary). A PreToolUse hook (`hooks/hooks.json` + `scripts/pr-babysit-cli-allow.sh`) auto-approves bounded invocations of that CLI - it rejects flags, chaining, and substitution, so it isn't a blanket approval.

Every reply the CLI posts carries a sentinel footer (`pr-babysit:addressed`) so re-runs can tell which threads are already handled without re-fetching everything; a separate `pr-babysit:followup` sentinel tags Deferred (real but out-of-scope) findings so they're easy to grep for later.
