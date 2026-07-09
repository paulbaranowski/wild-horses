"""plan-keeper CLI implementation package.

I/O + naming + mutation backend for the plan-keeper skills, split from the
former single-file ``plan_keeper_cli.py`` into focused modules. The stable
entry point remains ``scripts/plan_keeper_cli.py`` (a thin shim that calls
``plan_keeper.cli.main``); this package holds the implementation.

Module map (leaf → top of tree):
  errors      — PlanKeeperCliError, HelpfulArgumentParser
  dates       — _iso_utc_now, parse_date_arg
  frontmatter — parse/serialize frontmatter, Kind validation, default injection
  storage     — path constants, atomic write, listing, sort order
  naming      — repo derivation, slugify, name/extension validation
  roots       - the multi-root registry (union reads, save routing, ticket resolve)
  config      — per-repo .plankeeper.json load/save + redaction
  http        — outbound JSON HTTP chokepoint
  linear      — Linear GraphQL client + push
  jira        — Jira REST client + push
  push        — the `push` subcommand backend
  groundcrew  — groundcrew shell-adapter glue
  cli         — cmd_* handlers, argparse wiring, main()
"""

# Single source of truth for the package version. `pyproject.toml` reads this
# attribute (dynamic = ["version"]) so the Homebrew package and the CLI's
# `--version` output never drift, and it is kept in lockstep with the
# plan-keeper plugin.json version. Bump both together when releasing.
__version__ = "6.11.1"
