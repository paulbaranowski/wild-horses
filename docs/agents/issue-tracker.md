# Issue tracker: Linear

Issues for this repo live in Linear. Default workspace: **HRD** (Herds). Default project: **Wild-Horses**.

Operations use the [`linear`](https://gitlab.com/gitlab-org/cli) CLI (installed at `/opt/homebrew/bin/linear`). For raw GraphQL, use `linear api '<query>'`.

## Conventions

- **Issue identifiers** are team-prefixed (e.g. `HRD-123`). Always include the prefix when referencing an issue.
- **Create an issue**:

  ```bash
  linear issue create \
    --team HRD \
    --project "Wild-Horses" \
    --title "..." \
    --description-file <(cat <<'EOF'
  ...multi-line markdown body...
  EOF
  ) \
    --label triage:needs-triage \
    --no-interactive
  ```

  Use `<(cat <<'EOF' ... EOF)` (process substitution + quoted heredoc) so the body ships verbatim in one shell call — no temp file, no `$VAR` expansion. Repeat `--label` to apply multiple labels.

- **Read an issue**: `linear issue view HRD-123`. For comments: `linear issue comment list HRD-123`.
- **List issues**: `linear issue list --team HRD --project "Wild-Horses" --label triage:needs-triage --json` (filter further with `jq`).
- **Comment**: `linear issue comment add HRD-123 --body "..."`
- **Apply / change labels and state**: `linear issue update HRD-123 --label triage:ready-for-agent --state "In Progress"`. Check `linear issue update --help` for current behavior — label removal isn't a documented flag; use `linear api` (GraphQL) if you need to remove a single label without replacing all of them.
- **Close**: `linear issue update HRD-123 --state Done` (or `Canceled` / `Duplicate`).

## When a skill says "publish to the issue tracker"

Create a Linear issue in team `HRD`, project `Wild-Horses`, with the `triage:needs-triage` label applied. Use `--description-file <(cat <<'EOF' ... EOF)` for the body.

## When a skill says "fetch the relevant ticket"

Run `linear issue view <ID>` (e.g. `HRD-123`) and `linear issue comment list <ID>`. The user normally passes the issue ID directly.
