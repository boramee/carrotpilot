#!/usr/bin/env python3
"""
Rename rlog*.zst files to yyyymmddhhmiss.zst.

Timestamp is taken from each file's modification time.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys


def _format_timestamp(ts: float, use_utc: bool) -> str:
    if use_utc:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    else:
        dt = datetime.fromtimestamp(ts)
    return dt.strftime("%Y%m%d%H%M%S")


def _unique_target(directory: Path, base: str, suffix: str = ".zst") -> Path:
    candidate = directory / f"{base}{suffix}"
    if not candidate.exists():
        return candidate
    for i in range(1, 1000):
        candidate = directory / f"{base}_{i:02d}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Unable to find unique name for {base}{suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rename rlog*.zst files to yyyymmddhhmiss.zst"
    )
    parser.add_argument(
        "--dir",
        default=Path(__file__).resolve().parent,
        type=Path,
        help="Directory containing rlog*.zst files (default: script directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned renames without applying them",
    )
    parser.add_argument(
        "--utc",
        action="store_true",
        help="Use UTC timestamps instead of local time",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_dir = args.dir.expanduser().resolve()

    if not target_dir.exists() or not target_dir.is_dir():
        print(f"Directory not found: {target_dir}", file=sys.stderr)
        return 1

    files = sorted(target_dir.glob("rlog*.zst"))
    if not files:
        print(f"No matching files in {target_dir}")
        return 0

    for path in files:
        ts = path.stat().st_mtime
        base = _format_timestamp(ts, args.utc)
        dest = _unique_target(target_dir, base)
        if args.dry_run:
            print(f"DRY-RUN: {path.name} -> {dest.name}")
        else:
            path.rename(dest)
            print(f"{path.name} -> {dest.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
