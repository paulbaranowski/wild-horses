# plan-save

Save one or more files from the current conversation to `~/plans/<repo>/<YYYY-MM-DD>-<topic>.<ext>`.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo.

## Invoke

This skill is model-invoked by description — no slash command. Trigger phrases include:

```text
"save this plan"
"save the plan"
"save what I just sent"
"save the json file"                 # → .json extension
"save this as a yaml file"           # → .yaml extension
"save the task-list-builder output"  # → paired .json + .md
"capture these planning notes"
"save this as a herds plan"          # routes to ~/plans/herds/
"save the plan to general"           # routes to ~/plans/general/
"put it in scratch"                  # routes to ~/plans/scratch/
```

Pairs with [`plan-do`](../plan-do/) (which reads what `plan-save` wrote) and [`plan-done`](../plan-done/) (which archives it once finished).

## What it does

1. **Identifies the file(s).** Scans recent messages for a user-pasted file, paired output (e.g. task-list-builder's `.json` + `.md`), the latest `ExitPlanMode` plan, a recent "Design"/"Plan"/"Approach" section, or a substantial markdown outline. Asks if multiple plausible candidates exist.
2. **Extracts the topic.** Uses the first H1/H2 heading as the `--topic` (the CLI slugifies). For non-markdown content (JSON/YAML), uses a phrase from the user's invocation or the paired markdown's H1.
3. **Chooses the extension.** Honors explicit user phrasing ("save the json file" → `--extension json`), otherwise sniffs the content (starts with `{` or `[` → `.json`, etc.), and defaults to `.md`.
4. **Classifies the Kind** (`.md` saves). Infers the document type — `idea` / `prd` / `reqs` / `design` / `spec` / `exec-plan` (see [`../../plan-kinds.md`](../../plan-kinds.md)) — and passes it as `--kind`, surfacing it in the confirmation for one-step correction. This is the field `plan-do` later reads to route the plan, so it's recorded once here with full conversation context rather than re-inferred on every pickup.
5. **Saves via CLI.** Two shapes, chosen by where the content lives:
   - **Heredoc** — `save --topic "<heading>" --extension <ext>` with the body on stdin via a quoted heredoc, for content that lives only in conversation. The CLI constructs `<date>-<slug>.<ext>` for the target name (or `<date>-<slug>--<kind>.<ext>` when `--kind` is given on a `.md` save).
   - **`--from-path`** — for files already on disk (e.g., task-list-builder's `<date>-<runid>-<short>.<slug>.{json,md}`). The target keeps the source's basename verbatim and the source is unlinked after a successful write (atomic same-FS rename). `--topic`/`--extension`/`--date` are rejected.

   For paired output, calls the CLI once per file. The disk shape keeps pairs together automatically because both sources share a base name.
   - **`--root`** (only relevant when more than one plan root is configured, see `pk root list`) names the destination root explicitly; without it, save routes to the repo's existing root or the default root.

6. **Handles collisions.** On exit 2 (file already exists), asks: overwrite / suffix `-2` / pick a new name. Re-invokes with `--on-collision <choice>`. Keeps paired files in sync.
7. **Confirms.** Returns the absolute path(s) the CLI wrote.

The CLI owns slugify, dating, `mkdir -p`, atomic write, and collision detection. The skill owns choosing _what_ to save, _which extension_, and _how_ to handle conflicts.

## Content discipline

The plan body sent on stdin is **exactly** what was in the conversation:

- No "saved by Claude" header.
- No timestamp inside the file.
- No preamble, summary, or footer.
- No commentary the user didn't write.

The CLI writes stdin verbatim — it only appends a trailing newline if missing.

## Repo derivation

`<repo>` auto-derives from `git remote get-url origin` (with a `basename $PWD` fallback). Override with a natural phrase in the invocation. See [`../../repo-derivation.md`](../../repo-derivation.md) for the algorithm and the override-normalization rules (lowercase + whitespace-to-hyphen for overrides; verbatim for auto-derived names).

## Install

The skill ships with the `plan-keeper` plugin:

```text
/plugin install plan-keeper@wild-horses
```
