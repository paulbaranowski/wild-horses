#!/usr/bin/env python3
"""Write or append to ~/.config/clearance/personal-allow-hosts.

Two modes:

**Create mode** (no --append):
  - If the target file does NOT exist: create it with the DEFAULT BODY and
    exit 0, printing the absolute path.
  - If the target file ALREADY exists: refuse to overwrite (exit 1, message
    to stderr). Protects hand-edited files.

**Append mode** (--append):
  - If the target does NOT exist: create it with the DEFAULT BODY (equivalent
    to create mode; append-to-nonexistent is treated as a fresh create).
  - If the target EXISTS: add only the Claude host lines not already present,
    using a case-insensitive host comparison. Only uncommented lines count as
    present — a host that appears only in a comment is re-added uncommented.
    If both Claude hosts are already present, this is a no-op. Appended lines
    go under a '# Claude Code runtime' section comment, added only once.
  - Idempotent: running --append twice must not produce duplicate host lines.
  - Uses read-modify-write-atomic (reads existing content, computes new
    content, writes atomically via tmp + fsync + os.replace).

Exit codes:
  0  — success (created, appended, or no-op)
  1  — refused to overwrite existing file (create mode only)
  2  — bad arguments
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

CLAUDE_HOSTS: list[str] = [
    "downloads.claude.ai",
    "mcp-proxy.anthropic.com",
]

DEFAULT_TARGET = "~/.config/clearance/personal-allow-hosts"

SECTION_COMMENT = "# Claude Code runtime"

DEFAULT_BODY = f"""\
# Personal egress allowlist, layered on top of groundcrew's starter file.
# Loaded when CLEARANCE_PERSONAL_HOSTS is set in your shell env.
# One host per line, # comments, *.example.com wildcards OK.

{SECTION_COMMENT}
downloads.claude.ai
mcp-proxy.anthropic.com

# Uncomment as needed
#storage.googleapis.com
#mcp.render.com
#api.x.ai
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="render_clearance_hosts.py",
        description="Write or append to the clearance personal-allow-hosts file.",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=(
            f"Path to the hosts file (default: {DEFAULT_TARGET}). "
            "Supports ~ expansion. Use this flag in tests to avoid touching "
            "the real ~/.config/clearance/ directory."
        ),
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append missing Claude hosts instead of creating from scratch.",
    )
    return parser.parse_args()


def write_atomic(target: Path, content: str) -> None:
    """Write content to target atomically: tmp file + fsync + os.replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    os.replace(tmp_path, target)


def present_hosts(content: str) -> set[str]:
    """Return the set of lowercased host names that are present (uncommented) in content."""
    hosts: set[str] = set()
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        hosts.add(stripped.lower())
    return hosts


def compute_append_content(existing: str, hosts_to_add: list[str]) -> str:
    """Return new file content with missing hosts appended.

    Adds only hosts not already present (case-insensitive). If hosts are added,
    they go under a '# Claude Code runtime' section comment — but only if that
    comment is not already in the file.
    """
    already_present = present_hosts(existing)
    missing = [h for h in hosts_to_add if h.lower() not in already_present]

    if not missing:
        return existing

    lines: list[str] = []

    # Ensure the existing content ends with exactly one newline before appending.
    base = existing.rstrip("\n") + "\n"

    needs_section_comment = not any(
        line.strip() == SECTION_COMMENT for line in existing.splitlines()
    )

    if needs_section_comment:
        lines.append("")
        lines.append(SECTION_COMMENT)

    for host in missing:
        lines.append(host)

    return base + "\n".join(lines) + "\n"


def cmd_create(target: Path) -> None:
    """Create mode: write DEFAULT_BODY or refuse if the file already exists."""
    if target.exists():
        sys.stderr.write(
            f"refusing to overwrite existing {target}; pass --append to add hosts\n"
        )
        sys.exit(1)
    write_atomic(target, DEFAULT_BODY)
    sys.stdout.write(str(target) + "\n")


def cmd_append(target: Path) -> None:
    """Append mode: create with DEFAULT_BODY if missing, else add missing Claude hosts."""
    if not target.exists():
        write_atomic(target, DEFAULT_BODY)
        sys.stdout.write(str(target) + "\n")
        return

    existing = target.read_text(encoding="utf-8")
    new_content = compute_append_content(existing, CLAUDE_HOSTS)

    if new_content != existing:
        write_atomic(target, new_content)

    sys.stdout.write(str(target) + "\n")


def main() -> None:
    args = parse_args()
    target = Path(args.target).expanduser().resolve()

    if args.append:
        cmd_append(target)
    else:
        cmd_create(target)


if __name__ == "__main__":
    main()
