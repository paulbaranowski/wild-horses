# boundaries.json

`pr_churn_cli.py boundaries` writes this file into the run directory. It is a JSON list with one entry per component that the PR's repo talks to. The `Boundary` type in `scripts/churn_boundaries.py` is the source of this shape. This page describes it for the skill and the lens agents.

| Field        | Value                                                                                                                                                                   |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`       | The component. A repo uses `owner/repo`, and a library uses its package name. A service uses a short name, and a schema uses its path.                                  |
| `kind`       | `repo`, `service`, `library`, or `schema`.                                                                                                                              |
| `direction`  | `provider` when the PR's code calls the component. `consumer` when the component calls the PR's code. `unknown` when the agent docs do not say.                         |
| `local_path` | A readable local copy, or `null`. For a repo, it is a checkout whose origin matches, or has no origin.                                                                  |
| `gh_slug`    | `owner/repo` on GitHub, or `null`.                                                                                                                                      |
| `doc_url`    | The component's documentation, or `null`. Known external APIs and all libraries have one.                                                                               |
| `describes`  | For a schema: the name of the service its server URL names. `null` for every other entry.                                                                               |
| `sources`    | Where discovery found the component, as `file:line`. A generated client directory appears as its path alone. Cite these when a claim depends on the component existing. |

Only repos get their direction from text: the words in the agent docs around the link. Libraries, services, and schemas are always `provider`. Treat a repo's direction as a guess, and read the component before you rely on it.
