# wild-horses

A [Claude Code](https://claude.ai/code) plugin for engineering-quality code reviews.

## Skills

### harness-review

Analyzes code for **encapsulation**, **OOP design**, **testability**, and **harness-friendliness** (how well code supports tight feedback loops for agents and automated tooling).

Spawns 4 parallel specialist agents that each examine the code through a different lens, then merges their findings into a unified report with cross-pillar analysis and a single highest-impact refactor proposal.

#### What it evaluates

| Pillar | What it looks for |
|---|---|
| **Encapsulation** | Leaky abstractions, mutable state exposure, missing boundary validation, god objects |
| **OOP Design** | Procedural code hiding in classes, inheritance vs composition mismatches, SRP violations, missing polymorphism |
| **Testability** | Hard-wired dependencies, hidden side effects, non-determinism, missing seams for test doubles |
| **Harness-Friendliness** | Opaque failures, large blast radius, implicit contracts, poor error locality |

#### Usage

```
/wild-horses:harness-review                          # review files changed in current PR branch
/wild-horses:harness-review src/auth/                 # review a specific directory
/wild-horses:harness-review src/api.py                # review a specific file
/wild-horses:harness-review --scope module            # review the current module/package
/wild-horses:harness-review --scope full              # review all source files (slow)
```

#### Output

- Per-pillar ratings (X/10) with findings by severity
- Cross-pillar findings highlighted (same code flagged by multiple agents = highest leverage)
- Weighted overall score (testability and harness-friendliness weighted 30% each, others 20%)
- A single highest-impact refactor proposal with trade-offs
- Options to save the plan, implement it, or revise

## Install

```
/install-plugin paulbaranowski/wild-horses
```

## License

MIT
