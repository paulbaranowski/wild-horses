---
name: plan-linear
description: Use when the user asks to push a plan to Linear, file a Linear ticket/issue from a plan, push the plan to Linear, or update an existing Linear ticket from a plan. Supports first-time Linear setup inline.
---

# plan-linear

Push a saved plan markdown file as a Linear ticket description. On first push, write `Ticket` and `Ticket System` into the file's frontmatter so the same plan can be re-pushed (updates the existing ticket instead of creating a new one). Per-repo config — including the API key — lives at `~/plans/<repo>/.plankeeper.json` (see [`../../ticket-systems.md`](../../ticket-systems.md)).

The bundled `plan_keeper_cli.py` does every mutation: config CRUD, metadata fetch, frontmatter read/write, and the actual Linear API calls. This skill's job is the user-facing orchestration: arg parsing, file selection, setup wizard prompts, and final confirmation.

## Invocation

```text
/plan-linear [last|file]
```

The mode arg is optional.

## Procedure

### 1. Parse the user's args

Tokenize the invocation. Recognize `last` and `file` as the **mode arg**. Anything else is an error (unrecognized argument). The mode arg may be absent.

### 2. Resolve the repo and check Linear config

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" repo
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" linear config get
```

The first returns `<repo>` (e.g., `herds`). The second exits 0 (Linear is configured — its section prints as JSON) or exits 3 (not configured). On exit 3, go to step 3 (setup). On exit 0, continue to step 4.

### 3. Inline setup wizard (only if Linear isn't configured)

This step is only reached when `linear config get` exited 3. Walk the user through it:

#### 3.1 — Get the API key

AskUserQuestion: "Paste your Linear API key" (free-text via the "Other" path).

#### 3.2 — Validate the API key

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" linear api viewer --api-key <K>
```

- On non-zero exit (codes 3 / 4 / 5): surface the stderr message to the user, re-ask. After 3 failures, abort the whole flow.
- On exit 0: stdout contains the authenticated identity as JSON.

#### 3.3 — Refresh the cache

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" linear config refresh --api-key <K>
```

This is one CLI call but it internally fetches teams/projects/labels/users. The cache now contains everything the picker needs.

#### 3.4 — Pick defaults

Read the cache:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" linear config get
```

For each pickable field, AskUserQuestion with the cached list. The order matters because later picks depend on earlier ones:

1. Team → use `cache.teams` for choices.
2. Project → use `cache.projects` filtered to `teamIds.includes(picked_team)`. Allow "(none)".
3. Assignee → use `cache.users`. Default the picker to the identity from step 3.2.
4. Labels (multi-select) → use `cache.labels` filtered to `teamId == picked_team or teamId == null` (workspace-wide labels always shown; team-scoped labels filtered).

#### 3.5 — Save the section

Assemble the JSON object combining credentials + defaults + cache (the cache is already in the live config from step 3.3 — just preserve it):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" linear config save <<'EOF'
{
  "apiKey": "...",
  "defaults": { ... picked fields ... },
  "cache": { ... preserved from refresh ... }
}
EOF
```

#### 3.6 — Confirm to user

> Set up Linear: team Engineering / project Backend Refactor / assignee Paul Baranowski.

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

**If the plan isn't already on disk** (no path known), run `plan_keeper_cli.py save --topic "<H1>"` with the plan body on stdin. Use the returned path. Announce: "Saving to `<path>` and pushing to Linear..."

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

Returns JSON with `Ticket`, `Ticket System`, `Completed on`. Empty strings if not present.

### 6. Confirm the push

Show one of these summaries via AskUserQuestion (Push / Cancel):

- **No existing ticket:** "Will **create** a new Linear ticket from `<filename>`: `<H1 text>` in team `<team>`, project `<project>`, assigned to `<assignee>`."
- **Existing Linear ticket:** "Will **update** existing Linear ticket `<ID>` from `<filename>`: title → `<H1 text>`."
- **Existing ticket in a different system (jira):** "File references a `jira` ticket `<ID>`. Three options:
  1. Create new in Linear and overwrite the reference (V1 supported)
  2. Cancel
     (Keeping both references is a V2 feature, not in scope for now.)"

On Cancel: stop. Leave file unchanged.

### 7. Execute the push

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" linear push \
  --file <path> [--force-new if step 6 chose option 1]
```

`--ticket <id>` is an alternative to `--file`: it locates the plan by its `Ticket:` frontmatter across all repos (exactly one of the two is required).

Returns JSON on stdout: `{"action": "create"|"update", "id": "...", "url": "...", "title": "...", "system": "linear"}`.

**Error handling** — branch on exit code:

- **3** (auth): "Linear API key is no longer valid. Re-run `/plan-linear` to refresh credentials." Abort.
- **4** (network): "Couldn't reach Linear: `<stderr>`. Plan file unchanged. The ticket may or may not have been created — verify in Linear before retrying." Abort.
- **5** (API error, including 404 in update mode): if the error indicates the ticket doesn't exist anymore, AskUserQuestion "Configured ticket `<ID>` no longer exists. Create a fresh ticket? [yes/no]". On yes, retry step 7 with `--force-new`. On no, abort.

### 8. Write back the ticket reference

Only on `action == "create"` (updates don't change frontmatter):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta set \
  --file <path> --ticket-id <result.id> --ticket-system linear
```

If this fails (extremely rare — atomic local write): show the ticket URL from step 7's stdout and warn:

> Created ticket `<URL>` but couldn't update local frontmatter: `<error>`. Manually add to top of `<path>`:
>
> ```yaml
> ---
> Ticket: <ID>
> Ticket System: linear
> ---
> ```

### 9. Confirm to user

Two lines:

> Pushed to `<URL>`
> File updated: `<path>`

## Content discipline

- **Don't** modify the plan body before pushing — the CLI strips its own frontmatter and prepends `Repo: <owner>/<name>` automatically.
- **Don't** call any Linear API directly from the SKILL.md. Every network call goes through the CLI.
- **Don't** edit `.plankeeper.json` with `Edit` or `Write`. Use `linear config save` so atomic write + chmod 600 happen.

## Common mistakes

- **Forgetting to update frontmatter after create.** Step 8 is non-optional on create — without it, the next push will create a duplicate. The flow is: push → write back → confirm. Skipping any one of these is a bug.
- **Picking labels before picking team.** Linear labels are partly team-scoped. The team pick must precede the label pick so the filter has a value to apply.
- **Treating exit code 5 as a hard failure.** Code 5 is "API said no" — for update mode this often means "ticket not found," which is recoverable by offering `--force-new`. Look at stderr before aborting.
- **Hand-typing a JSON config and pushing.** The setup wizard is mandatory for first run because it validates the API key and refreshes the cache. A hand-typed config might have a wrong `teamId` that pushes succeed against silently (Linear doesn't validate `teamId` strictly on create — it just files under a default).

## Notes

- The `~/plans/<repo>/.plankeeper.json` config is local to the user's machine and includes the API key. It is never committed and never read from anywhere other than the per-repo path.
- Plans pushed to Linear render with full markdown formatting — the plan's markdown body goes through unchanged.
- Sibling skill `plan-jira` does the same for Jira; both share the bundled CLI and the `~/plans/<repo>/` tree.
