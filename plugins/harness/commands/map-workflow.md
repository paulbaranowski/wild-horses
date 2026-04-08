---
description: Map an application workflow so AI agents can run it, observe outputs, evaluate quality, and iterate. Produces a structured JSON artifact that enables agent self-verification loops. Use when you want an agent to be able to autonomously work on a feature by understanding inputs, run mechanisms, outputs, and success criteria.
argument-hint: "<workflow description>"
---

# Map Workflow

Map an application workflow into a structured artifact that enables AI agents to **run it, observe the results, evaluate quality, and iterate autonomously**. The mapping captures everything an agent needs to close the feedback loop: inputs, run mechanisms, outputs, and quality criteria.

**Workflow to map:** "$ARGUMENTS"

---

## The Schema

The mapping produces a JSON file with this structure:

```json
{
  "version": "1",
  "name": "<kebab-case identifier>",
  "description": "<what this workflow does and why>",

  "inputs": {
    "parameters": [],
    "environment": [],
    "files": [],
    "infrastructure": []
  },

  "run": {
    "cli": {},
    "api": {},
    "ui": {},
    "tests": [],
    "code": {}
  },

  "outputs": {
    "state_changes": [],
    "observability": []
  },

  "quality": {
    "goal": "",
    "trade_offs": [],
    "known_results": ""
  },

  "verification": {
    "command": "",
    "steps": []
  }
}
```

### Field Definitions

**inputs.parameters[]** — Every tunable knob for this workflow:
```json
{
  "name": "param_name",
  "type": "string | int | float | bool | enum",
  "values": ["only", "for", "enums"],
  "required": true,
  "default": "value or null",
  "description": "What this parameter controls",
  "constraints": "Valid ranges, relationships, or rules",
  "affects": [
    {
      "metric": "name of output metric this influences",
      "relationship": "how changing this param moves the metric (natural language, include direction)"
    }
  ],
  "cli_flag": "--flag-name",
  "query_param": "url_param_name",
  "code_param": "function_kwarg_name"
}
```
Include ALL parameter access methods — a parameter may be settable via CLI flag, URL query param, AND function argument. Capture all of them.

**inputs.environment[]** — Environment variables:
```json
{
  "name": "VAR_NAME",
  "default": "value",
  "description": "What it controls"
}
```

**inputs.files[]** — Files the workflow reads or depends on:
```json
{
  "role": "what role this file plays (config, data, template, etc.)",
  "location": "path or description of where to find it",
  "format": "file format (JSON, YAML, image, SQLite, etc.)",
  "description": "How the workflow uses this file"
}
```

**inputs.infrastructure[]** — Servers, databases, network connections that must be running:
```json
{
  "name": "human-readable name",
  "start": "command to start it",
  "verify": "command to check it's running (should exit 0 when ready)",
  "stop": "command to stop it (optional)",
  "required": true,
  "description": "What role it plays in the workflow"
}
```

**run.cli** — Command-line invocation:
```json
{
  "command": "command template with {param} placeholders",
  "example": "fully filled-in example command",
  "working_directory": "relative to project root",
  "notes": "any gotchas about invocation"
}
```

**run.api** — HTTP/REST/GraphQL endpoint invocation:
```json
{
  "method": "POST | GET | PUT | etc.",
  "url": "endpoint URL (use {param} placeholders for path params)",
  "headers": { "Content-Type": "application/json" },
  "body": "request body template with {param} placeholders",
  "example": "curl -X POST http://localhost:8443/api/link -H 'Content-Type: application/json' -d '{\"message\": \"hello\"}'",
  "response": {
    "success_status": 200,
    "body_format": "description of response shape and key fields"
  },
  "notes": "authentication requirements, rate limits, etc."
}
```

**run.ui** — Browser or mobile app invocation:
```json
{
  "url": "URL or screen to navigate to",
  "steps": [
    "human-readable step-by-step interaction (e.g., 'Click Create Link', 'Enter message in text field', 'Select image from dropdown', 'Click Submit')"
  ],
  "automation": "browser automation command if available (e.g., playwright, selenium, xctest)",
  "notes": "any prerequisites (login, specific browser, device)"
}
```

**run.tests[]** — Test files/commands that exercise this workflow:
```json
{
  "command": "test invocation command",
  "exercises": "what aspect of the workflow this test covers",
  "key_assertions": "what the test checks (optional, for important tests)"
}
```

**run.code** — Programmatic entry point:
```json
{
  "entry_point": "module.path.ClassName or module.path.function",
  "call_chain": [
    "step-by-step code showing how to invoke programmatically"
  ],
  "key_functions": [
    "module.path.function_name — one-line description"
  ]
}
```

**outputs.state_changes[]** — What the workflow creates or modifies:
```json
{
  "what": "description of the state change",
  "location": "where the change persists (DB, file, memory, etc.)",
  "key_fields": ["important fields in the changed state"],
  "inspect": "command or endpoint to inspect the change"
}
```

**outputs.observability[]** — How to see what happened:
```json
{
  "channel": "stdout | stderr | endpoint | logfile | metrics | debug_api",
  "access": "command, URL, or file path to access this channel",
  "description": "what information this channel provides",
  "fields": ["key fields available"],
  "metrics": [
    {
      "name": "metric_name",
      "direction": "lower_is_better | higher_is_better",
      "unit": "unit of measurement (optional)",
      "description": "what this metric measures and why it matters"
    }
  ]
}
```
Only include the `metrics` array on observability entries that expose quantitative metrics.

**quality** — How to evaluate the workflow's output:
```json
{
  "goal": "one-sentence description of what 'good' looks like",
  "trade_offs": [
    "natural language descriptions of tensions between parameters"
  ],
  "known_results": "any benchmarks, test results, or known-good configurations"
}
```

**verification** — How to verify changes to this workflow haven't broken it:
```json
{
  "command": "single command to verify (e.g., test suite)",
  "steps": [
    {
      "name": "step_name",
      "command": "command to run",
      "success": "what success looks like",
      "on_failure": "what to do if this step fails"
    }
  ]
}
```

---

## Phase 1: Identify the Workflow Boundary

Based on the user's description, determine what this workflow is:

1. **Name it** — choose a kebab-case identifier (e.g., `canyon-stealth-encoding`, `user-signup`, `deploy-to-prod`)
2. **Describe it** — one sentence: what does this workflow do and why does someone run it?
3. **Find the entry point** — where does this workflow start? Search for:
   - CLI commands (click, argparse, typer)
   - API endpoints (route handlers)
   - Test files that exercise this behavior
   - Scripts that invoke it
4. **Confirm with the user** — before deep exploration, state what you think the workflow is and its entry point. Ask if the scope is right.

**STOP and confirm the workflow boundary with the user before proceeding.**

---

## Phase 2: Trace Inputs

Starting from the entry point, trace ALL inputs to the workflow:

### Parameters
1. **Read the entry point** (CLI command, API handler, or function). List every parameter with its type, default, and description.
2. **Trace downstream** — follow the call chain. Parameters may be transformed, split, or renamed as they flow through layers. Capture the parameter at each access method:
   - CLI flag name (e.g., `--canyon-sigma`)
   - URL query parameter name (e.g., `?sigma=0.4`)
   - Function keyword argument name (e.g., `sigma`)
   - Config object field name (e.g., `ExperimentConfig.canyon_sigma`)
3. **Find hidden parameters** — look for:
   - Parameters only accessible via one mechanism (e.g., query param but no CLI flag)
   - Parameters with defaults buried in config objects, not exposed at the top level
   - Parameters that interact or constrain each other (e.g., "canyon forces distributed mode")
4. **Map `affects` relationships** — for each parameter, determine which output metrics it influences and in which direction. Check:
   - Test files that vary this parameter and assert on output metrics
   - Documentation or comments explaining the parameter's effect
   - Code logic: does changing this parameter feed into a metric calculation?

### Environment Variables
Search for `os.environ`, `os.getenv`, environment variable references in config files.

### Files
Identify input files: config files, data files, templates, images, databases. For each, note what the workflow reads from it and how.

### Infrastructure
Identify external dependencies: servers that must be running, database connections, network endpoints, third-party APIs. For each, find or infer:
- How to start it
- How to verify it's running
- Whether it's required or optional

---

## Phase 3: Map Run Mechanisms

Identify every way to invoke this workflow. Not all mechanisms will exist for every workflow — only include the ones that apply.

### CLI
- Find the CLI command and all its flags/arguments
- Construct a command template with `{param}` placeholders
- Write a complete, runnable example

### API
- Find HTTP/REST/GraphQL endpoints that trigger this workflow
- Search route handlers, URL configs, API decorators (`@app.route`, `@router.post`, etc.)
- Document: method, URL, headers, request body, response format
- Write a complete `curl` example
- Note authentication requirements, rate limits, or required headers

### UI (Web / Mobile)
- Find web pages, forms, or mobile screens that trigger this workflow
- Document the step-by-step user interaction (navigate to URL, fill form, click button)
- If browser automation exists (Playwright, Selenium, Cypress, XCTest), document the automation command
- If no automation exists, note this as a gap — the agent can only invoke via other mechanisms

### Tests
- Search for test files that exercise this workflow:
  - `grep` for the entry point function name, class name, or CLI command name in test files
  - Check test filenames for obvious matches
  - Read test files to understand what aspect of the workflow each test covers
- For each relevant test, note: command to run it, what it exercises, key assertions

### Code
- Document the programmatic entry point (class + method, or function)
- Write the call chain: step-by-step code showing how to invoke programmatically
- List key functions in the call chain with one-line descriptions

---

## Phase 4: Map Outputs

### State Changes
For each side effect of the workflow:
- What is created or modified? (DB record, file, in-memory state)
- Where does it persist?
- What are the key fields?
- How do you inspect it after the fact? (query, API endpoint, file read)

### Observability
Trace every observation channel:

1. **stdout/stderr** — what does the workflow print? What format? What key fields?
2. **Log output** — search for `logger`, `logging`, `print` in the call chain. What events are logged?
3. **Debug endpoints or commands** — API endpoints that expose internals, debug CLI flags, verbose modes
4. **Metrics** — quantitative measures of the output quality. For each metric:
   - What is it called?
   - What does it measure?
   - Is lower or higher better?
   - What unit?
   - Where is it computed? (function, module, test)
   - What's a good value? (if known from tests or documentation)

**Metrics are critical.** Without them, the agent cannot evaluate its own output. Search thoroughly:
- Test files that assert on numeric values
- Analysis/evaluation modules
- Benchmark code
- Documentation mentioning quality measures

---

## Phase 5: Define Quality Criteria

1. **Goal** — one sentence describing what "good" looks like for this workflow's output
2. **Trade-offs** — natural language descriptions of tensions:
   - Which parameters trade off against each other?
   - What improves one metric but degrades another?
   - Where are diminishing returns?
3. **Known results** — cite test results, benchmarks, or documented configurations that establish a baseline:
   - "test_filler_mode_stealth.py shows histogram beats log_uniform by 10% on KS"
   - "Production uses canyon_sigma=0.4 with chunk_size=256"

---

## Phase 6: Map Verification

Define how an agent verifies changes to this workflow haven't broken it:

1. **Primary verification command** — the single command that checks everything (e.g., `bash scripts/check`, `uv run pytest tests/ -v`)
2. **Step-by-step breakdown** — if the verification has stages:
   - What each step checks
   - What success looks like
   - What to do on failure (fix instruction, not just "it failed")

---

## Phase 7: Assemble and Store

1. **Assemble the JSON** — combine all findings into the schema structure defined above
2. **Validate completeness** — check every section:
   - [ ] Every parameter has type, default, and description
   - [ ] Every parameter with measurable effects has `affects` with direction
   - [ ] At least one run mechanism is documented
   - [ ] Outputs include at least one observability channel
   - [ ] Quality section has a goal and at least one trade-off
   - [ ] Verification section has a runnable command
3. **Verify accuracy** — for every file path, function name, CLI flag, and command in the mapping:
   - Confirm the file exists
   - Confirm the function/class exists at the stated location
   - Confirm CLI flags are spelled correctly
   - If possible, run the verification command to confirm it works
4. **Write the file** to `docs/workflows/{name}.json`
5. **Update CLAUDE.md** — if CLAUDE.md exists, add a reference to the workflow map under a `## Workflows` section. If the section already exists, append to it:
   ```markdown
   ## Workflows

   Structured workflow maps for agent consumption:
   - [{workflow name}](docs/workflows/{name}.json) — {one-line description}
   ```

---

## Phase 8: Present to User

Show the user:

1. **Summary** — what workflow was mapped, how many inputs/outputs/metrics found
2. **Highlights** — any surprising findings:
   - Parameters only accessible via one mechanism (e.g., query param but no CLI flag)
   - Missing observability (parts of the workflow with no way to inspect results)
   - Undocumented parameters found in code but not in CLI/docs
   - Missing tests (aspects of the workflow with no test coverage)
3. **The file location** — where the mapping was saved
4. **Gaps** — anything the skill could not determine and the user should fill in:
   - Metrics without known-good values
   - Trade-offs that require domain knowledge
   - Infrastructure setup that requires credentials or special access

> **What would you like to do?**
> 1. **Refine** — adjust the mapping based on your domain knowledge
> 2. **Map another workflow** — run again for a different workflow
> 3. **Done** — the mapping is complete

---

## Guidelines

- **Trace, don't guess.** Every field in the mapping must come from reading actual code, configs, or docs. Never infer a parameter's type or default without seeing the source.
- **Follow the data.** Start at the entry point and trace the call chain. Parameters get renamed, transformed, and split as they flow through layers. Capture the parameter at every level.
- **Metrics are the linchpin.** Without quantitative metrics and their direction (lower/higher is better), an agent cannot evaluate its own output. If no metrics exist, say so explicitly in the quality section — this is itself an important finding.
- **Multiple access methods matter.** A parameter may be settable via CLI, query param, config file, AND code. Capture all access methods — an agent may use any of them.
- **Don't fabricate relationships.** Only populate `affects` when you can trace a parameter's influence on a metric through code or test assertions. "This probably affects X" is not good enough.
- **Infrastructure is an input.** Servers, databases, and network connections are inputs to the workflow just like parameters and files. An agent cannot run the workflow without them.
- **Verification closes the loop.** The verification section is what makes this mapping actionable — it tells the agent how to check its own work. A mapping without verification is incomplete.
- **One workflow at a time.** Each mapping covers one workflow. If the user's description spans multiple workflows, ask them to pick one, or identify the boundaries and map each separately.
