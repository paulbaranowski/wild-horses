---
name: plan-jira
description: Use when the user asks to push a plan to Jira, file a Jira ticket/issue from a plan, push the plan to Jira, or update an existing Jira ticket from a plan. Supports first-time Jira setup inline.
---

# plan-jira

Push a saved plan markdown file as a Jira ticket description. On first push, write the `Jira Ticket` id into the file's frontmatter so the same plan can be re-pushed (updates the existing ticket instead of creating a new one). Per-repo config — including the API token — lives at `~/plans/<repo>/.plankeeper.json` (see [`../../ticket-systems.md`](../../ticket-systems.md)).

The bundled `plan_keeper_cli.py` does every mutation: config CRUD, metadata fetch, frontmatter read/write, and the actual Jira API calls. This skill's job is the user-facing orchestration: arg parsing, file selection, setup wizard prompts, and final confirmation.

## Invocation

```text
/plan-jira [last|file]
```

The mode arg is optional.

## Procedure

### 1. Parse the user's args

Tokenize the invocation. Recognize `last` and `file` as the **mode arg**. Anything else is an error (unrecognized argument). The mode arg may be absent.

### 2. Resolve the repo and check Jira config

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" repo name
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" jira config get
```

The first returns `<repo>` (e.g., `herds`). The second exits 0 (Jira is configured — its section prints as JSON) or exits 3 (not configured). On exit 3, go to step 3 (setup). On exit 0, continue to step 4.

### 3. Inline setup wizard (only if Jira isn't configured)

This step is only reached when `jira config get` exited 3. Walk the user through it:

#### 3.1 — Get the credentials

AskUserQuestion: "Paste your Jira API token" (free-text via the "Other" path). Also ask for `site` (e.g., `herds.atlassian.net`) and `email` separately.

#### 3.2 — Validate the credentials

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" jira api viewer \
  --api-key <K> --site <s> --email <e>
```

- On non-zero exit (codes 3 / 4 / 5): surface the stderr message to the user, re-ask. After 3 failures, abort the whole flow.
- On exit 0: stdout contains the authenticated identity as JSON.

#### 3.3 — Refresh the cache

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" jira config refresh \
  --api-key <K> --site <s> --email <e>
```

This is one CLI call but it internally fetches projects/components/users/issuetypes per-project. The cache now contains everything the picker needs.

#### 3.4 — Pick defaults

Read the cache:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" jira config get
```

For each pickable field, AskUserQuestion with the cached list. The order matters because later picks depend on earlier ones:

1. Project → use `cache.projects`.
2. Components (multi-select) → `cache.components` filtered to `projectKey == picked_project`.
3. Assignee → `cache.users` filtered to that project. Default to identity from step 3.2.
4. Issue type → `cache.issueTypes` filtered to that project's `id`.
5. Labels → free-text, comma-separated (Jira labels are flat workspace strings, no scoping).

#### 3.5 — Save the section

Assemble the JSON object combining credentials + defaults + cache (the cache is already in the live config from step 3.3 — just preserve it):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" jira config save <<'EOF'
{
  "site": "...",
  "email": "...",
  "apiToken": "...",
  "defaults": { ... picked fields ... },
  "cache": { ... preserved from refresh ... }
}
EOF
```

#### 3.6 — Confirm to user

> Set up Jira: project HERDS / components Backend / assignee Paul Baranowski / issue type Task.

Continue to step 4.

### 4. Resolve the target plan

Branch on the mode arg.

#### 4.a — Mode arg absent

AskUserQuestion: "Push the last plan from this conversation, or pick a file?" with options `last` / `file`. Set the mode arg accordingly and continue.

#### 4.b — Mode `last`

Find the last plan in the conversation:

1. The plan the user just pasted and pointed at in the invocation.
2. The most recent `ExitPlanMode` plan.
3. The most recent "Design"/"Plan"/"Approach" section the assistant produced.
4. A substantial numbered or bulleted markdown outline.

If multiple plausible candidates, ask the user which.

**If the plan isn't already on disk** (no path known), run `plan_keeper_cli.py save --topic "<H1>"` with the plan body on stdin. Use the returned path. Announce: "Saving to `<path>` and pushing to Jira..."

#### 4.c — Mode `file`

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list --state active
```

**Run this command fresh every time you reach this step — including on a re-invocation later in the same conversation.** Never reprint an earlier listing from memory: plans get saved, archived, or change status between turns, so a cached list can be stale, and the user picks by number. The numbered list you show must come from the output you just ran.

Print the result as a numbered list (1-indexed). Ask the user "Which one? (1-N, or 'cancel')". Re-prompt on invalid input. On "cancel": abort.

### 5. Read existing ticket reference

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta get --file <path>
```

Returns JSON with `Plan-keeper Ticket`, `Linear Ticket`, `Jira Ticket`, `Completed on`, etc. Empty strings if not present.

### 6. Confirm the push

Show one of these summaries via AskUserQuestion (Push / Cancel):

- **No existing ticket:** "Will **create** a new Jira ticket from `<filename>`: `<H1 text>` in project `<project>`, issue type `<type>`, assigned to `<assignee>`."
- **Existing Jira ticket:** "Will **update** existing Jira ticket `<ID>` from `<filename>`: title → `<H1 text>`."
- **Existing ticket in a different system (linear):** "File references a `linear` ticket `<ID>`. Three options:
  1. Create new in Jira and overwrite the reference (V1 supported)
  2. Cancel
     (Keeping both references is a V2 feature, not in scope for now.)"

On Cancel: stop. Leave file unchanged.

### 7. Execute the push

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" jira push \
  --file <path> [--force-new if step 6 chose option 1]
```

`--ticket <id>` is an alternative to `--file`: it locates the plan by any of its id fields (`Plan-keeper Ticket` / `Linear Ticket` / `Jira Ticket`) across all repos (exactly one of the two is required).

Returns JSON on stdout: `{"action": "create"|"update", "id": "...", "url": "...", "title": "...", "system": "jira"}`.

**Error handling** — branch on exit code:

- **3** (auth): "Jira credentials are no longer valid. Re-run `/plan-jira` to refresh credentials." Abort.
- **4** (network): "Couldn't reach Jira: `<stderr>`. Plan file unchanged. The ticket may or may not have been created — verify in Jira before retrying." Abort.
- **5** (API error, including 404 in update mode): if the error indicates the ticket doesn't exist anymore, AskUserQuestion "Configured ticket `<ID>` no longer exists. Create a fresh ticket? [yes/no]". On yes, retry step 7 with `--force-new`. On no, abort.

### 8. Write back the ticket reference

Only on `action == "create"` (updates don't change frontmatter):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta set \
  --file <path> --jira-ticket <result.id>
```

If this fails (extremely rare — atomic local write): show the ticket URL from step 7's stdout and warn:

> Created ticket `<URL>` but couldn't update local frontmatter: `<error>`. Manually add to top of `<path>`:
>
> ```yaml
> ---
> Jira Ticket: <ID>
> ---
> ```

### 9. Confirm to user

Two lines:

> Pushed to `<URL>`
> File updated: `<path>`

## Content discipline

- **Don't** modify the plan body before pushing — the CLI strips its own frontmatter and prepends `Repo: <owner>/<name>` automatically.
- **Don't** call any Jira API directly from the SKILL.md. Every network call goes through the CLI.
- **Don't** edit `.plankeeper.json` with `Edit` or `Write`. Use `jira config save` so atomic write + chmod 600 happen.

## Common mistakes

- **Forgetting to update frontmatter after create.** Step 8 is non-optional on create — without it, the next push will create a duplicate. The flow is: push → write back → confirm. Skipping any one of these is a bug.
- **Picking components or assignee before picking project.** Jira components and assignable users are project-scoped. The project pick must precede them so the filter has a value to apply.
- **Treating exit code 5 as a hard failure.** Code 5 is "API said no" — for update mode this often means "ticket not found," which is recoverable by offering `--force-new`. Look at stderr before aborting.
- **Hand-typing a JSON config and pushing.** The setup wizard is mandatory for first run because it validates the credentials and refreshes the cache.

## Notes

- The `~/plans/<repo>/.plankeeper.json` config is local to the user's machine and includes the API token. It is never committed and never read from anywhere other than the per-repo path.
- Plans pushed to Jira render as a single block of plain text — see [`../../ticket-systems.md`](../../ticket-systems.md) for the V1 limitation rationale.
- Sibling skill `plan-linear` does the same for Linear; both share the bundled CLI and the `~/plans/<repo>/` tree.
