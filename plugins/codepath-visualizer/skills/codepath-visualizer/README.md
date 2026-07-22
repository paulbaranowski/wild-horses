# codepath-visualizer

Open `docs/codepaths/architecture.html` in the browser. With `--select`, blocks on a user pick and returns the chosen codepath JSON to the calling agent.

The full instructions Claude follows when this skill runs are in [`SKILL.md`](./SKILL.md). This README is a pointer for people browsing the repo. Run this after [`codepath-mapper`](../codepath-mapper/) has produced the HTML - this skill never renders, it only opens what the mapper already built.

## Invoke

```text
/codepath-visualizer            # open the rendered diagram
/codepath-visualizer --select   # block on a user pick, return structured context to the calling agent
```

## What it does

1. **Precondition gate.** Checks `codepaths_cli.py status`: missing HTML tells the user to run `/codepath-mapper` first; a stale render (JSONs newer than the HTML) warns and recommends re-running the mapper.
2. **Viewer mode** (no args): opens `docs/codepaths/architecture.html` with the platform's default opener and prints a one-line summary of app name, codepath count, and component count.
3. **Select mode** (`--select`): opens the HTML with a "Send to agent" button on each codepath card, blocks until the user picks one, then returns the CLI's `{codepath, components}` JSON to the calling agent verbatim - never paraphrased. If the user closes the window without picking, reports that instead.

## Install

The skill ships with the `codepath-visualizer` plugin:

```text
/plugin install codepath-visualizer@wild-horses
```
