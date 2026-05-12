# task-list-builder

**Role:** create a paired `.json` + `.md` task list matching the schema at [`task-list-schema.md`](../task-list-schema.md).

The JSON is the canonical artifact — schema-validated, atomically written, and consumed by [task-list-runner](task-list-runner.md). The markdown is a human-readable summary paired with it.

## Usage

```text
/task-list-builder                                         # build from recent conversation context
/task-list-builder docs/exec-plans/active/foo.md           # build from a markdown report
/task-list-builder "extract config to TypedDict in src/x"  # build from free-form text
/task-list-builder docs/exec-plans/active/foo.json         # in-place rewrite of an existing JSON
```

Optional flags:

- `--slug <name>` — override the default `task-list-builder` filename suffix. Output paths become `…<short-description>.<name>.{json,md}`. Validates `<name>` matches `[a-z][a-z0-9-]*`.
- `--md-body-from-context` — use the most recent rendered analysis report from the current conversation as the MD body (instead of synthesizing one).

## Typical caller patterns

- **Standalone**: no flags. Default slug, generated MD body.
- **From `/harness:reasoning-gaps`**: `--slug reasoning-gaps --md-body-from-context`. The merged report is in conversation; the slug preserves provenance.
- **From `/harness:feedback-blockers`**: `--slug feedback-blockers --md-body-from-context`. Same shape.

## Output

Two paired files in `docs/exec-plans/active/`:

- `<short-description>.<slug>.json` — the schema-validated task list.
- `<short-description>.<slug>.md` — the human-readable summary. Its YAML frontmatter contains a `task_file` field pointing to the JSON.

## Rewrite mode

If a `.json` path is passed and the file exists (or the user phrasing includes "rewrite", "update", "regenerate"), the builder rewrites the file in-place. Useful for restructuring a plan that didn't survive contact with reality without losing the artifact's identity.

## Pairs with

- [task-list-runner](task-list-runner.md) — drives the JSON to completion.
- [task-list-viewer](task-list-viewer.md) — reads the JSON without mutating it.
