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

The version lives in exactly one place — `__version__` in
`plan_keeper/__init__.py`:

- `pyproject.toml` reads it dynamically (`dynamic = ["version"]` +
  `[tool.setuptools.dynamic] version = { attr = "plan_keeper.__version__" }`),
  so the built wheel/`--version` output always agrees with the module.
- It is kept **in lockstep with `plugin.json`'s `version`**. `TestVersion` in
  `tests/test_cli.py` fails the suite if the two diverge.

So a release bumps two numbers that must be equal: `__version__` and the
plugin.json `version`. Use the plugin bump rule (patch = fix, minor = feature,
major = breaking).

## Procedure

Cutting a release is **automated**. You bump the version and merge; CI tags the
release and re-points the formula. The manual steps are kept below only as a
fallback for when the workflow is unavailable.

### Automated (default)

1. Edit `__version__` in `plugins/plan-keeper/scripts/plan_keeper/__init__.py`.
2. Set the matching `version` in `plugins/plan-keeper/.claude-plugin/plugin.json`.
3. Run the tests locally — `TestVersion` enforces the lockstep:

   ```bash
   python3 -m unittest discover -s plugins/plan-keeper/scripts/tests
   ```

4. Open a PR and **merge it to `main`**.

That's it. Merging a `plugin.json` version change to `main` triggers
`.github/workflows/publish-plan-keeper.yml`, which:

- reads the new version from `plugin.json`;
- **skips if `plan-keeper-v<version>` already exists** (so a description-only edit
  publishes nothing — the path-filtered trigger behaves as "publish on version
  change");
- re-runs the test gate (a `__version__` ↔ `plugin.json` mismatch fails here, so a
  half-done bump can't ship);
- tags the merge commit `plan-keeper-v<version>` and pushes it; and
- checks out the tap and runs its `update-formula.sh <version>` (with retry),
  committing and pushing the re-pointed `Formula/plan-keeper.rb`.

The job runs on a **macOS runner** so it can call the tap's `update-formula.sh`
verbatim — that script uses BSD `sed -i ''`, which fails under GNU sed on Linux.
Keeping the call verbatim leaves the formula-rewrite logic single-sourced in the
tap. wild-horses is public, so the macOS minutes are free.

If the workflow fails, GitHub emails the repo owner the failed-run notice.
Re-running is safe — the guard won't double-tag. But note the one asymmetric
case: the job tags **before** it re-points the formula, so if it dies _after_
tagging (e.g. the formula step exhausts its retries), a re-run hits the guard,
sees the tag, and **skips** — it will not re-point the formula. In that case the
tag already exists, so finish with just **manual step 3 (Re-point the formula)**
below; skip the tagging step.

#### One-time setup: the tap token

The cross-repo push needs a fine-grained PAT, stored as the `HOMEBREW_TAP_TOKEN`
secret on wild-horses. Create it once (rotate ~yearly):

1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate.
2. Resource owner `paulbaranowski`, **Only select repositories** → `homebrew-tap`.
3. Repository permissions → **Contents: Read and write**. ~1-year expiry.
4. Store it:

   ```bash
   gh secret set HOMEBREW_TAP_TOKEN --repo paulbaranowski/wild-horses
   ```

The release tag itself is pushed with the workflow's built-in `GITHUB_TOKEN`
(`permissions: contents: write`) — no PAT needed for that half.

### Manual (fallback)

Use this only if the workflow is disabled or broken.

#### 1. Bump + merge (in `wild-horses`)

Do steps 1–4 from **Automated** above (edit `__version__`, match `plugin.json`,
run tests, merge to `main`).

#### 2. Tag the merge commit (in `wild-horses`)

Tag the commit that actually landed on `main` — not a pre-merge branch HEAD, or
the tarball won't match what's published.

```bash
git checkout main && git pull
git tag -a plan-keeper-v<version> -m "plan-keeper <version>"
git push origin plan-keeper-v<version>
```

#### 3. Re-point the formula (in `homebrew-tap`)

Run this **after** the tag is pushed — the script fetches the tagged tarball to
hash it, and fails loudly if the tag doesn't exist yet.

```bash
git clone https://github.com/paulbaranowski/homebrew-tap.git   # if needed
cd homebrew-tap
./update-formula.sh plan-keeper <version>   # rewrites url + sha256 in Formula/plan-keeper.rb
git commit -am "plan-keeper <version>"
git push
```

#### 4. Verify

```bash
brew update && brew upgrade plan-keeper
plan-keeper --version                  # → plan-keeper <version>
```

A clean `brew upgrade` exercises the whole chain at once: re-download the tagged
tarball, re-verify the pinned sha256, rebuild the venv via `pip install` (which
re-resolves `__version__`), and link the binary.

## Gotchas

- **Tag the merge commit, not the branch.** The formula downloads the tarball for
  the tag; it must be the canonical commit on `main`. (The workflow does this for
  you — it tags the commit that triggered it on `main`.)
- **Push the tag before running `update-formula.sh`.** The tarball must exist for
  the script to hash it. (The workflow tags first, then retries the formula step
  to absorb tarball-generation lag.)
- **`__version__` and `plugin.json` must match** — the suite enforces it, but set
  both in the same change.
