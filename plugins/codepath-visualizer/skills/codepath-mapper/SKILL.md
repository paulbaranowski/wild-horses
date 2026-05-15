---
name: codepath-mapper
description: Scan a codebase and produce architecture.json + codepaths.json describing components, edges, and traced codepaths. Renders architecture.html at the end. Use when the user says "map my app", "build the architecture viz", "scan my codepaths", "map the X flow", etc. Pass `--render-only` to skip all mapping work and just re-bake architecture.html from existing JSONs — use when the user says "re-render the visualizer", "rebuild the viewer HTML", "regenerate architecture.html", or after a template/CSS edit.
user-invocable: true
disable-model-invocation: false
argument-hint: "[scope: sentence | file | dir | --render-only]"
---

# codepath-mapper

Sweep a codebase, classify components into a category-laned architecture, trace named codepaths through it, and render `architecture.html` for human inspection.

The schema this skill produces is defined in `${CLAUDE_PLUGIN_ROOT}/codepaths-schema.md`. Re-read that file rather than relying on memory.

**Arguments:** `$ARGUMENTS`

---

## CLI reference — `codepaths_cli.py`

The bundled CLI at `${CLAUDE_PLUGIN_ROOT}/skills/codepath-mapper/codepaths_cli.py` is the canonical interface to `architecture.json` and `codepaths.json`. **Don't invent verbs** — every command must be one of these:

- **`set-architecture --json -`** — replace `architecture.json` body (stdin via quoted heredoc).
- **`add-codepath --json -`** — append one codepath.
- **`update-codepath --id <id> --json -`** — replace one codepath in place.
- **`remove-codepath --id <id>`** — delete one codepath.
- **`list --kind components|codepaths|categories|edges`** — print as JSON array.
- **`get --kind <k> --id <id>`** — print one item.
- **`status`** — counts + last-modified + render-staleness.
- **`render [--output <path>]`** — bake both JSONs into `docs/codepaths/architecture.html`.
- **`select [--output <path>]`** — browser picker (used by `/codepath-visualizer --select`, not by this skill).

**Every mutation goes through this CLI.** **Don't use `Edit`/`Write`/inline `python3 -c '…'`** against `architecture.json` or `codepaths.json` — it bypasses validation and atomic writes. **Don't run `init` or `validate`** — neither exists; auto-init and auto-validate are built into every verb.

**Exit codes:** 0 success · 1 IO error · 2 argparse · 10 id not found · 11 duplicate id · 12 schema validation · 13 JSON parse · 15 cross-ref broken · 16 select aborted.

---

## Phase 1 — Parse `$ARGUMENTS`

Extract the optional scope:

- Empty → full repo sweep.
- A sentence (e.g. `"invite new user flow"`) → codepath-name hint.
- A file path → trace codepaths that touch this file (inward + outward).
- A directory path → entry points rooted in this dir.
- The flag `--remap-architecture` → rebuild architecture from scratch even if it exists.
- The flag `--render-only` → skip Phases 2 and 3 entirely; jump straight to Phase 4. Use this when only the HTML template, CSS, or JSON inputs changed and no re-tracing is needed. Don't combine with a scope or `--remap-architecture` — those imply mapping work.

If the input is ambiguous (could be a sentence or a file path), prefer the file-path interpretation iff the path exists.

**`--render-only` short-circuit:** if this flag is present, do not run any mapping work. Run only Phase 4 (one CLI call), then print a one-line confirmation in place of Phase 5: `Re-rendered: docs/codepaths/architecture.html`. Do not touch `architecture.json` or `codepaths.json`.

---

## Phase 2 — Map architecture (run only if `architecture.json` is missing/empty or `--remap-architecture` was passed)

Check with `codepaths_cli.py status`. If `components: 0`, this is a fresh map. Otherwise skip to Phase 3.

1. **Identify packages/modules** — read `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, top-level directory structure. The repo's `CLAUDE.md` (if present) usually names the major pieces.
2. **Identify components** — read READMEs, route files, CLI entrypoint files, IPC handler files, scheduled-job declarations. A _component_ is anything that participates in at least one codepath — UI screens, controllers, services, background workers, data stores, external services.
3. **Propose categories** — adapt to the repo. Typical lanes: Actor → UI → API → Data → Background → External. Pick 4-8. Don't blindly use the default seed unless it fits.
4. **Classify components** into categories, assign `column` indices left-to-right by data flow direction.
5. **Identify edges** — for each component, which others does it call (imports, HTTP fetches, queue publishes, DB queries)? Add an edge per direct call relationship.
6. **Write via CLI**:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/codepath-mapper/codepaths_cli.py" set-architecture --json - <<'EOF'
   {
     "$schemaVersion": 1,
     "app": {"name": "…", "subtitle": "…"},
     "categories": [ … ],
     "components": [ … ],
     "edges": [ … ]
   }
   EOF
   ```

7. Show user: `Architecture mapped: X categories, Y components, Z edges.`

---

## Phase 3 — Map codepaths

Sequential in v1. Identify candidates from scope:

- **No scope:** every HTTP route, every CLI command, every IPC handler, every scheduled job, every user-action handler in UI components. **Cap at first 20** in v1.
- **Sentence scope:** find the entry point matching the sentence (greatest-overlap heuristic).
- **File scope:** start at the file; trace inward (callers) and outward (callees).
- **Dir scope:** every entry-point-like symbol within the directory.

For each candidate codepath:

1. **Trace the call path** through architecture components. Record each component-to-component hop as a step.
2. **For each step**, capture:
   - `from`, `to`: component ids (must exist in `architecture.json` — `codepaths_cli.py list --kind components` is your sanity check).
   - `annotation`: short verb-phrase (e.g. "save app config", "send confirmation email").
   - `payload`: optional, the data shape that gets passed (e.g. `{appId, version}`).
   - `ref`: optional file:line for the call site.
3. **In-component work** (e.g. a step that's purely an internal DB transaction): use a self-loop (`from == to`).
4. **Write via CLI**:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/codepath-mapper/codepaths_cli.py" add-codepath --json - <<'EOF'
   {
     "id": "kebab-case-id",
     "name": "Human-readable name",
     "description": "One sentence about what this codepath does end-to-end.",
     "steps": [
       {"from": "…", "to": "…", "annotation": "…", "payload": "…", "ref": "path/file.ts:42"}
     ]
   }
   EOF
   ```

If `add-codepath` fails with exit 15 (cross-ref), one of the step's `from`/`to` ids isn't in the architecture. **Don't paper over it** by editing files directly — either fix the typo, or add the missing component via `set-architecture` (the architecture may have been incomplete).

---

## Phase 4 — Render

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/codepath-mapper/codepaths_cli.py" render
```

`render` calls `load_and_validate` like every other verb, so if it succeeds the file is well-formed.

---

## Phase 5 — Summarize

Print to the user:

```text
Mapping complete.
  Architecture: {X} components, {Y} edges, {Z} categories
  Codepaths:    {N}
  HTML:         docs/codepaths/architecture.html

Run /codepath-visualizer to open it.
```

---

## Strictly forbidden

- **Don't use `Edit` or `Write`** against `architecture.json` or `codepaths.json`. Every mutation goes through `codepaths_cli.py`.
- **Don't invent CLI verbs** beyond the list above. `init` and `validate` are intentionally absent — auto-init and auto-validate are built into every verb.
- **Don't write the HTML directly.** `render` owns the bake step.
- **Don't run the parallel-agent dispatch pattern in v1.** Codepaths map sequentially; one CLI call per `add-codepath`.
