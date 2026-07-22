# codepath-mapper

Scan a codebase and produce `architecture.json` + `codepaths.json` describing components, edges, and traced codepaths, then render `architecture.html`.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo. See also the plugin-level [`CLAUDE.md`](../../CLAUDE.md) for the template/generated-artifact split, which matters when fixing a viewer bug.

## Invoke

```text
/codepath-mapper                      # full repo sweep
/codepath-mapper "invite new user"    # scope to a named flow
/codepath-mapper path/to/file.ts      # trace codepaths touching this file
/codepath-mapper --render-only        # re-bake HTML from existing JSONs, no re-tracing
```

Also model-invoked - trigger phrases include "map my app", "build the architecture viz", "scan my codepaths".

## What it does

1. **Parses the scope** from the argument: empty (full sweep), a sentence (codepath-name hint), a file path (trace inward/outward from it), a directory (entry points within it), or `--render-only` (skip straight to rendering).
2. **Maps the architecture** (only if `architecture.json` is missing/empty, or `--remap-architecture` was passed): identifies packages/components from manifests and route/entrypoint files, proposes 4-8 category lanes adapted to the repo, classifies components into them, and identifies call-relationship edges.
3. **Maps codepaths.** Sequential in v1: for each candidate entry point (capped at 20 with no scope), traces the call path through architecture components, capturing each hop's `from`/`to`/annotation/payload/ref.
4. **Renders** `docs/codepaths/architecture.html` by baking both JSONs into the template.
5. **Summarizes** the component/edge/codepath counts and points to `/codepath-visualizer` to open the result.

Every mutation goes through the bundled `codepaths_cli.py` (`set-architecture`, `add-codepath`, `update-codepath`, `remove-codepath`, `render`) - the skill never edits the JSON files directly, and never hand-writes the HTML.

## Install

The skill ships with the `codepath-visualizer` plugin:

```text
/plugin install codepath-visualizer@wild-horses
```
