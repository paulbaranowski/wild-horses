# Releasing the `plan-keeper` CLI to Homebrew

The standalone `plan-keeper` command is distributed through a Homebrew tap. This
is the maintainer runbook for cutting a new version.

## How distribution works

There is **no PyPI**. The release is entirely tag-driven across two repos:

- **`paulbaranowski/wild-horses`** (this repo) — holds the CLI source
  (`plugins/plan-keeper/scripts/plan_keeper_cli.py`) and its packaging manifest
  (`pyproject.toml`). The "release artifact" is a git tag: `plan-keeper-v<version>`.
  GitHub auto-generates a source tarball for that tag.
- **`paulbaranowski/homebrew-tap`** — holds `Formula/plan-keeper.rb`. The formula's
  `url` points at the tagged tarball and `sha256` pins its exact bytes; Homebrew
  refuses to build if the hash doesn't match, so it must be recomputed every release.
  The repo ships `update-formula.sh` to automate that.

The CLI is **dual-home**: the same source file is invoked in-place by the plugin
(`python3 .../plan_keeper_cli.py`) and packaged into the `plan-keeper` binary by
the formula. One physical source, two delivery vehicles — no second copy to drift.

## Version: one source of truth

The version lives in exactly one place — `__version__` in `plan_keeper_cli.py`:

- `pyproject.toml` reads it dynamically (`dynamic = ["version"]` +
  `[tool.setuptools.dynamic] version = { attr = "plan_keeper_cli.__version__" }`),
  so the built wheel/`--version` output always agrees with the module.
- It is kept **in lockstep with `plugin.json`'s `version`**. `TestVersion` in
  `test_plan_keeper_cli.py` fails the suite if the two diverge.

So a release bumps two numbers that must be equal: `__version__` and the
plugin.json `version`. Use the plugin bump rule (patch = fix, minor = feature,
major = breaking).

## Procedure

### 1. Bump + merge (in `wild-horses`)

1. Edit `__version__` in `plugins/plan-keeper/scripts/plan_keeper_cli.py`.
2. Set the matching `version` in `plugins/plan-keeper/.claude-plugin/plugin.json`.
3. Run the tests — `TestVersion` enforces the lockstep:

   ```bash
   python3 plugins/plan-keeper/scripts/test_plan_keeper_cli.py
   ```

4. Open a PR and **merge it to `main`** before tagging.

### 2. Tag the merge commit (in `wild-horses`)

Tag the commit that actually landed on `main` — not a pre-merge branch HEAD, or
the tarball won't match what's published.

```bash
git checkout main && git pull
git tag -a plan-keeper-v<version> -m "plan-keeper <version>"
git push origin plan-keeper-v<version>
```

### 3. Re-point the formula (in `homebrew-tap`)

Run this **after** the tag is pushed — the script fetches the tagged tarball to
hash it, and fails loudly if the tag doesn't exist yet.

```bash
git clone https://github.com/paulbaranowski/homebrew-tap.git   # if needed
cd homebrew-tap
./update-formula.sh <version>          # rewrites url + sha256 in Formula/plan-keeper.rb
git commit -am "plan-keeper <version>"
git push
```

### 4. Verify

```bash
brew update && brew upgrade plan-keeper
plan-keeper --version                  # → plan-keeper <version>
```

A clean `brew upgrade` exercises the whole chain at once: re-download the tagged
tarball, re-verify the pinned sha256, rebuild the venv via `pip install` (which
re-resolves `__version__`), and link the binary.

## Gotchas

- **Tag the merge commit, not the branch.** The formula downloads the tarball for
  the tag; it must be the canonical commit on `main`.
- **Push the tag before running `update-formula.sh`.** The tarball must exist for
  the script to hash it.
- **`__version__` and `plugin.json` must match** — the suite enforces it, but set
  both in the same change.
