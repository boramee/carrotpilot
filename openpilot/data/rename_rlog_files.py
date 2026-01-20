#!/usr/bin/env python3
"""
Rename rlog*.zst files to yyyymmddhhmiss.zst.

Timestamp is taken from wallTimeNanos inside the rlog.
"""
from __future__ import annotations

import argparse
import bz2
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import capnp
import zstandard as zstd

from cereal import log as capnp_log

ZSTD_MAGIC = b"\x28\xB5\x2F\xFD"


def _format_timestamp_from_nanos(ts_nanos: int, use_utc: bool) -> str:
    ts = ts_nanos / 1e9
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


def _decompress_stream(data: bytes) -> bytes:
    dctx = zstd.ZstdDecompressor()
    with dctx.stream_reader(data) as reader:
        return reader.read()


def _read_log_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    ext = path.suffix.lower()
    if ext == ".bz2" or data.startswith(b"BZh9"):
        return bz2.decompress(data)
    if ext == ".zst" or data.startswith(ZSTD_MAGIC):
        return _decompress_stream(data)
    return data


def _extract_wall_time_nanos(path: Path) -> int:
    data = _read_log_bytes(path)
    try:
        events = capnp_log.Event.read_multiple_bytes(data)
        for event in events:
            which = event.which()
            if which in ("initData", "clocks", "boot"):
                wall_time = getattr(event, which).wallTimeNanos
                if wall_time:
                    return int(wall_time)
    except capnp.KjException as exc:
        raise RuntimeError(f"Corrupted log data in {path.name}") from exc

    raise RuntimeError(f"No wallTimeNanos found in {path.name}")


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

    errors = 0
    for path in files:
        try:
            wall_time_nanos = _extract_wall_time_nanos(path)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            errors += 1
            continue

        base = _format_timestamp_from_nanos(wall_time_nanos, args.utc)
        dest = _unique_target(target_dir, base)
        if args.dry_run:
            print(f"DRY-RUN: {path.name} -> {dest.name}")
        else:
            path.rename(dest)
            print(f"{path.name} -> {dest.name}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
