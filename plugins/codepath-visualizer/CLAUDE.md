# codepath-visualizer

Two skills:

- **`codepath-mapper`** — scans a codebase, produces `architecture.json` + `codepaths.json`, and renders `architecture.html` via a CLI (`codepaths_cli.py`).
- **`codepath-visualizer`** — opens the rendered HTML in a browser; supports a `--select` mode that blocks until the user picks a codepath.

## The template/artifact split (the easy mistake)

`skills/codepath-mapper/template.html` is the **source** for the viewer code (HTML + CSS + the JS that draws the graph). `codepaths_cli.py render` bakes the JSON inputs into this template and writes `architecture.html`.

`docs/codepaths/architecture.html` in this repo is a **generated artifact** — it's the dogfood map of the wild-horses marketplace itself, produced by running `render` against this repo. It is checked in so the GitHub-hosted viewer works without a build step, but it is not source.

**When fixing a viewer bug (anything in `<script>`, `<style>`, or the page chrome):**

- Edit `skills/codepath-mapper/template.html`. That's the source.
- Re-render the dogfood: `python3 plugins/codepath-visualizer/skills/codepath-mapper/codepaths_cli.py render` (defaults to `--dir docs/codepaths`).
- Don't hand-edit `docs/codepaths/architecture.html`. The next render overwrites it, and a fix that lives only in the generated file silently regresses for every user of the plugin.

**Why this rule exists:** a code-review fix landed in `docs/codepaths/architecture.html` first; the same bug was still in `template.html`, so the fix would have shipped to nobody and would have been wiped on the next render. Both files have the same `drawEdges` body — grep for the symbol you're touching across the plugin tree before editing.

## Verifying staleness

`codepaths_cli.py status` returns `renderStale: true` when the JSON inputs are newer than `architecture.html`. After editing `template.html`, status will _not_ flag staleness (it compares JSON mtimes, not template mtime) — you have to re-render manually. Treat any `template.html` change as implicitly stale.

## Versioning

Per the repo-root CLAUDE.md, every change to plugin content (including `template.html`) requires a version bump in `plugin.json`. Patch for viewer bug fixes; minor for new viewer features.
