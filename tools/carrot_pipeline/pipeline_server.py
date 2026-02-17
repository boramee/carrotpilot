#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import queue
import re
import threading
import traceback
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from analyze_carrot_log_standalone import analyze_logs

SAFE_COMPONENT = re.compile(r"[^a-zA-Z0-9._|:@+\-=]")


def sanitize_component(value: str) -> str:
  cleaned = SAFE_COMPONENT.sub("_", value.strip())
  if not cleaned or cleaned in (".", ".."):
    raise ValueError("invalid component")
  return cleaned


def send_json(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
  body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
  handler.send_response(code)
  handler.send_header("Content-Type", "application/json; charset=utf-8")
  handler.send_header("Content-Length", str(len(body)))
  handler.end_headers()
  handler.wfile.write(body)


@dataclass
class Config:
  bind: str
  port: int
  token: str
  storage_root: Path
  min_speed: float
  telegram_bot_token: str
  telegram_chat_id: str
  max_upload_mb: int


class Worker(threading.Thread):
  def __init__(self, cfg: Config):
    super().__init__(daemon=True)
    self.cfg = cfg
    self.jobs: queue.Queue[str] = queue.Queue()
    self.pending: set[str] = set()
    self.lock = threading.Lock()
    self.results: dict[str, dict[str, Any]] = {}

  def enqueue(self, route: str) -> int:
    with self.lock:
      if route in self.pending:
        return self.jobs.qsize()
      self.pending.add(route)
      self.jobs.put(route)
      return self.jobs.qsize()

  def get_result(self, route: str) -> dict[str, Any] | None:
    with self.lock:
      return self.results.get(route)

  def _route_logs(self, route: str) -> list[Path]:
    root = self.cfg.storage_root / "uploads" / route
    if not root.exists():
      return []
    files = [p for p in root.rglob("*") if p.is_file()]
    rlogs = [p for p in files if p.name.startswith("rlog")]
    qlogs = [p for p in files if p.name.startswith("qlog")]
    return sorted(rlogs) if rlogs else sorted(qlogs)

  def _save_result(self, route: str, result: dict[str, Any]) -> None:
    out_dir = self.cfg.storage_root / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{route}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    with self.lock:
      self.results[route] = result

  def _send_telegram(self, route: str, result: dict[str, Any]) -> None:
    if not self.cfg.telegram_bot_token or not self.cfg.telegram_chat_id:
      return
    s = result.get("suggested_params", {})
    m = result.get("metrics", {})
    samples = result.get("samples", {})
    notes = result.get("notes", [])
    note_block = "\n".join(f"- {n}" for n in notes) if notes else "- none"
    text = (
      f"[Carrot Tuning]\n"
      f"route: {route}\n"
      f"samples: total={samples.get('total')} lat={samples.get('lateral')} long={samples.get('longitudinal')}\n\n"
      f"SteerActuatorDelay={s.get('SteerActuatorDelay')} ({s.get('SteerActuatorDelay_int_x100')})\n"
      f"LongActuatorDelay={s.get('LongActuatorDelay')} ({s.get('LongActuatorDelay_int_x100')})\n"
      f"LateralTorqueAccelFactor={s.get('LateralTorqueAccelFactor')} ({s.get('LateralTorqueAccelFactor_int_x1000')})\n"
      f"LateralTorqueFriction={s.get('LateralTorqueFriction')} ({s.get('LateralTorqueFriction_int_x1000')})\n\n"
      f"lat_delay={m.get('lateral_delay_s')} long_delay={m.get('longitudinal_delay_s')}\n"
      f"saturation={m.get('lateral_saturation_ratio')} torque_limit={m.get('torque_limit_ratio')}\n"
      f"notes:\n{note_block}"
    )
    url = f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/sendMessage"
    requests.post(url, json={"chat_id": self.cfg.telegram_chat_id, "text": text}, timeout=15)

  def run(self) -> None:
    while True:
      route = self.jobs.get()
      try:
        logs = self._route_logs(route)
        if not logs:
          result = {"route": route, "error": "no uploaded logs", "notes": ["upload files first"], "source_logs": []}
        else:
          result = analyze_logs([str(p) for p in logs], min_speed=self.cfg.min_speed)
          result["route"] = route
          result["source_logs"] = [str(p) for p in logs]
        self._save_result(route, result)
        self._send_telegram(route, result)
      except Exception as e:
        self._save_result(route, {"route": route, "error": str(e), "traceback": traceback.format_exc()})
      finally:
        with self.lock:
          self.pending.discard(route)


class PipelineServer(ThreadingHTTPServer):
  def __init__(self, server_address, handler_cls, cfg: Config, worker: Worker):
    super().__init__(server_address, handler_cls)
    self.cfg = cfg
    self.worker = worker


class Handler(BaseHTTPRequestHandler):
  server: PipelineServer

  def _authorized(self) -> bool:
    token = self.server.cfg.token
    if not token:
      return True
    auth = self.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
      return auth[7:] == token
    return self.headers.get("X-API-Key", "") == token

  def do_GET(self) -> None:  # noqa: N802
    parsed = urlparse(self.path)
    if parsed.path == "/health":
      send_json(self, HTTPStatus.OK, {"ok": True, "queue": self.server.worker.jobs.qsize()})
      return

    if parsed.path == "/result":
      route = parse_qs(parsed.query).get("route", [""])[0]
      if not route:
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "route required"})
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
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "route, segment, filename required"})
        return
      try:
        route = sanitize_component(route)
        segment = sanitize_component(segment)
        filename = sanitize_component(filename)
      except ValueError:
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid path component"})
        return

      length = int(self.headers.get("Content-Length", "0"))
      if length <= 0:
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "empty body"})
        return
      max_size = self.server.cfg.max_upload_mb * 1024 * 1024
      if length > max_size:
        send_json(self, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "file too large"})
        return

      data = self.rfile.read(length)
      out_dir = self.server.cfg.storage_root / "uploads" / route / segment
      out_dir.mkdir(parents=True, exist_ok=True)
      (out_dir / filename).write_bytes(data)
      send_json(self, HTTPStatus.OK, {"ok": True, "route": route, "segment": segment, "filename": filename, "bytes": len(data)})
      return

    if parsed.path == "/complete":
      route = params.get("route", [""])[0]
      if not route:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
          route = str(json.loads(body.decode("utf-8")).get("route", ""))
        except Exception:
          route = ""
      if not route:
        send_json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "route required"})
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

  def log_message(self, fmt: str, *args) -> None:  # noqa: A003
    print(f"[pipeline] {self.address_string()} - {fmt % args}")


def build_parser() -> argparse.ArgumentParser:
  p = argparse.ArgumentParser(description="Standalone carrot upload/analyze server")
  p.add_argument("--bind", default="0.0.0.0")
  p.add_argument("--port", type=int, default=9090)
  p.add_argument("--token", default="", help="Bearer token")
  p.add_argument("--storage-root", default="/tmp/carrot_pipeline")
  p.add_argument("--min-speed", type=float, default=8.0)
  p.add_argument("--telegram-bot-token", default="")
  p.add_argument("--telegram-chat-id", default="")
  p.add_argument("--max-upload-mb", type=int, default=512)
  return p


def main() -> None:
  args = build_parser().parse_args()
  cfg = Config(
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

  worker = Worker(cfg)
  worker.start()

  server = PipelineServer((cfg.bind, cfg.port), Handler, cfg, worker)
  print(f"[pipeline] listen {cfg.bind}:{cfg.port}")
  print(f"[pipeline] storage {cfg.storage_root}")
  print(f"[pipeline] auth {'on' if cfg.token else 'off'}")
  server.serve_forever()


if __name__ == "__main__":
  main()
