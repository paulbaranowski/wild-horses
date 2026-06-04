---
name: plan-save
description: Use when the user asks to save a plan, save the plan, persist the plan, capture planning notes for future reference, store a plan for later, or save a file (any extension — markdown, JSON, YAML, etc.) into the per-repo plans tree. Also handles paired outputs such as task-list-builder's `.json` + `.md` pair.
---

# plan-save

Save one or more files from the current conversation to `~/plans/<repo>/<YYYY-MM-DD>-<topic>.<ext>`. The bundled `plan_keeper_cli.py` handles the actual I/O (slugify, date, `mkdir -p`, atomic write, collision detection). This skill's job is to identify the file(s), extract a topic, pick the right extension, and route to the CLI — calling it once per file when there is more than one.

## Quick reference

- **Target:** `~/plans/<repo>/<YYYY-MM-DD>-<topic>.<ext>` (a classified `.md` save with `--kind` becomes `<YYYY-MM-DD>-<topic>--<kind>.md` — see **Kind in filename** below).
- **`<repo>`:** auto-derived from `git remote`/`pwd`, or override from the user's invocation — see [../../repo-derivation.md](../../repo-derivation.md).
- **`<topic>`:** first H1/H2 of the plan, used as the CLI's `--topic` (CLI slugifies). For non-markdown content with no heading, use a short phrase from the user's invocation.
- **`<ext>`:** defaults to `md`. Set via `--extension` when the content is not markdown — see [Choosing the extension](#choosing-the-extension).
- **Date:** today, in the user's local timezone (CLI handles).
- **Collision:** ask the user; never overwrite silently.
- **Content:** file body verbatim — no preamble, footer, or commentary. (For `.md` saves — heredoc **and** `--from-path` moves — the CLI injects an `Agent: claude\nStatus: backlog\nCreated: <iso>\n` frontmatter block if one isn't present, and fills missing Agent/Status/Created fields if a partial block is; `Kind` is filled only when `--kind` is passed. Non-`.md` saves stay byte-exact. On a `--from-path` `.md` move, `Created` comes from the source file's birthtime, not the move time, since the plan pre-existed.)
- **`--agent`:** override the default `claude` (e.g., `--agent codex`); affects `.md` saves on both the heredoc and `--from-path` move paths.
- **`--kind`:** the document type — one of `idea` / `prd` / `design` / `spec` / `exec-plan` (see [../../plan-kinds.md](../../plan-kinds.md)). Infer it from the content and pass it on `.md` heredoc saves; `plan-do` later reads it to route the plan. Fill-if-absent, `.md`-only. See [Classifying the Kind](#classifying-the-kind).
- **Kind in filename:** a markdown save with `--kind` lands at `<date>-<slug>--<kind>.md` (double-hyphen separator). Without `--kind` (or for non-`.md` saves) the name stays `<date>-<slug>.<ext>`. The `--` is the sole, unambiguous Kind boundary — `slugify` can never emit `--` inside the slug, so the stages of one project (which share a slug) group cleanly in `plan-list`/`plan-do`.
- **Multiple files:** when the user has produced a paired/grouped artifact (most commonly task-list-builder's `.json` + `.md`), save each file with one `save` invocation, sharing `--topic` (and `--date` if you set it) so the resulting filenames pair on the base name.

## Procedure

Follow these steps in order. Do not skip steps.

### 1. Identify the file(s) to save

Scan recent conversation messages — from both the user and the assistant — for the content to save. Prefer, in order:

- Content the user just pasted and pointed at in the save invocation ("save this", "save what I just sent", "save the json file", "save the plan I pasted")
- A paired artifact the assistant just produced where both files belong together — most commonly **task-list-builder output**, which writes a `.json` (canonical task list) plus a `.md` (human-readable report) with the same base name. If you see a recent JSON object with `tasks`, `verifySteps`, and `plan` fields next to a markdown report whose H1 matches the JSON's intent, treat them as one paired save. See [Paired-output handling](#paired-output-handling).
- The most recent `ExitPlanMode` plan
- The most recent "Design", "Plan", or "Approach" section the assistant produced
- A substantial numbered or bulleted markdown outline — whoever wrote it

If you cannot confidently identify a single file or paired group, stop and ask the user which one to save. Do not guess between candidates.

### 2. Extract the topic, choose the extension, classify the Kind, and check for a repo override

**Topic:** Take the first H1 or H2 heading in the file's text. Pass the raw heading (with punctuation, capitalization, whitespace) to `--topic` — the CLI slugifies. If the file has no heading (typical for JSON/YAML), use the first 4–6 meaningful words of the user's invocation, or — for paired output — the H1 of the paired markdown report.

**Extension:** see [Choosing the extension](#choosing-the-extension). Pass `--extension <ext>` (no leading dot needed). Omit the flag only when you're confident the content is markdown.

**Kind:** for `.md` saves, classify the document type and pass it as `--kind`. See [Classifying the Kind](#classifying-the-kind).

**Repo override:** Check the user's invocation for one of these phrases. If present, extract `<name>` and pass it as `--override`. Otherwise omit `--override` and the CLI auto-derives per [../../repo-derivation.md](../../repo-derivation.md).

- "save the plan to `<name>`"
- "save (this|it|the plan) as a `<name>` plan"
- "save to `<name>`"
- "put it in `<name>`"
- "in the `<name>` folder/bucket"

### 3. Save via the CLI

Two delivery shapes — pick the one that fits the source:

**3a. Content lives in the conversation (heredoc).** Stream the body on stdin via a quoted heredoc:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" save \
  --topic "<heading text>" \
  --kind <idea|prd|design|spec|exec-plan> \
  <<'EOF'
<file body verbatim — no preamble, no footer>
EOF
```

(Add `--kind` only for `.md` saves — it's rejected for `--extension json` and other non-md extensions. For a paired `.json` + `.md`, put `--kind` on the `.md` half only.)

**3b. Content already exists on disk (move).** Skip the heredoc and let the CLI relocate the file directly. The target always keeps the source's basename, and the source is unlinked after a successful write — `--from-path` is verbatim + always-move, and `--topic`/`--extension`/`--date` are rejected:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" save \
  --from-path "<absolute or repo-relative path to existing file>"
```

Use this whenever the source filename is already a good final name — most notably **task-list-builder** output at `docs/exec-plans/active/<date>-<runid>-<short>.<slug>.{json,md}`. If the source needs renaming, rename it on disk first, then pass that path; the CLI deliberately does not rename, because reconstructing date/topic/extension from a well-named source is what produced the original `-json.md` bug this shape exists to prevent.

`--from-path` reads the body from the named file instead of stdin (no shell-quoting hazards for JSON bodies). A non-`.md` source is relocated byte-for-byte via an atomic same-FS rename — no trailing-newline normalization, no rewrite (this is the verbatim guarantee the paired `.json` sibling relies on). A `.md` source is treated like a heredoc `.md` save: the CLI fills any missing `Agent`/`Status`/`Created` frontmatter (fill-if-absent — a block already carrying those keeps its own values, with `Created` sourced from the source file's birthtime), `write_atomic`s the stamped result to the target, then unlinks the source — a write-then-delete move, not an atomic rename. Either way the source is **only** deleted once the target write succeeded — a malformed-frontmatter `.md` or a collision (exit 2) leaves the source untouched, so retrying is safe.

Common to both shapes (3a and 3b):

- Add `--override <name>` if step 2 found one.
- For a **paired save**, run this step once per file. See [Paired-output handling](#paired-output-handling).

**On exit 0:** the CLI prints the written absolute path on stdout. Use that path verbatim in step 5.

**On exit 2 (collision):** stderr contains:

```text
ERROR: collision
existing: /Users/<you>/plans/<repo>/<date>-<slug>.<ext>
suggestion: /Users/<you>/plans/<repo>/<date>-<slug>-2.<ext>
```

Go to step 4.

### 4. Handle collision (only if step 3 exited 2)

Ask the user:

> File `<path-from-stderr>` already exists. Overwrite, save as `<slug>-2`, or pick a new topic name?

Wait for their answer, then re-invoke the CLI with the appropriate flag:

- **"save as -2" / "use the suggestion" / "suffix":** add `--on-collision suffix` and re-run step 3. (The CLI finds the lowest unused `-N`.)
- **"overwrite":** add `--on-collision overwrite` and re-run step 3.
- **"new name" / a different topic:** rerun step 3 with the new `--topic`.

For paired saves where one file collides and the other doesn't, apply the chosen resolution to **both** files so they stay paired (e.g., if the `.json` got `-2`, re-save the `.md` with `--on-collision suffix` even if the original `.md` slot was free — preferable to letting the pair drift apart).

### 5. Confirm

Tell the user the absolute path(s) the CLI returned, and — for `.md` saves — the `Kind` you assigned, so they can correct it in one reply:

> Saved to `/Users/<you>/plans/<repo>/<YYYY-MM-DD>-<topic>.md` as **Kind: prd**. (Say so if it's really an idea / design / spec / exec-plan and I'll fix it.)

If the user corrects the Kind, apply it without re-saving the body:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" file-meta set \
  --file "<path the CLI returned>" --kind <corrected value>
```

For paired saves, list both paths on consecutive lines.

## Choosing the extension

The CLI's `--extension` flag accepts `^[a-z0-9]+$` (with optional leading `.`). Decide the value as follows:

1. **Explicit user phrasing wins.** Honor the user's words verbatim if they name an extension. Examples:
   - "save the json file" → `--extension json`
   - "save this as a yaml file" → `--extension yaml`
   - "save it with .toml extension" → `--extension toml`
2. **Otherwise, sniff the content.** Look at the first non-whitespace character(s) of the file body:
   - First non-whitespace char is `{` or `[` → `--extension json`
   - Document starts with `---\n` followed by `key: value` lines (and is _not_ a markdown file whose frontmatter is bracketed by a closing `---` followed by a real markdown body) → `--extension yaml`
   - First non-whitespace line looks like `<?xml` or `<!DOCTYPE` → `--extension xml` or `html`
   - Default: `md`
3. **When in doubt, ask.** If sniffing is ambiguous (e.g. a markdown file that opens with YAML frontmatter), ask the user which extension to use. Don't silently guess against the user's intent.

## Classifying the Kind

For `.md` saves, infer the **document type** from the content and conversation and pass it as `--kind`. The closed value set and its full definitions live in [../../plan-kinds.md](../../plan-kinds.md); the short form:

| `--kind`    | Use when the file is…                                                           |
| ----------- | ------------------------------------------------------------------------------- |
| `idea`      | an exploratory thought / sketch — no committed requirements or design           |
| `prd`       | product requirements: the problem, the why, user-facing reqs, scope / non-goals |
| `design`    | an architecture / technical design: components, data model, trade-offs          |
| `spec`      | an implementation spec: the concrete, detailed how, ready to plan against       |
| `exec-plan` | an executable plan / task list: phased steps or independent tasks, ready to run |

This is the same axis `plan-do` uses to route a plan, recorded once at save time so `plan-do` doesn't have to re-infer it. Classify, pass `--kind`, and **surface the value in your step-5 confirmation** so the user can correct it in one reply (infer-and-confirm).

- **If the inference is genuinely ambiguous** between two kinds (e.g. a doc that's half-PRD, half-design), ask the user which before saving rather than guessing.
- **Don't pass `--kind` for non-`.md` saves** — the CLI rejects it (frontmatter only lives in markdown). For a paired `.json` + `.md`, the `.md` carries the Kind.
- **`--kind` is fill-if-absent:** if the body already declares `Kind:` in its own frontmatter, that value is kept and `--kind` is ignored.

## Frontmatter injection (markdown saves only)

A markdown save with `--kind`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" save \
  --topic "My Plan" \
  --kind spec \
  <<'EOF'
# My Plan
Body.
EOF
```

produces a file that starts with:

```markdown
---
Agent: claude
Status: backlog
Kind: spec
Created: 2026-06-02T14:30:00Z
---

# My Plan

Body.
```

The defaults are a floor, not an override: if the user pipes in a body that already declares `Agent:`, `Status:`, `Created:`, or `Kind:` in its own frontmatter, those values are kept untouched. `Status: backlog` means the plan is fetched but not dispatched (confirm via `crew status <id>`) — promote via `/plan-update` (or `file-meta set --status todo`) when the plan is ready for groundcrew to pick up. `Created` is an ISO-8601 UTC save-time stamp that gives `plan-do`'s newest-first listing precise _intra-day_ ordering (filenames carry only a `YYYY-MM-DD` date, so without it same-day plans fell back to slug-alphabetical). It's persisted in frontmatter so status mutations — which rewrite the file and reset its OS timestamps — never disturb the order. `Kind` (omitted entirely if you don't pass `--kind`) records the document type — see [Classifying the Kind](#classifying-the-kind) and [../../plan-kinds.md](../../plan-kinds.md).

The injection happens for every `.md` save — both heredoc and `--from-path` moves. JSON and other extensions are written byte-for-byte, including via `--from-path` (that byte-verbatim guarantee now applies only to non-`.md` artifacts, the paired `.json` it exists to protect). On a `--from-path` `.md` move the only difference from a heredoc save is `Created`'s source: it comes from the source file's birthtime (best-effort, falling back to mtime), because the moved-in plan pre-existed the move rather than being authored now.

## Paired-output handling

`task-list-builder` produces a paired `.json` (the canonical task list the harness loop runner consumes) and `.md` (a human-readable report). Both files are written to `docs/exec-plans/active/<date>-<runid>-<short>.<slug>.{json,md}` (default `<slug>` is `task-list-builder`; `/harness:reasoning-gaps` uses `reasoning-gaps`, `/harness:feedback-blockers` uses `feedback-blockers`). The filename already encodes date, run-id, and identity — there is no need to rename it.

When the user invokes plan-save after a task-list-builder run, save **both** files in one go:

1. Locate the paired files on disk. They are usually the most recent matching pair under `docs/exec-plans/active/`.
2. Call the CLI once per file using the disk shape from step 3b (no `--topic`, no `--extension`, no `--date` — the source's basename is the target's basename):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" save \
     --from-path docs/exec-plans/active/<date>-<runid>-<short>.<slug>.json

   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" save \
     --from-path docs/exec-plans/active/<date>-<runid>-<short>.<slug>.md
   ```

3. Both files land at `~/plans/<repo>/<date>-<runid>-<short>.<slug>.{json,md}` — the pair stays together because they share the same source basename — and the originals under `docs/exec-plans/active/` are removed.

If the files exist **only in conversation** (no disk write yet — uncommon for task-list-builder), use the heredoc shape from step 3a instead, passing a shared `--topic` and `--date` to both invocations so the resulting filenames pair on the base name:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" save \
  --topic "<h1>" --date <date> --extension json <<'EOF'
{ ... }
EOF

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py" save \
  --topic "<h1>" --date <date> --extension md <<'EOF'
# ...
EOF
```

Handle collisions per step 4 above, keeping the pair in sync. Because `--from-path` only deletes the source on a successful write, a collision is safe to retry.

You may also encounter other paired-file groupings the user describes informally (e.g., "save these three configs together"). Treat each file as its own `save` invocation — verbatim mode for files already on disk with good names, heredoc with shared topic+date otherwise.

## Content discipline

The plan body sent on stdin must be **exactly** the plan as it appeared in the conversation:

- **Don't add a "Saved by Claude" header.**
- **Don't add a timestamp inside the file.**
- **Don't add a summary, preamble, or footer.**
- **Don't include commentary that wasn't in the original plan.**

The CLI writes stdin verbatim (it only appends a trailing newline if missing).

## Common mistakes

- **Pre-slugifying the topic before passing to `--topic`.** The CLI slugifies. Pass the raw heading text — e.g., `--topic "Multi-Event parent_title Design"`, not `--topic "multi-event-parent_title-design"`. (Both work, but raw is the canonical input.)
- **Stuffing the format into the slug instead of the extension.** Writing `--topic "task list json"` (no `--extension`) lands the file at `…-task-list-json.md`. The format goes in `--extension json`, not the topic.
- **Forgetting `--override` when the user named a destination.** "save this as a general plan" → `--override general`. Without it, the CLI auto-derives from the current repo and the plan lands in the wrong folder.
- **Reading the CLI's stderr as a fatal error.** Exit 2 is a structured collision signal, not a failure to act on. Parse it and ask the user (step 4) — do not abort.
- **Guessing between multiple plan candidates.** Step 1 requires asking the user when more than one plausible plan exists. Don't pick the most recent one to seem helpful.
- **Saving a task-list-builder JSON without its paired MD.** When you see the paired output in conversation, save both. Saving just the JSON loses the human-readable report that explains what the task list is for.
- **Heredoc-piping a file that already exists on disk.** If the source is already at `docs/exec-plans/active/foo.json`, use `--from-path docs/exec-plans/active/foo.json` rather than `cat` + heredoc. The disk-based shape is leaner, avoids quoting hazards in JSON bodies, and atomically relocates the file rather than re-piping it through the shell.
- **Passing `--topic`/`--extension`/`--date` alongside `--from-path`.** `--from-path` is the verbatim shape: the source basename is the target basename, end of story. The CLI rejects any of those flags with exit 2 rather than guessing what you meant. If the source is poorly named, rename it on disk first, then pass that path.
- **Letting the pair drift on a collision.** If the `.json` half collides and the user picks `--on-collision suffix`, the `.md` half must use the same resolution (rerun with `--on-collision suffix`) so both filenames stay matched.

## Notes

- The `~/plans/` tree is local to the user's machine. This skill never commits anything to any repo.
- The `Kind` this skill assigns is what `plan-do` reads to route the plan (idea → brainstorming, prd/design/spec → writing-plans, exec-plan → execution menu). Classifying it here, with full conversation context, saves `plan-do` from re-inferring it later — see [../../plan-kinds.md](../../plan-kinds.md).
- `--from-path` moves never carry a `Kind` (the CLI rejects `--kind` on `--from-path`): a non-`.md` source stays byte-exact, and a moved-in `.md` gets the managed `Agent`/`Status`/`Created` block but no `Kind`. If you want one on the relocated `.md`, add it afterward with `file-meta set --kind exec-plan`.
- A `.md` dropped into `~/plans/<repo>/` by a manual `mv`/`cp` (the CLI never ran) carries no `Created`, so it falls back to day-granularity filename ordering. To get an exact stamp (and intra-day ordering), bring the file in with `save --from-path` instead — it stamps `Created` from the source's birthtime. `list`/`plan-do` deliberately do **not** stamp on read — those stay read-only queries; stamping lives only in the write path (`save`).
- Sibling skills in the `plan-` family (`plan-do`, `plan-done`) share the same CLI and the same `~/plans/<repo>/` tree.
