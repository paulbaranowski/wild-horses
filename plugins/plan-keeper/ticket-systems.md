# Ticket systems

`/plan-push` supports pushing plans as tickets to either **Linear** or **Jira**. Per-repo configuration lives at `~/plans/<repo>/.plankeeper.json`. This doc describes the schema, the field meanings, and the API surface plan-keeper actually uses.

## Config schema

Two top-level keys (`linear`, `jira`) are independently optional. Each section has three zones:

- **Credentials** — the keys to talk to the API.
- **`defaults`** — the IDs that get sent to the API on `push`.
- **`cache`** — the available teams/projects/labels/users, populated by `ticket-system-config refresh`. Used as the picker's data source during setup.

### Linear

```json
{
  "linear": {
    "apiKey": "lin_api_...",
    "defaults": {
      "teamId": "uuid",          "teamName": "Engineering",
      "projectId": "uuid",       "projectName": "Backend Refactor",
      "assigneeId": "uuid",      "assigneeName": "Paul Baranowski",
      "labelIds": ["uuid"],      "labelNames": ["plan"]
    },
    "cache": {
      "refreshedAt": "2026-05-20T12:34:56Z",
      "teams":    [{"id": "...", "name": "..."}],
      "projects": [{"id": "...", "name": "...", "teamIds": ["..."]}],
      "labels":   [{"id": "...", "name": "...", "teamId": "..." or null}],
      "users":    [{"id": "...", "name": "...", "email": "..."}]
    }
  }
}
```

### Jira

```json
{
  "jira": {
    "site": "herds.atlassian.net",
    "email": "you@example.com",
    "apiToken": "ATATT...",
    "defaults": {
      "projectKey": "HERDS",
      "componentIds": ["10001"],
      "componentNames": ["Backend"],
      "assigneeAccountId": "5e8f...",
      "assigneeName": "Paul",
      "issueType": "Task",
      "labels": ["plan"]
    },
    "cache": {
      "refreshedAt": "2026-05-20T12:34:56Z",
      "projects": [{ "key": "HERDS", "id": "...", "name": "..." }],
      "components": [{ "id": "...", "name": "...", "projectKey": "..." }],
      "users": [{ "accountId": "...", "name": "...", "email": "..." }],
      "issueTypes": [{ "id": "...", "name": "...", "projectId": "..." }]
    }
  }
}
```

## File permission

`ticket-system-config save` enforces `chmod 600` on `.plankeeper.json` after every write. If the chmod fails (e.g., on a filesystem that doesn't support POSIX modes), the write still succeeds but a warning is printed to stderr.

## Cache staleness

`cache.refreshedAt` is ISO 8601 UTC. The `plan-push` SKILL.md surfaces "cache is N days old; refresh?" in setup re-runs if the value is older than 14 days. Plain push does not check staleness — it trusts that `defaults` are still valid until the API tells it otherwise.

## API surface plan-keeper uses

| Operation        | Linear (GraphQL)                      | Jira (REST v3)                                              |
| ---------------- | ------------------------------------- | ----------------------------------------------------------- |
| Validate auth    | `viewer { id name email }`            | `GET /myself`                                               |
| Create ticket    | `issueCreate(input)`                  | `POST /issue` (project/summary/description/issuetype/...)   |
| Update ticket    | `issueUpdate(id, input)`              | `PUT /issue/<key>` (summary + description only)             |
| List teams       | `teams(first, after)`                 | n/a                                                         |
| List projects    | `projects(first, after) { teams }`    | `GET /project/search` (paginated)                           |
| List components  | n/a                                   | `GET /project/<key>/components`                             |
| List labels      | `issueLabels(first, after) { team }`  | n/a (labels are free-text strings, no IDs)                  |
| List users       | `users(first, after)`                 | `GET /user/assignable/multiProjectSearch?projectKeys=<key>` |
| List issue types | n/a (Linear has built-in states only) | `GET /issuetype/project?projectId=<id>`                     |

### Auth headers

- **Linear:** `Authorization: <apiKey>` — no `Bearer` prefix.
- **Jira:** `Authorization: Basic <base64(email:apiToken)>`.

### Pagination

- **Linear** uses GraphQL cursor pagination: `first: 100, after: $cursor`, response includes `pageInfo { endCursor hasNextPage }`. Loop until `hasNextPage` is false.
- **Jira** uses REST `startAt`/`maxResults`. Response includes `isLast` and a `values` array. Loop until `isLast` is true.

## Description format

The push subcommand prepends a `Repo: <owner>/<name>` line to every ticket description, derived from the local git remote via `repo --full`.

- **Linear:** the description field is rendered as markdown natively — the plan's markdown body goes through unchanged.
- **Jira:** the description field requires ADF (Atlassian Document Format) JSON. **V1 wraps the entire markdown body in a single ADF paragraph node** — the result is accurate but appears as one continuous block of plain text in Jira. Headings, lists, and code blocks all collapse to inline text with no formatting. Users who want richer formatting can edit the ticket in Jira after creation. V2 (not yet implemented) will convert markdown to structured ADF.

## Description size limit

Linear documents a 65,000-character limit on issue descriptions; Jira's is higher. The push subcommand pre-checks Linear payloads against this limit and aborts (exit 2) with the actual size and the cap. Users who hit it can trim the plan and retry.
