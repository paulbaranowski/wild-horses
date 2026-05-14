# Codepaths schema

Canonical schema for the two JSON files this plugin reads/writes. Both skills (`codepath-mapper`, `codepath-visualizer`) link to this doc; field definitions never duplicated elsewhere.

## architecture.json

```jsonc
{
  "$schemaVersion": 1,
  "app": { "name": "string", "subtitle": "string" },
  "categories": [
    { "id": "kebab-id", "label": "string", "color": "#hex", "column": 0 },
  ],
  "components": [
    {
      "id": "kebab-id",
      "label": "string",
      "category": "category-id",
      "files": ["glob/**/*.ts"],
      "description": "string (optional)",
    },
  ],
  "edges": [
    { "from": "component-id", "to": "component-id", "label": "string" },
  ],
}
```

**Invariants:**

- All `components[].category` exist in `categories[].id`.
- All `categories[].column` are unique non-negative integers.
- All `edges[].from`/`edges[].to` exist in `components[].id`.
- `components[].id`, `categories[].id` unique within their array, match `^[a-z0-9-]+$`.

## codepaths.json

```jsonc
{
  "$schemaVersion": 1,
  "codepaths": [
    {
      "id": "kebab-id",
      "name": "string",
      "description": "string",
      "steps": [
        {
          "from": "component-id",
          "to": "component-id",
          "annotation": "string",
          "payload": "string (optional)",
          "ref": "path/file.ts:42 (optional)",
        },
      ],
    },
  ],
}
```

**Invariants:**

- All `steps[].from`/`steps[].to` exist in `architecture.json`'s `components[].id`.
- `codepaths[].id` unique within array, matches `^[a-z0-9-]+$`.
- `(steps[i].from, steps[i].to)` _should_ exist in `architecture.json`'s `edges` (warning, not error).

## Exit codes (every CLI subcommand)

- `0` success
- `1` IO error
- `2` argparse error
- `10` id not found
- `11` duplicate id / invalid state
- `12` schema validation
- `13` JSON parse error
- `15` cross-ref broken
- `16` select aborted (user closed browser without picking)
