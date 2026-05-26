# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's Linear workspace.

| Canonical role    | Linear label             | Meaning                                  |
| ----------------- | ------------------------ | ---------------------------------------- |
| `needs-triage`    | `triage:needs-triage`    | Maintainer needs to evaluate this issue  |
| `needs-info`      | `triage:needs-info`      | Waiting on reporter for more information |
| `ready-for-agent` | `triage:ready-for-agent` | Fully specified, ready for an AFK agent  |
| `ready-for-human` | `triage:ready-for-human` | Requires human implementation            |
| `wontfix`         | `triage:wontfix`         | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding Linear label string from this table.

## First-time setup

These labels do not yet exist in the HRD team. Create them once with:

```bash
linear label create --team HRD --name "triage:needs-triage"    --color "#9B51E0"
linear label create --team HRD --name "triage:needs-info"      --color "#F2C94C"
linear label create --team HRD --name "triage:ready-for-agent" --color "#4EA7FC"
linear label create --team HRD --name "triage:ready-for-human" --color "#27AE60"
linear label create --team HRD --name "triage:wontfix"         --color "#828282"
```

Or create them in the Linear UI — same effect. Verify with `linear label list --team HRD`.

## Why the `triage:` prefix

This workspace already uses prefixed labels for agent metadata (`agent-claude`, `agent-task`). The `triage:` prefix keeps the five triage labels grouped together in Linear's autocomplete and visually distinct from feature/area labels (`Bug`, `Feature`, `Backend`, etc.).
