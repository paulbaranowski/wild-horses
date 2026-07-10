#!/usr/bin/env python3
"""Copy every plugin from a Cursor marketplace catalog into ~/.cursor/plugins/local.

Uses rsync for tree sync; never creates symlinks in the destination.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_DEST = Path.home() / ".cursor" / "plugins" / "local"
MANIFEST_REL = Path(".cursor-plugin") / "marketplace.json"
CURSOR_PLUGIN_MANIFEST = Path(".cursor-plugin") / "plugin.json"
RSYNC_EXCLUDES = (".git/", ".DS_Store")


def plugin_dest_error(name: str) -> str | None:
    if name in ("", ".", ".."):
        return "unsafe plugin name"
    if "/" in name or "\\" in name:
        return "plugin name must be a single path segment"
    if Path(name).name != name:
        return "unsafe plugin name"
    return None


def plugin_dest_dir(dest_root: Path, name: str) -> Path:
    if err := plugin_dest_error(name):
        raise ValueError(err)
    dest_dir = (dest_root / name).resolve()
    dest_root_resolved = dest_root.resolve()
    if dest_dir == dest_root_resolved or dest_root_resolved not in dest_dir.parents:
        raise ValueError(f"plugin destination escapes dest root: {name!r}")
    return dest_dir


def plugin_source_path(source: object) -> str | None:
    if isinstance(source, str):
        return source
    if isinstance(source, dict):
        path = source.get("path")
        if isinstance(path, str):
            return path
    return None


def normalize_plugin_root(plugin_root: str | None) -> str | None:
    if not plugin_root:
        return None
    root = plugin_root.removeprefix("./").rstrip("/")
    return root or None


def resolve_source_dir(
    marketplace_root: Path, source: str, plugin_root: str | None
) -> Path:
    rel = source.removeprefix("./")
    root = normalize_plugin_root(plugin_root)
    if root and not rel.startswith(f"{root}/"):
        rel = f"{root}/{rel}"
    return marketplace_root / rel


def load_catalog(manifest_path: Path) -> tuple[str | None, list[tuple[str, str]]]:
    data = json.loads(manifest_path.read_text())
    if not isinstance(data, dict):
        raise ValueError("marketplace manifest must be a JSON object")

    plugin_root = None
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        raw_root = metadata.get("pluginRoot")
        if isinstance(raw_root, str):
            plugin_root = raw_root

    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise ValueError("marketplace manifest missing 'plugins' array")

    entries: list[tuple[str, str]] = []
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        source = plugin_source_path(entry.get("source"))
        if isinstance(name, str) and source:
            entries.append((name, source))
    return plugin_root, entries


def rsync_copy(src_dir: Path, dest_dir: Path) -> None:
    rsync = shutil.which("rsync")
    if not rsync:
        raise RuntimeError("rsync is required")

    cmd = [
        rsync,
        "-a",
        "--delete",
        *[f"--exclude={pattern}" for pattern in RSYNC_EXCLUDES],
        f"{src_dir}/",
        f"{dest_dir}/",
    ]
    subprocess.run(cmd, check=True)


def copy_plugin(name: str, src_dir: Path, dest_dir: Path) -> None:
    if dest_dir.is_symlink():
        print(f"replacing symlink {dest_dir} with a real copy")
        dest_dir.unlink()
    dest_dir.mkdir(parents=True, exist_ok=True)
    rsync_copy(src_dir, dest_dir)
    if dest_dir.is_symlink():
        raise RuntimeError(f"destination is still a symlink after copy: {dest_dir}")
    print(f"copied {name} -> {dest_dir}")


def update_plugins(marketplace_root: Path, dest_root: Path) -> tuple[int, int]:
    manifest_path = marketplace_root / MANIFEST_REL
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing Cursor marketplace manifest: {manifest_path}")

    plugin_root, entries = load_catalog(manifest_path)
    dest_root.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    for name, source in entries:
        src_dir = resolve_source_dir(marketplace_root, source, plugin_root)
        if not src_dir.is_dir():
            print(f"skip {name}: source not found: {src_dir}", file=sys.stderr)
            skipped += 1
            continue
        if not (src_dir / CURSOR_PLUGIN_MANIFEST).is_file():
            print(
                f"skip {name}: no {CURSOR_PLUGIN_MANIFEST} under {src_dir}",
                file=sys.stderr,
            )
            skipped += 1
            continue

        try:
            dest_dir = plugin_dest_dir(dest_root, name)
        except ValueError as exc:
            print(f"skip {name}: {exc}", file=sys.stderr)
            skipped += 1
            continue

        copy_plugin(name, src_dir, dest_dir)
        copied += 1

    print(f"done: copied={copied} skipped={skipped} dest={dest_root}")
    return copied, skipped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy each plugin listed in .cursor-plugin/marketplace.json into "
            "~/.cursor/plugins/local as real files (never symlinks)."
        )
    )
    parser.add_argument(
        "marketplace_root",
        type=Path,
        help="Directory that contains .cursor-plugin/marketplace.json",
    )
    parser.add_argument(
        "dest_root",
        nargs="?",
        type=Path,
        default=DEFAULT_DEST,
        help=f"Destination root (default: {DEFAULT_DEST})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    marketplace_root = args.marketplace_root.resolve()
    dest_root = args.dest_root.expanduser().resolve()

    try:
        copied, _skipped = update_plugins(marketplace_root, dest_root)
    except (FileNotFoundError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0 if copied > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
