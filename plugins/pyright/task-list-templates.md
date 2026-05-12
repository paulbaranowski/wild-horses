# Pyright per-intent `agentValidations` templates

Three template arrays — one per `--intent` (`silence`, `improve`,
`bugs-only`) — that `/pyright:run-and-fix` copies into each per-finding
`**AgentValidations:**` block of the markdown report it renders in
Phase 3. The `task-list-builder` (invoked in Phase 4 with
`--md-body-from-context`) then transcribes those bullets into each JSON
task's `agentValidations` array. Templates are loaded by the orchestrator
after the user resolves `--intent`; the matching block is copied verbatim
into every per-file task in the run.

The schema rule for `agentValidations` (in
`${CLAUDE_PLUGIN_ROOT}/../harness/task-list-schema.md`, find via
`Glob "**/harness/task-list-schema.md"` if cross-plugin path doesn't
resolve): **"if you can write a shell command that answers the question, it
belongs in `verifySteps`, not here."** The entries below are
inspection-verifiable factual claims a fresh-context validation subagent
confirms by reading the post-change diff and file contents. They are NOT
pass/fail signals a subprocess can decide — those live in `verifySteps`
(per-task `pyright <file>`).

## How the orchestrator uses this file

1. Phase 1 resolves `--intent` (from arg, prompt in Phase 2, or default).
2. Phase 3 Step 2 reads this file once and selects the block whose heading
   exactly matches `## Template: <intent>`.
3. For each per-file task in the markdown report, the orchestrator copies
   the block's bullet entries verbatim under that finding's
   `**AgentValidations:**` field.
4. Phase 4 Step 2's `task-list-builder` invocation transcribes the field
   into the JSON task's `agentValidations` array (per the builder's hard
   rule for `agentValidations` ingestion).
5. Phase 4 Step 3's `task-list-runner` invocation drives the per-task
   agents; the runner's Step 2.5 dispatches a fresh-context validation
   subagent that evaluates each entry against the post-change code,
   returning PASS or FAIL with `file:line` evidence.

## Authoring guidance for new templates

Keep these in mind when adding entries or templates:

- **Don't include command-answerable claims** like "no pyright errors", "no
  lint errors", "tests pass". Those belong in `verifySteps`. Per-task
  `pyright <file>` already covers the typing axis.
- **Don't write vague claims** like "the code is clean" or "looks good". The
  validation subagent reports `file:line` evidence; vague claims have no
  inspectable target.
- **Don't synthesize claims unrelated to the resolved errors.** Each entry
  should follow from the intent's behavior contract documented in
  `run-and-fix.md` Phase 3 § "Apply the chosen intent". If a claim doesn't
  trace back to that contract, it's not a `silence`/`improve`/`bugs-only`
  invariant — drop it.
- **Don't paraphrase between this file and Phase 3 § "Apply the chosen
  intent".** That section is the source of truth for what each intent does;
  this file is the source of truth for what each intent's tasks must
  validate. They reference each other but say different things.

## Template: `silence`

The `silence` intent leans on rule-specific suppressions and `cast()` at
boundaries. The validation surface is suppression discipline and
signature stability — `improve`-style structural changes are out of scope
under this intent.

- Every `# pyright: ignore` comment added in this task uses bracketed rule form (e.g., `# pyright: ignore[reportOptionalMemberAccess]`); no bare `# pyright: ignore` (without rule bracket) was introduced.
- Every suppression added in this task has a non-empty trailing rationale, separated by `—` or two spaces (e.g., `# pyright: ignore[reportArgumentType]  # tests use a builder that yields a wider type`).
- No `# type: ignore` markers (mypy syntax) were added; pyright suppression is `# pyright: ignore[<rule>]` only.
- No public function or method signature in any file the task resolves was widened or narrowed; private (leading-underscore) signature changes are allowed without further constraint.
- Any `cast()` call added in this task is at a trust boundary (HTTP response, user input, subprocess output) OR materializes a TypedDict from `dict[str, Any]` per a `rules.md` recipe.

## Template: `improve`

The `improve` intent leans on the semantically richer fix: widen, annotate,
extract factories, scan for opaque `dict[str, Any]`. The validation surface
is structural change discipline — random suppressions and silent semantic
collapses are the failure modes to catch.

- If a suppression present in the resolved files before this task could have been removed via type widening, annotation, or factory extraction, it was removed.
- If the resolved file had `dict[str, Any]` values read through 3+ distinct literal keys (per `rules.md` § "Opaque `dict[str, Any]` with repeated key reads"), either an extracted TypedDict or Pydantic model is now in use, OR the file's module-level docstring carries a `TODO(types):` marker acknowledging the deferred extraction with a one-line reason.
- No `# type: ignore` markers (mypy syntax) were added; any `# pyright: ignore` added uses bracketed rule form and a non-empty trailing rationale.

## Template: `bugs-only`

The `bugs-only` intent fixes only patterns documented in `bugs.md`;
everything else gets suppressed with the grep-able TODO marker. The
validation surface is: bug fixes are real bug fixes, non-bug suppressions
are uniformly marked, and no improvement-class structural work leaked in.

- Every code change in this task corresponds to a bug class documented in `bugs.md` (e.g., attribute that never existed, subclass attribute shadowing inherited method, repeated side-effectful call in a loop). No semantic refactors were applied beyond fixing the documented bug class.
- Every non-bug pyright error in the resolved files that was previously erroring is now suppressed with a rule-specific marker carrying the trailing comment `# TODO(types): revisit under --intent improve` so the locations are grep-able for a later improvement pass.
- No new `cast()` calls or type widenings were added beyond the minimum the bug-class fix required.
- No public function or method signature in any file the task resolves was modified.

## Adding a fourth intent

If a future `--intent` value is added, this file must be updated with a
fourth template block whose heading matches `## Template: <intent>` exactly
(case-sensitive). The orchestrator's lookup is by exact heading match; an
unrecognized intent stops the run with a message asking the user to verify
the new intent's template before proceeding.
