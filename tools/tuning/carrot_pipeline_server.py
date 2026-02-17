#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import queue
import re
import threading
import time
import traceback
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from tools.tuning.analyze_carrot_log import analyze

SAFE_COMPONENT = re.compile(r"[^a-zA-Z0-9._|:@+\-=]")


def sanitize_component(value: str) -> str:
  cleaned = SAFE_COMPONENT.sub("_", value.strip())
  if not cleaned or cleaned in (".", ".."):
    raise ValueError("invalid path component")
  return cleaned


def send_json(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
  body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
  handler.send_response(code)
  handler.send_header("Content-Type", "application/json; charset=utf-8")
  handler.send_header("Content-Length", str(len(body)))
  handler.end_headers()
  handler.wfile.write(body)


@dataclass
class ServerConfig:
  bind: str
  port: int
  token: str
  storage_root: Path
  min_speed: float
  telegram_bot_token: str
  telegram_chat_id: str
  max_upload_mb: int


class AnalysisWorker(threading.Thread):
  def __init__(self, config: ServerConfig):
    super().__init__(daemon=True)
    self.config = config
    self.jobs: queue.Queue[str] = queue.Queue()
    self.pending: set[str] = set()
    self.lock = threading.Lock()
    self.last_results: dict[str, dict[str, Any]] = {}

  def enqueue(self, route: str) -> int:
    with self.lock:
      if route in self.pending:
        return self.jobs.qsize()
      self.pending.add(route)
      self.jobs.put(route)
      return self.jobs.qsize()

  def get_result(self, route: str) -> dict[str, Any] | None:
    with self.lock:
      return self.last_results.get(route)

  def _collect_route_logs(self, route: str) -> list[Path]:
    route_dir = self.config.storage_root / "uploads" / route
    if not route_dir.exists():
      return []

    files = [p for p in route_dir.rglob("*") if p.is_file()]
    rlogs = [p for p in files if p.name.startswith("rlog")]
    qlogs = [p for p in files if p.name.startswith("qlog")]

    # Prefer full-fidelity rlogs, fallback to qlogs.
    selected = sorted(rlogs) if rlogs else sorted(qlogs)
    return selected

  def _save_result(self, route: str, result: dict[str, Any]) -> None:
    results_dir = self.config.storage_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / f"{route}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    with self.lock:
      self.last_results[route] = result

  def _send_telegram(self, route: str, result: dict[str, Any]) -> None:
    if not self.config.telegram_bot_token or not self.config.telegram_chat_id:
      return

    s = result.get("suggested_params", {})
    m = result.get("metrics", {})
    samples = result.get("samples", {})
    notes = result.get("notes", [])
    note_block = "\n".join(f"- {n}" for n in notes) if notes else "- none"
    error = result.get("error", "")

    msg = (
      f"[Carrot GV70 Tuning]\n"
      f"route: {route}\n"
      f"samples: total={samples.get('total')}, lat={samples.get('lateral')}, long={samples.get('longitudinal')}\n\n"
      f"SteerActuatorDelay: {s.get('SteerActuatorDelay')} (x100={s.get('SteerActuatorDelay_int_x100')})\n"
      f"LongActuatorDelay: {s.get('LongActuatorDelay')} (x100={s.get('LongActuatorDelay_int_x100')})\n"
      f"LateralTorqueAccelFactor: {s.get('LateralTorqueAccelFactor')} (x1000={s.get('LateralTorqueAccelFactor_int_x1000')})\n"
      f"LateralTorqueFriction: {s.get('LateralTorqueFriction')} (x1000={s.get('LateralTorqueFriction_int_x1000')})\n\n"
      f"lateral_delay_s={m.get('lateral_delay_s')}, long_delay_s={m.get('longitudinal_delay_s')}\n"
      f"saturation={m.get('lateral_saturation_ratio')}, torque_limit={m.get('torque_limit_ratio')}\n"
      f"error={error}\n\n"
      f"notes:\n{note_block}"
    )

    url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
    payload = {"chat_id": self.config.telegram_chat_id, "text": msg}
    requests.post(url, json=payload, timeout=15)

  def run(self) -> None:
    while True:
      route = self.jobs.get()
      try:
        logs = self._collect_route_logs(route)
        if not logs:
          result = {
            "route": route,
            "error": "No uploaded logs found for route",
            "suggested_params": {},
            "metrics": {},
            "samples": {},
            "notes": ["Upload qlog/rlog files first, then call /complete."],
          }
        else:
          analysis = analyze([str(p) for p in logs], min_speed=self.config.min_speed)
          result = analysis.to_dict()
          result["route"] = route
          result["source_logs"] = [str(p) for p in logs]

        self._save_result(route, result)
        self._send_telegram(route, result)
      except Exception as e:
        err = {
          "route": route,
          "error": str(e),
          "traceback": traceback.format_exc(),
        }
        self._save_result(route, err)
      finally:
        with self.lock:
          self.pending.discard(route)


class CarrotPipelineHTTPServer(ThreadingHTTPServer):
  def __init__(self, server_address, handler_cls, config: ServerConfig, worker: AnalysisWorker):
    super().__init__(server_address, handler_cls)
    self.config = config
    self.worker = worker


class Handler(BaseHTTPRequestHandler):
  server: CarrotPipelineHTTPServer

  def _authorized(self) -> bool:
    token = self.server.config.token
    if not token:
      return True

    auth = self.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
      return auth[7:] == token
    return self.headers.get("X-API-Key", "") == token

  def _read_json_body(self) -> dict[str, Any]:
    length = int(self.headers.get("Content-Length", "0"))
    raw = self.rfile.read(length) if length > 0 else b"{}"
    if not raw:
      return {}
    return json.loads(raw.decode("utf-8"))

  def do_GET(self) -> None:  # noqa: N802
    parsed = urlparse(self.path)
    if parsed.path == "/health":
      send_json(self, HTTPStatus.OK, {"ok": True, "time": time.time(), "queue": self.server.worker.jobs.qsize()})
      return

    if parsed.path == "/result":
      route = parse_qs(parsed.query).get("route", [""])[0]
      if not route:
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "route is required"})
        return
      try:
        route = sanitize_component(route)
      except ValueError:
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid route"})
        return
      result = self.server.worker.get_result(route)
      if result is None:
        send_json(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "result not found"})
        return
      send_json(self, HTTPStatus.OK, {"ok": True, "result": result})
      return

    send_json(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

  def do_POST(self) -> None:  # noqa: N802
    if not self._authorized():
      send_json(self, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
      return

    parsed = urlparse(self.path)
    params = parse_qs(parsed.query)

    if parsed.path == "/upload":
      route = params.get("route", [""])[0]
      segment = params.get("segment", [""])[0]
      filename = params.get("filename", [""])[0]
      if not route or not segment or not filename:
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "route, segment, filename are required"})
        return

      try:
        route = sanitize_component(route)
        segment = sanitize_component(segment)
        filename = sanitize_component(filename)
      except ValueError:
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid path components"})
        return

      length = int(self.headers.get("Content-Length", "0"))
      if length <= 0:
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "empty body"})
        return

      max_size = self.server.config.max_upload_mb * 1024 * 1024
      if length > max_size:
        send_json(self, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": f"file exceeds {self.server.config.max_upload_mb}MB"})
        return

      data = self.rfile.read(length)
      out = self.server.config.storage_root / "uploads" / route / segment
      out.mkdir(parents=True, exist_ok=True)
      file_path = out / filename
      file_path.write_bytes(data)

      send_json(self, HTTPStatus.OK, {"ok": True, "route": route, "segment": segment, "filename": filename, "bytes": len(data)})
      return

    if parsed.path == "/complete":
      route = params.get("route", [""])[0]
      if not route:
        body = self._read_json_body()
        route = str(body.get("route", ""))
      if not route:
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "route is required"})
        return
      try:
        route = sanitize_component(route)
      except ValueError:
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid route"})
        return

      qsize = self.server.worker.enqueue(route)
      send_json(self, HTTPStatus.OK, {"ok": True, "queued": route, "queue_size": qsize})
      return

    send_json(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

  def log_message(self, format: str, *args) -> None:  # noqa: A003
    # Keep logs concise for daemon-like use.
    print(f"[server] {self.address_string()} - {format % args}")


def build_argparser() -> argparse.ArgumentParser:
  p = argparse.ArgumentParser(description="Carrot log upload + analysis + telegram pipeline server")
  p.add_argument("--bind", default="0.0.0.0", help="Bind address")
  p.add_argument("--port", type=int, default=9090, help="Bind port")
  p.add_argument("--token", default="", help="Upload API token (Bearer/X-API-Key)")
  p.add_argument("--storage-root", default="/tmp/carrot_pipeline", help="Storage root for uploads/results")
  p.add_argument("--min-speed", type=float, default=8.0, help="Minimum analysis speed in m/s")
  p.add_argument("--telegram-bot-token", default="", help="Telegram bot token")
  p.add_argument("--telegram-chat-id", default="", help="Telegram chat id")
  p.add_argument("--max-upload-mb", type=int, default=512, help="Per-file upload size limit in MB")
  return p


def main() -> None:
  args = build_argparser().parse_args()
  cfg = ServerConfig(
    bind=args.bind,
    port=args.port,
    token=args.token,
    storage_root=Path(args.storage_root),
    min_speed=args.min_speed,
    telegram_bot_token=args.telegram_bot_token,
    telegram_chat_id=args.telegram_chat_id,
    max_upload_mb=args.max_upload_mb,
  )
  cfg.storage_root.mkdir(parents=True, exist_ok=True)

  worker = AnalysisWorker(cfg)
  worker.start()

  server = CarrotPipelineHTTPServer((cfg.bind, cfg.port), Handler, cfg, worker)
  print(f"[server] listening on {cfg.bind}:{cfg.port}")
  print(f"[server] storage: {cfg.storage_root}")
  if cfg.token:
    print("[server] auth: enabled")
  else:
    print("[server] auth: disabled (not recommended for public network)")
  server.serve_forever()


if __name__ == "__main__":
  main()
