# codepath-visualizer

Map and visualize codepaths in any codebase as an interactive architecture diagram.

Install:

```text
/plugin install codepath-visualizer@wild-horses
```

## Skills

| Skill                                                    | Invoke                            | What it does                                                                                                                                                         |
| -------------------------------------------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **[`codepath-mapper`](skills/codepath-mapper/)**         | `/codepath-mapper [scope]`        | Scans a codebase (or a scoped flow/file/directory), classifies components into category lanes, traces named codepaths through them, and renders `architecture.html`. |
| **[`codepath-visualizer`](skills/codepath-visualizer/)** | `/codepath-visualizer [--select]` | Opens the rendered `architecture.html`; `--select` blocks on a user pick and returns the chosen codepath's structured JSON to a calling agent.                       |

## How it works

Both skills go through one bundled CLI, `skills/codepath-mapper/codepaths_cli.py`, which owns `architecture.json` and `codepaths.json`: `set-architecture`/`add-codepath`/`update-codepath`/`remove-codepath` for mutations, `list`/`get`/`status` for reads, and `render` to bake both JSONs into `docs/codepaths/architecture.html`. Neither skill edits those JSON files directly or hand-writes the HTML - every mutation and every render goes through the CLI, which validates the schema on every call.

The schema itself is documented once in [`codepaths-schema.md`](codepaths-schema.md).

For contributor-facing details - the template-vs-generated-artifact split, and why a viewer bug fix has to land in `template.html` rather than the checked-in `docs/codepaths/architecture.html` - see [`CLAUDE.md`](CLAUDE.md).
