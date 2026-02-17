#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


def route_prefix(segment_name: str) -> str | None:
  if "--" not in segment_name:
    return None
  return segment_name.rsplit("--", 1)[0]


def segment_idx(segment_name: str) -> int:
  if "--" not in segment_name:
    return -1
  try:
    return int(segment_name.rsplit("--", 1)[1])
  except ValueError:
    return -1


class UploadClient:
  def __init__(self, server_url: str, token: str, include_rlog: bool, log_root: Path, state_file: Path):
    self.server_url = server_url.rstrip("/")
    self.token = token
    self.include_rlog = include_rlog
    self.log_root = log_root
    self.state_file = state_file
    self.session = requests.Session()
    self.state = self._load_state()

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
    self.state_file.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")

  def _uploaded(self, route: str) -> bool:
    return route in self.state.get("uploaded_routes", {})

  def _mark_uploaded(self, route: str) -> None:
    self.state.setdefault("uploaded_routes", {})[route] = {"uploaded_at": int(time.time())}
    self._save_state()

  def _all_segments(self) -> list[Path]:
    if not self.log_root.exists():
      return []
    return [p for p in self.log_root.iterdir() if p.is_dir() and "--" in p.name]

  def _route_segments(self) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    for seg in self._all_segments():
      rp = route_prefix(seg.name)
      if rp is None:
        continue
      grouped.setdefault(rp, []).append(seg)
    for route, segs in grouped.items():
      grouped[route] = sorted(segs, key=lambda s: segment_idx(s.name))
    return grouped

  def _route_closed(self, segs: list[Path]) -> bool:
    for seg in segs:
      try:
        if any(p.name.endswith(".lock") for p in seg.iterdir()):
          return False
      except OSError:
        return False
    return True

  def pending_routes(self) -> list[str]:
    grouped = self._route_segments()
    pending: list[tuple[float, str]] = []
    for route, segs in grouped.items():
      if self._uploaded(route):
        continue
      if not self._route_closed(segs):
        continue
      latest = max(s.stat().st_mtime for s in segs)
      pending.append((latest, route))
    pending.sort(reverse=True)
    return [r for _, r in pending]

  def _segment_logs(self, seg: Path) -> list[Path]:
    files: list[Path] = []
    for name in ("qlog.zst", "qlog.bz2", "qlog"):
      p = seg / name
      if p.exists():
        files.append(p)
        break
    if self.include_rlog:
      for name in ("rlog.zst", "rlog.bz2", "rlog"):
        p = seg / name
        if p.exists():
          files.append(p)
          break
    return files

  def _upload_file(self, route: str, seg_name: str, path: Path, retries: int = 4) -> None:
    url = (
      f"{self.server_url}/upload"
      f"?route={quote(route, safe='')}"
      f"&segment={quote(seg_name, safe='')}"
      f"&filename={quote(path.name, safe='')}"
    )
    size = path.stat().st_size
    delay = 2.0
    for attempt in range(1, retries + 1):
      try:
        with path.open("rb") as f:
          resp = self.session.post(url, data=f, headers=self._headers({"Content-Length": str(size)}), timeout=120)
        if resp.status_code == 200:
          return
        raise RuntimeError(f"upload status={resp.status_code}, body={resp.text[:200]}")
      except Exception as e:
        if attempt == retries:
          raise RuntimeError(f"upload failed {path}: {e}") from e
        time.sleep(delay)
        delay *= 2.0

  def _complete(self, route: str) -> None:
    resp = self.session.post(f"{self.server_url}/complete", json={"route": route}, headers=self._headers(), timeout=30)
    if resp.status_code != 200:
      raise RuntimeError(f"complete failed status={resp.status_code}, body={resp.text[:300]}")

  def upload_route(self, route: str) -> None:
    grouped = self._route_segments()
    segs = grouped.get(route, [])
    if not segs:
      raise RuntimeError(f"route not found: {route}")
    if not self._route_closed(segs):
      raise RuntimeError(f"route still recording: {route}")

    print(f"[upload] route={route} segments={len(segs)} include_rlog={self.include_rlog}")
    for seg in segs:
      for log_file in self._segment_logs(seg):
        print(f"[upload] {seg.name}/{log_file.name}")
        self._upload_file(route, seg.name, log_file)
    self._complete(route)
    self._mark_uploaded(route)
    print(f"[upload] queued analysis for route={route}")

  def upload_latest_pending(self) -> bool:
    pending = self.pending_routes()
    if not pending:
      return False
    self.upload_route(pending[0])
    return True


def _default_log_root(arg_log_root: str) -> Path:
  if arg_log_root:
    return Path(arg_log_root)
  try:
    from openpilot.system.hardware.hw import Paths
    return Path(Paths.log_root())
  except Exception:
    return Path("/data/media/0/realdata")


def build_parser() -> argparse.ArgumentParser:
  p = argparse.ArgumentParser(description="Standalone carrot uploader")
  p.add_argument("--server-url", required=True, help="e.g. https://your-server:9090")
  p.add_argument("--token", default="", help="Bearer token")
  p.add_argument("--include-rlog", action="store_true", help="Upload rlog with qlog")
  p.add_argument("--once", action="store_true", help="Upload latest pending route once")
  p.add_argument("--route", default="", help="Upload specific route once")
  p.add_argument("--log-root", default="", help="Log root (default: device log root)")
  p.add_argument("--state-file", default="", help="State file (default: <log_root>/carrot_upload_state.json)")
  p.add_argument("--poll-sec", type=float, default=10.0, help="Daemon poll interval")
  p.add_argument("--settle-sec", type=float, default=90.0, help="Wait before upload after drive end")
  return p


def main() -> None:
  args = build_parser().parse_args()
  log_root = _default_log_root(args.log_root)
  state_file = Path(args.state_file) if args.state_file else (log_root / "carrot_upload_state.json")
  client = UploadClient(args.server_url, args.token, args.include_rlog, log_root, state_file)

  if args.route:
    client.upload_route(args.route)
    return
  if args.once:
    ok = client.upload_latest_pending()
    if not ok:
      print("[upload] no pending routes")
    return

  try:
    from openpilot.common.params import Params
  except Exception as e:
    raise SystemExit(
      "daemon mode requires openpilot Params on device.\n"
      f"import error: {e}\n"
      "use --once or --route in non-device environment."
    )

  params = Params()
  prev_onroad = params.get_bool("IsOnroad")
  print(f"[upload] daemon start log_root={log_root}")
  while True:
    try:
      onroad = params.get_bool("IsOnroad")
      offroad = params.get_bool("IsOffroad")
      if prev_onroad and offroad:
        print(f"[upload] drive end detected, waiting {args.settle_sec}s")
        time.sleep(args.settle_sec)
        client.upload_latest_pending()
      elif offroad:
        client.upload_latest_pending()
      prev_onroad = onroad
    except Exception as e:
      print(f"[upload] loop error: {e}")
    time.sleep(args.poll_sec)


if __name__ == "__main__":
  main()
