# wild-horses

A [Claude Code](https://claude.ai/code) plugin marketplace for harness engineering — making code AI-readable and agent-friendly.

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

### reasoning-gaps

Analyzes code for **AI reasoning gaps** — places where AI agents struggle to trace data flow, predict control flow, or understand state mutations. Focuses on making code **AI-readable**: can an agent confidently determine what data flows where, what happens at runtime, and how the code is structured?

Spawns 3 parallel specialist agents that examine code through different lenses, then merges findings into a prioritized remediation plan.

#### What it evaluates

| Dimension | What it looks for |
|---|---|
| **Type & Data Contracts** | Untyped signatures, dict-based data passing, missing return types, `Any` usage, stringly-typed interfaces, missing boundary validation |
| **Implicit Flow & State** | Decorator side effects, dynamic dispatch, magic methods, signal/event systems, global mutable state, hidden mutations |
| **Structure & Documentation** | Missing module/class docstrings, long functions, deep nesting, circular imports, undocumented protocols |

#### Usage

```text
/reasoning-gaps:reasoning-gaps                            # analyze files changed in current PR branch
/reasoning-gaps:reasoning-gaps src/auth/                  # analyze a specific directory
/reasoning-gaps:reasoning-gaps src/api.py                 # analyze a specific file
/reasoning-gaps:reasoning-gaps --scope module             # analyze the current module/package
/reasoning-gaps:reasoning-gaps --scope full               # analyze all source files (slow)
/reasoning-gaps:reasoning-gaps --scope imports src/api.py # analyze file + its import graph
```

#### Output

- Per-dimension ratings (X/10) with findings by severity
- Cross-dimension findings highlighted (same code flagged by multiple agents = highest leverage)
- Weighted overall score with letter grade (A-F)
- Top 5 prioritized interventions with effort estimates
- Options to save the plan, implement the top fix, or revise

## Install

1. Run `/plugin` in Claude Code
2. Select **Marketplaces**
3. Select **Add marketplace**
4. Enter `paulbaranowski/wild-horses`

## License

MIT
