---
name: plan-push
description: Use when the user asks to push a plan to Linear or Jira, file a ticket from a plan, push the plan to a ticket system, push a plan as a Linear/Jira issue, or update an existing ticket from a plan. Supports first-time setup inline.
---

# plan-push

Push a saved plan markdown file as a Linear or Jira ticket description. On first push, write `Ticket` and `Ticket System` into the file's frontmatter so the same plan can be re-pushed (updates the existing ticket instead of creating a new one). Per-repo config — including API keys — lives at `~/plans/<repo>/.plankeeper.json` (see [`../../ticket-systems.md`](../../ticket-systems.md)).

The bundled `plan_keeper_cli.py` does every mutation: config CRUD, metadata fetch, frontmatter read/write, and the actual Linear/Jira API calls. This skill's job is the user-facing orchestration: arg parsing, system disambiguation, file selection, setup wizard prompts, and final confirmation.

## Invocation

```text
/plan-push [linear|jira] [last|file]
```

Both positional args are optional. Position doesn't matter — `/plan-push last linear` ≡ `/plan-push linear last`.

## Procedure

### 1. Parse the user's args

Tokenize the invocation. Recognize four tokens:

- `linear`, `jira` → **system arg**
- `last`, `file` → **mode arg**

Anything else is an error (unrecognized argument). Either or both may be absent.

### 2. Resolve the repo and configured systems

Call:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" repo
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" ticket-system-config list
```

The first returns `<repo>` (e.g., `herds`). The second returns a JSON array of configured systems (e.g., `["linear"]`, `["linear", "jira"]`, or `[]`).

### 3. Resolve the target system

Apply the disambiguation rules:

| Configured systems         | System arg     | Action                                                                          |
| -------------------------- | -------------- | ------------------------------------------------------------------------------- |
| `[]` (none)                | absent         | AskUserQuestion: "Set up Linear or Jira?" → go to step 4 with the chosen system |
| `[]` (none)                | present        | Go to step 4 with the named system                                              |
| `["linear"]` (one)         | absent         | Use `linear` silently. Continue to step 5.                                      |
| `["linear"]` (one)         | matches        | Use it. Continue to step 5.                                                     |
| `["linear"]` (one)         | other (`jira`) | Go to step 4 to set up `jira`                                                   |
| `["linear","jira"]` (both) | absent         | AskUserQuestion: "Push to Linear or Jira?"                                      |
| `["linear","jira"]` (both) | present        | Use it. Continue to step 5.                                                     |

### 4. Inline setup wizard (only if needed)

This step is only reached when the target system isn't already in `.plankeeper.json`. Walk the user through it:

#### 4.1 — Get the API key

AskUserQuestion: "Paste your `<sys>` API key" (free-text via the "Other" path).

For Jira, also ask for `site` (e.g., `herds.atlassian.net`) and `email` separately.

#### 4.2 — Validate the API key

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" ticket-api viewer \
  --name <sys> --api-key <K> [--site <s> --email <e> for jira]
```

- On non-zero exit (codes 3 / 4 / 5): surface the stderr message to the user, re-ask. After 3 failures, abort the whole flow.
- On exit 0: stdout contains the authenticated identity as JSON.

#### 4.3 — Refresh the cache

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" ticket-system-config refresh \
  --name <sys> --api-key <K> [--site <s> --email <e> for jira]
```

This is one CLI call but it internally fetches teams/projects/labels/users (Linear) or projects/components/users/issuetypes per-project (Jira). The cache now contains everything the picker needs.

#### 4.4 — Pick defaults

Read the cache:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" ticket-system-config get --name <sys>
```

For each pickable field, AskUserQuestion with the cached list. The order matters because later picks depend on earlier ones:

**Linear:**

1. Team → use `cache.teams` for choices.
2. Project → use `cache.projects` filtered to `teamIds.includes(picked_team)`. Allow "(none)".
3. Assignee → use `cache.users`. Default the picker to the identity from step 4.2.
4. Labels (multi-select) → use `cache.labels` filtered to `teamId == picked_team or teamId == null` (workspace-wide labels always shown; team-scoped labels filtered).

**Jira:**

1. Project → use `cache.projects`.
2. Components (multi-select) → `cache.components` filtered to `projectKey == picked_project`.
3. Assignee → `cache.users` filtered to that project. Default to identity from step 4.2.
4. Issue type → `cache.issueTypes` filtered to that project's `id`.
5. Labels → free-text, comma-separated (Jira labels are flat workspace strings, no scoping).

#### 4.5 — Save the section

Assemble the JSON object combining credentials + defaults + cache (the cache is already in the live config from step 4.3 — just preserve it):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" ticket-system-config save --name <sys> <<'EOF'
{
  "apiKey": "...",
  "defaults": { ... picked fields ... },
  "cache": { ... preserved from refresh ... }
}
EOF
```

#### 4.6 — Confirm to user

> Set up `<sys>`: team Engineering / project Backend Refactor / assignee Paul Baranowski.

Continue to step 5.

### 5. Resolve the target plan

Branch on the mode arg.

#### 5.a — Mode arg absent

AskUserQuestion: "Push the last plan from this conversation, or pick a file?" with options `last` / `file`. Set the mode arg accordingly and continue.

#### 5.b — Mode `last`

Find the last plan in the conversation:

1. The plan the user just pasted and pointed at in the invocation.
2. The most recent `ExitPlanMode` plan.
3. The most recent "Design"/"Plan"/"Approach" section the assistant produced.
4. A substantial numbered or bulleted markdown outline.

If multiple plausible candidates, ask the user which.

**If the plan isn't already on disk** (no path known), run `plan_keeper_cli.py save --topic "<H1>"` with the plan body on stdin. Use the returned path. Announce: "Saving to `<path>` and pushing to `<sys>`..."

#### 5.c — Mode `file`

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" list --state active
```

**Run this command fresh every time you reach this step — including on a re-invocation later in the same conversation.** Never reprint an earlier listing from memory: plans get saved, archived, or change status between turns, so a cached list can be stale, and the user picks by number. The numbered list you show must come from the output you just ran.

Print the result as a numbered list (1-indexed). Ask the user "Which one? (1-N, or 'cancel')". Re-prompt on invalid input. On "cancel": abort.

### 6. Read existing ticket reference

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta get --file <path>
```

Returns JSON with `Ticket`, `Ticket System`, `Completed on`. Empty strings if not present.

### 7. Confirm the push

Show one of these summaries via AskUserQuestion (Push / Cancel):

- **No existing ticket:** "Will **create** a new `<sys>` ticket from `<filename>`: `<H1 text>` in team `<team>`, project `<project>`, assigned to `<assignee>`."
- **Existing ticket in same system:** "Will **update** existing `<sys>` ticket `<ID>` from `<filename>`: title → `<H1 text>`."
- **Existing ticket in different system:** "File references `<other-sys>` ticket `<ID>`. Three options:
  1. Create new in `<sys>` and overwrite the reference (V1 supported)
  2. Cancel
     (Keeping both references is a V2 feature, not in scope for now.)"

On Cancel: stop. Leave file unchanged.

### 8. Execute the push

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" push \
  --name <sys> --file <path> [--force-new if step 7 chose option 1]
```

Returns JSON on stdout: `{"action": "create"|"update", "id": "...", "url": "...", "title": "...", "system": "..."}`.

**Error handling** — branch on exit code:

- **3** (auth): "API key for `<sys>` is no longer valid. Re-run `/plan-push` to refresh credentials." Abort.
- **4** (network): "Couldn't reach `<sys>`: `<stderr>`. Plan file unchanged. The ticket may or may not have been created — verify in `<sys>` before retrying." Abort.
- **5** (API error, including 404 in update mode): if the error indicates the ticket doesn't exist anymore, AskUserQuestion "Configured ticket `<ID>` no longer exists. Create a fresh ticket? [yes/no]". On yes, retry step 8 with `--force-new`. On no, abort.

### 9. Write back the ticket reference

Only on `action == "create"` (updates don't change frontmatter):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta set \
  --file <path> --ticket <result.id> --ticket-system <sys>
```

If this fails (extremely rare — atomic local write): show the ticket URL from step 8's stdout and warn:

> Created ticket `<URL>` but couldn't update local frontmatter: `<error>`. Manually add to top of `<path>`:
>
> ```yaml
> ---
> Ticket: <ID>
> Ticket System: <sys>
> ---
> ```

### 10. Confirm to user

Two lines:

> Pushed to `<URL>`
> File updated: `<path>`

## Content discipline

- **Don't** modify the plan body before pushing — the CLI strips its own frontmatter and prepends `Repo: <owner>/<name>` automatically.
- **Don't** call any Linear/Jira API directly from the SKILL.md. Every network call goes through the CLI.
- **Don't** edit `.plankeeper.json` with `Edit` or `Write`. Use `ticket-system-config save` so atomic write + chmod 600 happen.

## Common mistakes

- **Forgetting to update frontmatter after create.** Step 9 is non-optional on create — without it, the next push will create a duplicate. The flow is: push → write back → confirm. Skipping any one of these is a bug.
- **Picking labels before picking team (Linear).** Linear labels are partly team-scoped. The team pick must precede the label pick so the filter has a value to apply.
- **Treating exit code 5 as a hard failure.** Code 5 is "API said no" — for update mode this often means "ticket not found," which is recoverable by offering `--force-new`. Look at stderr before aborting.
- **Hand-typing a JSON config and pushing.** The setup wizard is mandatory for first run because it validates the API key and refreshes the cache. A hand-typed config might have a wrong `teamId` that pushes succeed against silently (Linear doesn't validate `teamId` strictly on create — it just files under a default).

## Notes

- The `~/plans/<repo>/.plankeeper.json` config is local to the user's machine and includes API keys. It is never committed and never read from anywhere other than the per-repo path.
- Plans pushed to Linear render with full markdown formatting. Plans pushed to Jira render as a single block of plain text — see [`../../ticket-systems.md`](../../ticket-systems.md) for the V1 limitation rationale.
