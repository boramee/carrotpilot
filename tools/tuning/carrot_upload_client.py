#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


def route_prefix_from_segment(segment_name: str) -> str | None:
  if "--" not in segment_name:
    return None
  return segment_name.rsplit("--", 1)[0]


def segment_index(segment_name: str) -> int:
  if "--" not in segment_name:
    return -1
  try:
    return int(segment_name.rsplit("--", 1)[1])
  except ValueError:
    return -1


class CarrotUploadClient:
  def __init__(self, server_url: str, token: str, include_rlog: bool, state_file: Path, log_root: Path):
    self.server_url = server_url.rstrip("/")
    self.token = token
    self.include_rlog = include_rlog
    self.state_file = state_file
    self.log_root = log_root
    self.session = requests.Session()
    self.uploaded_routes = self._load_state()

  def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if self.token:
      headers["Authorization"] = f"Bearer {self.token}"
    if extra:
      headers.update(extra)
    return headers

  def _load_state(self) -> dict[str, Any]:
    if not self.state_file.exists():
      return {"uploaded_routes": {}}
    try:
      return json.loads(self.state_file.read_text(encoding="utf-8"))
    except Exception:
      return {"uploaded_routes": {}}

  def _save_state(self) -> None:
    self.state_file.parent.mkdir(parents=True, exist_ok=True)
    self.state_file.write_text(json.dumps(self.uploaded_routes, indent=2, ensure_ascii=False), encoding="utf-8")

  def _is_uploaded(self, route: str) -> bool:
    return route in self.uploaded_routes.get("uploaded_routes", {})

  def _mark_uploaded(self, route: str) -> None:
    self.uploaded_routes.setdefault("uploaded_routes", {})[route] = {
      "uploaded_at": int(time.time()),
    }
    self._save_state()

  def _all_segments(self) -> list[Path]:
    if not self.log_root.exists():
      return []
    return [p for p in self.log_root.iterdir() if p.is_dir() and "--" in p.name]

  def _route_to_segments(self) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    for seg in self._all_segments():
      route = route_prefix_from_segment(seg.name)
      if route is None:
        continue
      grouped.setdefault(route, []).append(seg)
    for route, segs in grouped.items():
      grouped[route] = sorted(segs, key=lambda s: segment_index(s.name))
    return grouped

  def _route_closed(self, segments: list[Path]) -> bool:
    for seg in segments:
      try:
        if any(p.name.endswith(".lock") for p in seg.iterdir()):
          return False
      except OSError:
        return False
    return True

  def _pending_routes(self) -> list[str]:
    grouped = self._route_to_segments()
    pending: list[tuple[float, str]] = []
    for route, segs in grouped.items():
      if self._is_uploaded(route):
        continue
      if not self._route_closed(segs):
        continue
      mtime = max(s.stat().st_mtime for s in segs)
      pending.append((mtime, route))
    pending.sort(reverse=True)
    return [r for _, r in pending]

  def _upload_file(self, route: str, segment: str, path: Path, retries: int = 4) -> None:
    url = (
      f"{self.server_url}/upload"
      f"?route={quote(route, safe='')}"
      f"&segment={quote(segment, safe='')}"
      f"&filename={quote(path.name, safe='')}"
    )
    size = path.stat().st_size

    delay = 2.0
    for attempt in range(1, retries + 1):
      try:
        with path.open("rb") as f:
          headers = self._headers({"Content-Length": str(size)})
          resp = self.session.post(url, data=f, headers=headers, timeout=120)
        if resp.status_code == 200:
          return
        raise RuntimeError(f"upload failed status={resp.status_code}, body={resp.text[:300]}")
      except Exception as e:
        if attempt == retries:
          raise RuntimeError(f"upload failed for {path}: {e}") from e
        time.sleep(delay)
        delay *= 2.0

  def _complete_route(self, route: str, retries: int = 4) -> None:
    url = f"{self.server_url}/complete"
    payload = {"route": route}

    delay = 2.0
    for attempt in range(1, retries + 1):
      try:
        resp = self.session.post(url, json=payload, headers=self._headers(), timeout=30)
        if resp.status_code == 200:
          return
        raise RuntimeError(f"complete failed status={resp.status_code}, body={resp.text[:300]}")
      except Exception as e:
        if attempt == retries:
          raise RuntimeError(f"complete failed for {route}: {e}") from e
        time.sleep(delay)
        delay *= 2.0

  def _segment_log_files(self, seg: Path) -> list[Path]:
    files: list[Path] = []
    q_candidates = ["qlog.zst", "qlog.bz2", "qlog"]
    r_candidates = ["rlog.zst", "rlog.bz2", "rlog"]

    for q in q_candidates:
      p = seg / q
      if p.exists():
        files.append(p)
        break

    if self.include_rlog:
      for r in r_candidates:
        p = seg / r
        if p.exists():
          files.append(p)
          break

    return files

  def upload_route(self, route: str) -> None:
    grouped = self._route_to_segments()
    segs = grouped.get(route, [])
    if not segs:
      raise RuntimeError(f"route not found: {route}")
    if not self._route_closed(segs):
      raise RuntimeError(f"route still open/locked: {route}")

    print(f"[upload] route={route} segments={len(segs)} include_rlog={self.include_rlog}")
    for seg in segs:
      files = self._segment_log_files(seg)
      if not files:
        continue
      for f in files:
        print(f"[upload] {seg.name}/{f.name}")
        self._upload_file(route, seg.name, f)

    self._complete_route(route)
    self._mark_uploaded(route)
    print(f"[upload] complete queued for analysis: {route}")

  def upload_latest_pending(self) -> bool:
    pending = self._pending_routes()
    if not pending:
      return False
    route = pending[0]
    self.upload_route(route)
    return True


def build_argparser() -> argparse.ArgumentParser:
  p = argparse.ArgumentParser(description="Carrot automatic uploader (offroad trigger)")
  p.add_argument("--server-url", required=True, help="Pipeline server base URL, e.g. https://example.com:9090")
  p.add_argument("--token", default="", help="Upload token (Bearer)")
  p.add_argument("--include-rlog", action="store_true", help="Upload rlog in addition to qlog")
  p.add_argument("--once", action="store_true", help="Upload latest pending route once and exit")
  p.add_argument("--route", default="", help="Upload one explicit route and exit")
  p.add_argument("--poll-sec", type=float, default=10.0, help="Polling interval in seconds")
  p.add_argument("--settle-sec", type=float, default=90.0, help="Wait after onroad->offroad transition before upload")
  p.add_argument("--state-file", default="", help="State file path (default: <log_root>/carrot_upload_state.json)")
  p.add_argument("--log-root", default="", help="Log root path (default from Paths.log_root())")
  return p


def main() -> None:
  args = build_argparser().parse_args()

  if args.log_root:
    log_root = Path(args.log_root)
  else:
    try:
      from openpilot.system.hardware.hw import Paths
      log_root = Path(Paths.log_root())
    except Exception:
      log_root = Path("/data/media/0/realdata")

  state_file = Path(args.state_file) if args.state_file else (log_root / "carrot_upload_state.json")
  client = CarrotUploadClient(args.server_url, args.token, args.include_rlog, state_file, log_root)

  if args.route:
    client.upload_route(args.route)
    return

  if args.once:
    uploaded = client.upload_latest_pending()
    if not uploaded:
      print("[upload] no pending routes")
    return

  try:
    from openpilot.common.params import Params
  except Exception as e:
    raise SystemExit(
      "Failed to import openpilot Params for daemon mode.\n"
      f"Import error: {e}\n"
      "Use --once/--route for one-shot mode, or run on-device openpilot environment."
    )

  params = Params()
  prev_onroad = params.get_bool("IsOnroad")
  print(f"[upload] daemon started, log_root={log_root}")

  while True:
    try:
      onroad = params.get_bool("IsOnroad")
      offroad = params.get_bool("IsOffroad")

      # Trigger upload after drive ends.
      if prev_onroad and offroad:
        print(f"[upload] drive end detected, waiting {args.settle_sec}s")
        time.sleep(args.settle_sec)
        client.upload_latest_pending()
      # Offroad background catch-up in case transition was missed.
      elif offroad:
        client.upload_latest_pending()

      prev_onroad = onroad
    except Exception as e:
      print(f"[upload] loop error: {e}")
    time.sleep(args.poll_sec)


if __name__ == "__main__":
  main()
