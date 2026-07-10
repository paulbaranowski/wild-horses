---
name: pr
description: Open a PR for the current branch using pr-summary-writer for the title/body, then run cb-babysit up to three times (stop early on clean). Use when the user says /pr, "create a PR and babysit", "open a PR and babysit", or wants create-then-tend in one shot.
user-invocable: true
disable-model-invocation: true
argument-hint: "[optional base branch or extra gh pr create flags]"
---

# /pr — create PR, then babysit ×3

Open a PR for the current branch, write its description with **pr-summary-writer**, then tend it with **cb-babysit** up to three times (stop early if a pass exits clean).

**Dependencies (must be available this session):**

- `pr-summary-writer` (wild-horses) — description + title
- `cb-babysit` / `core:cb-babysit` (clipboard core) — CI + review tending

If either skill is missing, stop and tell the user what to install. Do not invent a substitute description format or a hand-rolled babysit loop.

**Arguments:** `$ARGUMENTS` — optional base branch name, or extra flags to pass through to `gh pr create` (e.g. `--draft`). Only pass values that are valid `gh pr create` flags or a base branch name — do not dump free-form prose into the create command.

---

## Composing other skills

For each dependency skill:

1. **Locate** its installed `SKILL.md` (plugin cache, marketplace checkout, or session skill list).
2. **Read** that file with the Read tool — do not rely on memory or a paraphrase.
3. **Execute** it fully, then return here to the next phase.

**Don't paraphrase** either skill's rules from memory. **Don't skip** the Read step even if you have used the skill before in this conversation.

When invoking cb-babysit, treat the captured PR URL (or number) as the skill's argument text — as if the user had run `/cb-babysit <url>`. That feeds its Setup parser so it targets the right PR.

---

## Phase 1 — Preflight

Run in parallel:

```bash
git status --short
git branch --show-current
git rev-parse --abbrev-ref HEAD@{upstream} 2>/dev/null
gh pr view --json number,url,state 2>/dev/null
```

Stop and report if:

- Not on a git repo, or on the default branch (`main` / `master`) with no feature branch.
- Working tree is dirty in a way that would leave uncommitted work out of the PR — ask the user to commit or stash first. Do not auto-commit.
- An open PR already exists for this branch — print its URL and ask whether to (a) skip create and only run the babysit loop on it, or (b) abort. Do not open a duplicate.

If the branch has no upstream, or is behind/ahead in a way that needs a push before create:

```bash
git push -u origin HEAD
```

---

## Phase 2 — Description via pr-summary-writer

1. Compose **pr-summary-writer** per **Composing other skills** above.
2. That skill produces a **title** and **body**. Because no PR exists yet, take the title and body it hands off — do not offer an edit confirmation loop here; `/pr` means create now.
3. Respect repo conventions the summary-writer already covers (conventional titles when the repo uses them, no "Generated with Claude" footers, no Co-Authored-By trailers).

---

## Phase 3 — Create the PR

```bash
gh pr create --title "<title>" --body "$(cat <<'EOF'
<body>
EOF
)" $ARGUMENTS
```

Notes:

- Use a single-quoted `EOF` heredoc so `$`, backticks, and quotes in the body stay literal.
- If `$ARGUMENTS` looks like a bare branch name (no leading `-`), pass it as `--base <name>` instead of raw args.
- Capture the PR URL from `gh pr create` output (or `gh pr view --json url -q .url` right after).

Print the PR URL, then continue — do not wait for the user.

---

## Phase 4 — Babysit up to 3 times

`/pr` owns the outer babysit loop. Run **cb-babysit** on the PR you just opened, **up to three sequential passes**.

cb-babysit's own Loop control may say "tell the user to re-run" or "wrap with `/loop`" on a `progressing` exit. **Ignore that advice while inside `/pr`** — continue to the next pass yourself until you hit 3 passes or an early-exit condition below.

For each pass `N` in `1..3`:

1. Compose **cb-babysit** / **core:cb-babysit** per **Composing other skills**, with the captured PR URL as its argument.
2. After the pass finishes, note its stop condition (`clean` / `progressing` / `stuck`).
3. **Early exit (clean):** if the pass exits **clean**, stop the loop. Do not run remaining passes.
4. **Stuck — soft vs hard:**
   - **Soft stuck** (CI still pending after watch timeout, or similar "re-run could help"): count the pass, then continue to the next pass immediately.
   - **Hard stuck** (auth/infra/external check/diagnosis-only with nothing actionable): stop the loop and report. Do not burn remaining passes.
5. **Progressing:** count the pass and start the next one immediately. CI wait lives inside cb-babysit's own `gh pr checks --watch`, not between passes.

After the loop (3 passes or early clean/hard-stuck stop), summarize:

- PR URL
- How many babysit passes ran, and each pass's stop condition
- Commits / replies made across passes (URLs if any)
- Anything still open (failing CI, deferred follow-ups, stuck reason)

---

## Rules

**Do:**

- Use pr-summary-writer for every title/body — never a changelog-style stub.
- Use cb-babysit for tending — never a hand-rolled "check CI and reply" shortcut.
- Push before create when the branch is not on the remote.
- Return the PR URL in the final summary.
- Own the outer babysit loop: on `progressing` or soft `stuck`, run the next pass yourself.

**Don't:**

- **Don't open a second PR** when one already exists for the branch.
- **Don't auto-commit** dirty work during preflight.
- **Don't run more than three** cb-babysit passes in this skill, even if the PR is still progressing.
- **Don't stop after a `progressing` exit** to wait for the user or suggest `/loop` — continue to the next pass until 3 or early exit.
- **Don't paraphrase** pr-summary-writer or cb-babysit from memory — Read each skill's SKILL.md before executing it.
- **Don't** append "Generated with Claude" footers or Co-Authored-By trailers.
