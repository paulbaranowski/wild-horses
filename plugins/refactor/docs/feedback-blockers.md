# /refactor:feedback-blockers

**Core question:** _Can an AI edit this code and know whether it got it right?_

This is about **correctness and observability**, not cycle speed. When an AI makes an edit, the question is whether it can tell — without a human eyeballing the result — that the edit was correct. If tests pass but assert the wrong invariants, if effects happen invisibly to the caller, if the only confirmation a change worked is to run the full app and look at the UI, if a seam is so wide the agent can't isolate "did I break X?" from everything else, the agent has no way to verify its own work. Speed and noise show up as downstream symptoms of the same underlying problem.

## Usage

```text
/refactor:feedback-blockers src/auth/
/refactor:feedback-blockers src/api.py
/refactor:feedback-blockers the ingestion service
/refactor:feedback-blockers
/refactor:feedback-blockers --resume
```

The argument is a file path, directory path, or free-form description. With no argument, defaults to files changed on the current branch. `--resume` picks up an in-progress task list from `docs/exec-plans/active/`.

## How it works

Spawns 4 parallel specialist agents that each examine the code through a different lens, then merges their findings into a unified report with a prioritized remediation plan.

| Pillar                   | What it looks for                                                                                              |
| ------------------------ | -------------------------------------------------------------------------------------------------------------- |
| **Encapsulation**        | Leaky abstractions, mutable state exposure, missing boundary validation, god objects                           |
| **OOP Design**           | Procedural code hiding in classes, inheritance vs composition mismatches, SRP violations, missing polymorphism |
| **Testability**          | Hard-wired dependencies, hidden side effects, non-determinism, missing seams for test doubles                  |
| **Harness-Friendliness** | Opaque failures, large blast radius, implicit contracts, poor error locality                                   |

Findings from all four agents are deduplicated and merged. When the same code location is flagged by multiple agents (e.g., a god object that is also untestable and produces opaque errors), it's highlighted as a cross-pillar finding — these are the highest-leverage fixes because one refactor improves multiple pillars.

## Output

A ranked list of interventions. The report can optionally be implemented via [task-list-runner](task-list-runner.md) — each intervention becomes a task, agents implement them one at a time, progress is tracked in a JSON task file that supports `--resume` across sessions.

## How this differs from `/refactor:reasoning-gaps`

|             | reasoning-gaps                       | feedback-blockers                                 |
| ----------- | ------------------------------------ | ------------------------------------------------- |
| Asks        | "would an AI misread this?"          | "can an AI verify its edit was correct?"          |
| Lens        | comprehension                        | correctness & observability                       |
| Typical fix | add types, docs, narrow control flow | tighten assertions, surface effects, shrink seams |

Run reasoning-gaps first to establish a clean comprehension baseline, then feedback-blockers to attack what remains.
