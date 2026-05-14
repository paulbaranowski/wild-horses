---
name: codepath-visualizer
description: Open docs/codepaths/architecture.html in the browser. With --select, blocks on a user pick and returns the chosen codepath JSON to the calling agent. Run after /codepath-mapper has produced the HTML.
user-invocable: true
disable-model-invocation: false
argument-hint: "[--select]"
---

# codepath-visualizer

Thin wrapper around `codepaths_cli.py status`, `open`, and `codepaths_cli.py select`. **The mapper owns rendering** — this skill never re-renders.

**Arguments:** `$ARGUMENTS`

---

## Mode 1 — viewer (no args)

1. **Precondition gate.** Run:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/codepath-mapper/codepaths_cli.py" status
   ```

   - Exit 0 + `"htmlExists": true` + `"renderStale": false` → continue.
   - Exit 0 + `"htmlExists": false` → tell user `architecture.html` is missing; instruct: `run /codepath-mapper first`.
   - Exit 0 + `"renderStale": true` → warn user the HTML is older than the JSONs; recommend `rerun /codepath-mapper` to regenerate.
   - Exit non-zero → report the stderr to the user; bail.

2. **Open the HTML.** Use the platform's default opener:
   - macOS: `open docs/codepaths/architecture.html`
   - Linux: `xdg-open docs/codepaths/architecture.html`
   - Windows: `start docs/codepaths/architecture.html`

3. **Print summary.** Echo: `Opened docs/codepaths/architecture.html — {app.name}: {N} codepaths across {Y} components.`

---

## Mode 2 — `--select` (agent picker)

Intended for other agents that need structured context about a codepath the user picks.

1. **Precondition gate** (same as Mode 1).
2. **Run `select`**:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/codepath-mapper/codepaths_cli.py" select
   ```

   This will open the HTML with a "Send to agent →" button on each codepath card, then block on the user's pick. **Don't add `--output`** unless the HTML lives outside `docs/codepaths/`.

3. **Return** the CLI's stdout (a JSON object `{codepath, components}`) to the calling agent. **Don't paraphrase it** — the calling agent expects the structured shape.

4. If exit code is 16, the user closed the window without picking. Report this to the calling agent (no codepath was chosen).

---

## Strictly forbidden

- **Don't re-render the HTML** — `/codepath-mapper` owns that. If the HTML is stale, ask the user to re-run the mapper.
- **Don't invoke `select` without first running `status`** — a broken JSON file at select time is a confusing error for the user (server already running, browser already open).
- **Don't add interactive prompts to the user inside `--select` mode** — the calling agent is waiting on stdout; any prompt would block both sides.
