# Scope resolution

Resolve the command argument in this order. First match wins.

1. **Existing path.** A file resolves to itself. A directory resolves to
   every file under it, recursively, filtered by the extension table.
2. **PR number or URL.** An integer, `#123`, or a GitHub PR URL. Build the
   file list and changed-lines map from the diff (recipe below).
3. **`--full`.** Every tracked file: `git ls-files`, filtered by the
   extension table.
4. **Free text.** Any other non-empty argument is text to handle inline in
   the reply. No files are read or written.
5. **No argument.** The current branch's changed files: the union of
   `git diff --name-only "$(git merge-base HEAD origin/HEAD)"` and dirty
   files from `git status --porcelain`. Whole files, no line filter.

## Extension table

| Extensions                              | Language   | Mode    |
| --------------------------------------- | ---------- | ------- |
| `.py`                                   | python     | comment |
| `.js` `.jsx` `.ts` `.tsx` `.mjs` `.cjs` | javascript | comment |
| `.sh` `.bash` `.zsh`                    | shell      | comment |
| `.md` `.markdown`                       | markdown   | prose   |
| `.txt`                                  | text       | prose   |

Comment mode edits comment and docstring prose only. Prose mode treats the
whole file as prose. Any other extension is skipped; report it with a
notice.

## Changed-lines map (PR scope)

Fetch the diff with `gh pr diff <number>`. For each file, parse the diff
body and keep only new-side lines that carry a `+` prefix. Skip the `+++`
file header. Coalesce adjacent kept lines into ranges.

A hunk header `@@ -a,b +c,d @@` gives the bounds of the whole hunk, and
those bounds include unchanged context lines. Use the header to track the
current new-side line number, never as a range of changed lines. Taking
`[c, c+d-1]` would report prose the PR never touched. Emit:

```json
{
  "relative/path.py": [
    [12, 18],
    [40, 40]
  ]
}
```

Pipe it to the scanner on stdin:

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT}}/scripts/plain_language_cli.py" scan --changed-lines - FILE... <<'EOF'
{ "relative/path.py": [[12, 18]] }
EOF
```

Use the same relative paths in the map as on the command line. The CLI
normalizes both sides with `os.path.normpath`.
