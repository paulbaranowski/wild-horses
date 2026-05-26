---
description: Install plan-keeper's groundcrew shell-adapter wrappers into the user's groundcrew config dir and wire up the sources entry in ~/.config/groundcrew/config.ts. Idempotent — safe to re-run.
---

# /plan-keeper:setup-groundcrew

Wire this plugin's bash wrappers into the user's groundcrew installation so that plans under `~/plans/<repo>/*.md` become dispatchable groundcrew tickets.

The command is **idempotent**: re-running it after a previous successful setup is a no-op (it detects existing files and existing config entries).

The command **never** silently mutates files. Every write is preceded by a diff and an explicit confirmation.

## Procedure

Follow these steps in order. Do not skip steps.

### 1. Detect the user's groundcrew installation

Run:

```bash
{
  echo "crew_on_path=$(command -v crew 2>/dev/null || echo none)"
  echo "config_ts_exists=$([ -f ~/.config/groundcrew/config.ts ] && echo yes || echo no)"
  echo "config_dir_exists=$([ -d ~/.config/groundcrew ] && echo yes || echo no)"
}
```

**Branch on the result:**

- Both `crew_on_path != none` AND `config_ts_exists == yes` → groundcrew is installed and configured. Proceed to step 2.
- `config_ts_exists == no` but `config_dir_exists == yes` → config dir exists but no config.ts. Tell the user: "I found `~/.config/groundcrew/` but no `config.ts`. Has groundcrew been initialised? You may need to run `crew init` first (or copy `crew.config.example.ts` from a groundcrew checkout). Re-run `/plan-keeper:setup-groundcrew` once `config.ts` exists." Stop.
- `crew_on_path == none` AND `config_ts_exists == no` → groundcrew probably not installed. Tell the user: "I couldn't find `crew` on PATH or `~/.config/groundcrew/config.ts`. groundcrew doesn't seem to be installed. Install it first (see <https://github.com/jasonkneen/groundcrew> or wherever your team gets it), then re-run `/plan-keeper:setup-groundcrew`." Stop.

### 2. Locate this plugin's bash wrappers

The wrappers ship at `${CLAUDE_PLUGIN_ROOT}/groundcrew/{fetch,resolveOne,markInProgress}.sh`. Confirm:

```bash
ls "${CLAUDE_PLUGIN_ROOT}/groundcrew/"
```

Expected: `README.md`, `fetch.sh`, `markInProgress.sh`, `resolveOne.sh`.

Also resolve the absolute path to `plan_keeper_cli.py` for the `$PLAN_KEEPER_CLI` env var the user will set:

```bash
PLUGIN_GROUNDCREW="${CLAUDE_PLUGIN_ROOT}/groundcrew"
PLUGIN_CLI="${CLAUDE_PLUGIN_ROOT}/scripts/plan_keeper_cli.py"
echo "PLUGIN_GROUNDCREW=$PLUGIN_GROUNDCREW"
echo "PLUGIN_CLI=$PLUGIN_CLI"
```

If either path doesn't exist, the plugin install is broken — tell the user and stop.

### 3. Choose the install location

Default: `~/.config/groundcrew/plan-keeper/`. The `plan-keeper` basename identifies which plugin owns these scripts (so future plugins that ship groundcrew adapters can coexist).

Ask the user (AskUserQuestion):

- **Use default `~/.config/groundcrew/plan-keeper/`** (recommended)
- **Specify a different directory** (then prompt for the path)
- **Skip the copy step** (use the plugin path directly — fragile because it changes on plugin version bump)

Save the resolved destination as `INSTALL_DIR`. For the "skip copy" branch, `INSTALL_DIR=$PLUGIN_GROUNDCREW`.

### 4. Copy the wrappers (if not skipped)

If the user picked an install dir:

```bash
ls -la "$INSTALL_DIR" 2>/dev/null || echo "(doesn't exist yet)"
```

**If the dir already exists with files matching `fetch.sh` / `resolveOne.sh` / `markInProgress.sh`:**

Compare each existing file to the plugin's version using `diff -q`. If they're identical → tell the user "already up to date" and skip the copy. If they differ → ask for explicit confirmation before overwriting (show a `diff -u` of one of them so the user can see what changes).

**Otherwise:**

```bash
mkdir -p "$INSTALL_DIR"
cp -p "$PLUGIN_GROUNDCREW/fetch.sh" "$INSTALL_DIR/"
cp -p "$PLUGIN_GROUNDCREW/resolveOne.sh" "$INSTALL_DIR/"
cp -p "$PLUGIN_GROUNDCREW/markInProgress.sh" "$INSTALL_DIR/"
ls -l "$INSTALL_DIR"
```

Confirm with the user that the listing shows three executable `.sh` files.

### 5. Edit `~/.config/groundcrew/config.ts`

This step is the riskiest — we're mutating a user-owned config file. Safety rails:

1. **Always back up first** (timestamped sibling: `config.ts.bak.YYYYMMDD-HHMMSS`)
2. **Always show a unified diff before writing**
3. **Always require explicit confirmation** (AskUserQuestion) before saving
4. **Idempotency**: detect an existing entry whose `name: "plan-keeper"` and skip the edit (tell the user "already configured")

Use the inline Python below — it parses the `sources: [...]` array carefully (tracks brace depth, ignores braces inside strings) and inserts the new entry before the array's closing `]`. Capture stdout to a temp file:

```bash
CONFIG_TS=~/.config/groundcrew/config.ts
PLAN_KEEPER_CLI_PATH="$PLUGIN_CLI"
NEW_CONFIG=$(mktemp)

PLAN_KEEPER_CLI_PATH="$PLAN_KEEPER_CLI_PATH" \
INSTALL_DIR="$INSTALL_DIR" \
python3 <<'PYEOF' "$CONFIG_TS" > "$NEW_CONFIG"
import os
import re
import sys

src_path = sys.argv[1]
install_dir = os.environ["INSTALL_DIR"]
plan_keeper_cli = os.environ["PLAN_KEEPER_CLI_PATH"]

with open(src_path, encoding="utf-8") as f:
    text = f.read()

# Idempotency check: an existing plan-keeper source entry means we're done.
if re.search(r'name:\s*["\']plan-keeper["\']', text):
    print("__PLAN_KEEPER_ALREADY_CONFIGURED__", file=sys.stderr)
    sys.stdout.write(text)
    sys.exit(0)

# Find the `sources: [` array opener. Anchor on the literal text — fragile
# only if the user reformatted "sources" to something exotic, in which case
# the user can paste the snippet manually.
m = re.search(r'\bsources\s*:\s*\[', text)
if not m:
    print("__NO_SOURCES_ARRAY__", file=sys.stderr)
    sys.exit(2)

# Walk from the `[` to its matching `]`, tracking braces/brackets and
# respecting double-quoted, single-quoted, and template strings.
start = m.end() - 1  # index of `[`
i = start + 1
depth = 1
in_str = None  # None | '"' | "'" | '`'
escaped = False
while i < len(text) and depth > 0:
    ch = text[i]
    if escaped:
        escaped = False
    elif in_str:
        if ch == "\\":
            escaped = True
        elif ch == in_str:
            in_str = None
    elif ch in ('"', "'", "`"):
        in_str = ch
    elif ch == "[":
        depth += 1
    elif ch == "]":
        depth -= 1
        if depth == 0:
            break
    i += 1
if depth != 0:
    print("__UNMATCHED_BRACKETS__", file=sys.stderr)
    sys.exit(2)
close_idx = i  # index of matching `]`

# Decide whether the array already has entries. If yes, we need a leading
# comma; if no, we don't.
array_body = text[start + 1 : close_idx].strip()
needs_leading_comma = bool(array_body) and not array_body.endswith(",")

entry_lines = [
    "    {",
    '      kind: "shell",',
    '      name: "plan-keeper",',
    "      commands: {",
    f'        fetch: "{install_dir}/fetch.sh",',
    f'        resolveOne: "{install_dir}/resolveOne.sh ${{id}}",',
    f'        markInProgress: "{install_dir}/markInProgress.sh",',
    "      },",
    "    },",
]
entry = ("," if needs_leading_comma else "") + "\n" + "\n".join(entry_lines) + "\n  "

new_text = text[:close_idx] + entry + text[close_idx:]
sys.stdout.write(new_text)
PYEOF

py_rc=$?
case $py_rc in
  0)
    # Either patched-successfully OR idempotent-skip — examine stderr.
    : ;;
  2)
    echo "ERROR: couldn't locate a sources: [ ] array in config.ts" >&2
    echo "Falling back to print-snippet mode below."
    ;;
esac
```

After the inline-Python run, three outcomes are possible (Claude should branch on which one happened):

**Outcome A — idempotent skip** (stderr contained `__PLAN_KEEPER_ALREADY_CONFIGURED__`): tell the user "Your config.ts already has a `plan-keeper` source entry. Skipping the edit." Skip to step 6.

**Outcome B — patched successfully** (rc 0, no marker on stderr): show the diff to the user:

```bash
diff -u "$CONFIG_TS" "$NEW_CONFIG"
```

Then ask (AskUserQuestion):

- **Apply the patch** (back up to `config.ts.bak.<timestamp>` and replace)
- **Skip — I'll paste it myself** (print the entry from step 6 instead)
- **Cancel**

On "Apply":

```bash
TS=$(date +%Y%m%d-%H%M%S)
cp -p "$CONFIG_TS" "$CONFIG_TS.bak.$TS"
cp "$NEW_CONFIG" "$CONFIG_TS"
echo "Backup saved at $CONFIG_TS.bak.$TS"
```

**Outcome C — parse failure** (rc 2): the config.ts has an unusual structure. Tell the user "Couldn't safely auto-patch your config.ts — please paste the snippet from step 6 by hand. (Backup not taken because no write was attempted.)"

In all three outcomes, clean up: `rm -f "$NEW_CONFIG"`.

### 6. Print the post-install instructions

Regardless of whether step 5 patched config.ts or fell back, always print these final instructions to the user, with paths interpolated:

> **Set the env var (one-time):**
>
> Add to your shell rc (`~/.zshrc`, `~/.bashrc`, etc.):
>
> ```bash
> export PLAN_KEEPER_CLI="<PLUGIN_CLI>"
> ```
>
> The wrappers fall back to a relative path that only works when called from inside the plugin tree, so this env var is required when running from `~/.config/groundcrew/plan-keeper/`. Restart your shell (or `source ~/.zshrc`) before the next step.

If step 5 fell back (Outcome C or "skip — paste myself"), also print:

> **Paste this into the `sources:` array in `~/.config/groundcrew/config.ts`:**
>
> ```ts
> {
>   kind: "shell",
>   name: "plan-keeper",
>   commands: {
>     fetch: "<INSTALL_DIR>/fetch.sh",
>     resolveOne: "<INSTALL_DIR>/resolveOne.sh ${id}",
>     markInProgress: "<INSTALL_DIR>/markInProgress.sh",
>   },
> },
> ```

### 7. Suggest verification

Tell the user:

> **Verify with:**
>
> ```bash
> crew doctor
> ```
>
> You should see a `plan-keeper` source listed. Save a plan via `/plan-save`, promote it via `/plan-update` (Status=todo), then `crew doctor` should report it as a dispatchable ticket. `crew run --ticket <id>` will then dispatch and flip the plan's `Status:` to `in-progress`.

## Common mistakes

- **Don't edit config.ts without showing a diff first.** Backup-and-rewrite-silently is what corrupts user configs; the diff-and-confirm pattern is what makes this safe.
- **Don't assume the user's `sources:` array is empty.** A user with existing source entries should get a comma-prefixed insertion; the inline Python handles this via `needs_leading_comma`.
- **Don't try to set `$PLAN_KEEPER_CLI` in the user's shell rc directly.** Editing a user's `.zshrc` is more invasive than editing `config.ts` — print the export line and let them paste.
- **Don't use `--no-preserve=mode` semantics in `cp`.** The bash wrappers must stay executable; `cp -p` preserves the mode bit, default `cp` on macOS does too but Linux behaviour varies. Always use `cp -p`.

## Notes

- This command does not modify the user's `~/plans/` tree or any plan files.
- This command does not install groundcrew. It assumes groundcrew is already installed.
- The `$PLAN_KEEPER_CLI` env var indirection means the user's `config.ts` paths stay stable across plugin version bumps — only the env var needs to change when they upgrade the plugin.
- Idempotency is by `name: "plan-keeper"` detection. If you've manually wired up a source with a different name, this command will think it's not configured and try to add a fresh entry. Rename your existing entry to `plan-keeper` first, or accept that you'll get a duplicate to clean up.
