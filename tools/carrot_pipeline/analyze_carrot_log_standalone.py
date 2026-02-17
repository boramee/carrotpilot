#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bz2
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from cereal import log as capnp_log


def _read_events(path: Path) -> list[Any]:
  if not path.exists():
    raise FileNotFoundError(f"log file not found: {path}")

  raw = path.read_bytes()
  if path.name.endswith(".bz2") or raw.startswith(b"BZh"):
    raw = bz2.decompress(raw)
  elif path.name.endswith(".zst") or raw.startswith(b"\x28\xB5\x2F\xFD"):
    import zstandard as zstd
    raw = zstd.ZstdDecompressor().decompress(raw)

  return list(capnp_log.Event.read_multiple_bytes(raw))


def _load_sorted_events(paths: list[str]) -> list[Any]:
  events: list[Any] = []
  for p in paths:
    events.extend(_read_events(Path(p)))
  events.sort(key=lambda e: e.logMonoTime)
  return events


def _clamp(v: float, lo: float, hi: float) -> float:
  return max(lo, min(hi, v))


def _estimate_delay(x: np.ndarray, y: np.ndarray, dt: float, max_delay: float) -> tuple[float | None, float | None]:
  if x.size < 50 or y.size < 50:
    return None, None

  x0 = x - np.mean(x)
  y0 = y - np.mean(y)
  if float(np.std(x0)) < 1e-6 or float(np.std(y0)) < 1e-6:
    return None, None

  max_lag = int(round(max_delay / dt))
  best_corr = -2.0
  best_lag = 0
  for lag in range(0, max_lag + 1):
    if lag == 0:
      xx, yy = x0, y0
    else:
      xx, yy = x0[:-lag], y0[lag:]
    if xx.size < 20:
      continue
    corr = float(np.corrcoef(xx, yy)[0, 1])
    if np.isfinite(corr) and corr > best_corr:
      best_corr = corr
      best_lag = lag

  if best_corr <= -1.5:
    return None, None
  return best_lag * dt, best_corr


def analyze_logs(log_files: list[str], min_speed: float = 8.0) -> dict[str, Any]:
  events = _load_sorted_events(log_files)

  last_car_state = None
  last_controls = None
  last_car_output = None
  cp_defaults = {
    "steerActuatorDelay": None,
    "longitudinalActuatorDelay": None,
    "latAccelFactor": None,
    "friction": None,
  }

  rows: list[dict[str, Any]] = []

  for msg in events:
    which = msg.which()
    if which == "carState":
      last_car_state = msg.carState
    elif which == "controlsState":
      last_controls = msg.controlsState
    elif which == "carOutput":
      last_car_output = msg.carOutput
    elif which == "carParams":
      cp = msg.carParams
      cp_defaults["steerActuatorDelay"] = float(cp.steerActuatorDelay)
      cp_defaults["longitudinalActuatorDelay"] = float(cp.longitudinalActuatorDelay)
      lt = cp.lateralTuning
      if lt.which() == "torque":
        cp_defaults["latAccelFactor"] = float(lt.torque.latAccelFactor)
        cp_defaults["friction"] = float(lt.torque.friction)
    elif which == "carControl":
      if last_car_state is None or last_controls is None:
        continue

      cs = last_car_state
      cst = last_controls
      cc = msg.carControl

      lat_union = cst.lateralControlState
      lat_name = lat_union.which()
      lat_state = getattr(lat_union, lat_name)
      saturated = bool(getattr(lat_state, "saturated", False))

      if lat_name == "torqueState":
        desired_lat_accel = float(lat_state.desiredLateralAccel)
        actual_lat_accel = float(lat_state.actualLateralAccel)
      else:
        desired_lat_accel = float(cst.desiredCurvature) * float(cs.vEgo) ** 2
        actual_lat_accel = float(cst.curvature) * float(cs.vEgo) ** 2

      out_torque = float(last_car_output.actuatorsOutput.torque) if last_car_output is not None else float(cc.actuators.torque)

      rows.append({
        "t": msg.logMonoTime * 1e-9,
        "vEgo": float(cs.vEgo),
        "aEgo": float(cs.aEgo),
        "standstill": bool(cs.standstill),
        "steeringPressed": bool(cs.steeringPressed),
        "brakePressed": bool(cs.brakePressed),
        "enabled": bool(cc.enabled),
        "latActive": bool(cc.latActive),
        "longActive": bool(cc.longActive),
        "cmdTorque": float(cc.actuators.torque),
        "outTorque": out_torque,
        "cmdAccel": float(cc.actuators.accel),
        "desiredCurvature": float(cst.desiredCurvature),
        "actualCurvature": float(cst.curvature),
        "saturated": saturated,
        "desiredLatAccel": desired_lat_accel,
        "actualLatAccel": actual_lat_accel,
      })

  if len(rows) < 50:
    return {
      "samples": {"total": len(rows), "lateral": 0, "longitudinal": 0},
      "timing": {"duration_s": 0.0, "dt_s": 0.0},
      "metrics": {},
      "suggested_params": {
        "SteerActuatorDelay": cp_defaults["steerActuatorDelay"],
        "LongActuatorDelay": cp_defaults["longitudinalActuatorDelay"],
        "LateralTorqueAccelFactor": cp_defaults["latAccelFactor"],
        "LateralTorqueFriction": cp_defaults["friction"],
      },
      "notes": ["not enough valid samples"],
    }

  t = np.array([r["t"] for r in rows], dtype=float)
  dt = float(np.median(np.diff(t)))
  if not np.isfinite(dt) or dt <= 0.0:
    dt = 0.01
  duration = float(t[-1] - t[0])

  v_ego = np.array([r["vEgo"] for r in rows], dtype=float)
  a_ego = np.array([r["aEgo"] for r in rows], dtype=float)
  standstill = np.array([r["standstill"] for r in rows], dtype=bool)
  steering_pressed = np.array([r["steeringPressed"] for r in rows], dtype=bool)
  brake_pressed = np.array([r["brakePressed"] for r in rows], dtype=bool)
  enabled = np.array([r["enabled"] for r in rows], dtype=bool)
  lat_active = np.array([r["latActive"] for r in rows], dtype=bool)
  long_active = np.array([r["longActive"] for r in rows], dtype=bool)
  saturated = np.array([r["saturated"] for r in rows], dtype=bool)
  cmd_torque = np.array([r["cmdTorque"] for r in rows], dtype=float)
  out_torque = np.array([r["outTorque"] for r in rows], dtype=float)
  cmd_accel = np.array([r["cmdAccel"] for r in rows], dtype=float)
  desired_curvature = np.array([r["desiredCurvature"] for r in rows], dtype=float)
  actual_curvature = np.array([r["actualCurvature"] for r in rows], dtype=float)
  desired_lat_accel = np.array([r["desiredLatAccel"] for r in rows], dtype=float)
  actual_lat_accel = np.array([r["actualLatAccel"] for r in rows], dtype=float)

  base_mask = enabled & (~standstill) & (v_ego >= min_speed)
  lat_mask = base_mask & lat_active & (~steering_pressed)
  long_mask = base_mask & long_active & (~brake_pressed)
  lat_count = int(np.count_nonzero(lat_mask))
  long_count = int(np.count_nonzero(long_mask))

  lat_delay, lat_corr = (None, None)
  if lat_count >= 100:
    lat_delay, lat_corr = _estimate_delay(desired_curvature[lat_mask], actual_curvature[lat_mask], dt, 0.6)
  long_delay, long_corr = (None, None)
  if long_count >= 100:
    long_delay, long_corr = _estimate_delay(cmd_accel[long_mask], a_ego[long_mask], dt, 0.8)

  lat_err = desired_curvature[lat_mask] - actual_curvature[lat_mask] if lat_count > 0 else np.array([], dtype=float)
  lat_rms_error = float(math.sqrt(float(np.mean(lat_err ** 2)))) if lat_err.size > 0 else None

  lateral_osc_hz = None
  if lat_err.size > 10:
    zc = int(np.count_nonzero(np.diff(np.signbit(lat_err))))
    lateral_osc_hz = float(zc / max(1e-3, (t[lat_mask][-1] - t[lat_mask][0])))

  sat_ratio = float(np.mean(saturated[lat_mask])) if lat_count > 0 else None
  torque_limit_ratio = float(np.mean(np.abs(cmd_torque[lat_mask] - out_torque[lat_mask]) > 0.03)) if lat_count > 0 else None

  torque_ratio = None
  ratio_mask = lat_mask & (np.abs(desired_lat_accel) > 0.5) & np.isfinite(desired_lat_accel) & np.isfinite(actual_lat_accel)
  if np.count_nonzero(ratio_mask) >= 50:
    ratio = np.abs(actual_lat_accel[ratio_mask]) / np.maximum(1e-3, np.abs(desired_lat_accel[ratio_mask]))
    torque_ratio = float(np.median(ratio))

  steer_now = cp_defaults["steerActuatorDelay"]
  long_now = cp_defaults["longitudinalActuatorDelay"]
  lat_factor_now = cp_defaults["latAccelFactor"]
  friction_now = cp_defaults["friction"]

  steer_suggest = steer_now if steer_now is not None else 0.14
  if lat_delay is not None:
    steer_suggest = _clamp(lat_delay + (0.01 if (lateral_osc_hz or 0.0) > 1.5 else 0.0), 0.08, 0.28)

  long_suggest = long_now if long_now is not None else 0.25
  if long_delay is not None:
    long_suggest = _clamp(long_delay, 0.15, 0.60)

  lat_factor_suggest = lat_factor_now if lat_factor_now is not None else 2.5
  if lat_factor_now is not None and torque_ratio is not None:
    tr = _clamp(torque_ratio, 0.85, 1.15)
    lat_factor_suggest = _clamp(lat_factor_now * tr, lat_factor_now * 0.85, lat_factor_now * 1.15)

  friction_suggest = friction_now if friction_now is not None else 0.10
  if friction_now is not None and sat_ratio is not None:
    if sat_ratio > 0.30:
      friction_suggest = _clamp(friction_now * 1.03, 0.03, 0.30)
    elif (lateral_osc_hz or 0.0) > 1.5:
      friction_suggest = _clamp(friction_now * 0.95, 0.03, 0.30)

  notes: list[str] = []
  if lat_count < 100:
    notes.append("low lateral active sample count")
  if long_count < 100:
    notes.append("low longitudinal active sample count")
  if lat_corr is not None and lat_corr < 0.2:
    notes.append("low lateral correlation confidence")
  if long_corr is not None and long_corr < 0.2:
    notes.append("low longitudinal correlation confidence")
  if sat_ratio is not None and sat_ratio > 0.30:
    notes.append("high steering saturation ratio")

  return {
    "samples": {"total": len(rows), "lateral": lat_count, "longitudinal": long_count},
    "timing": {"duration_s": duration, "dt_s": dt},
    "metrics": {
      "lateral_delay_s": lat_delay,
      "lateral_delay_corr": lat_corr,
      "longitudinal_delay_s": long_delay,
      "longitudinal_delay_corr": long_corr,
      "lateral_rms_error": lat_rms_error,
      "lateral_oscillation_hz": lateral_osc_hz,
      "lateral_saturation_ratio": sat_ratio,
      "torque_limit_ratio": torque_limit_ratio,
      "torque_ratio_median": torque_ratio,
    },
    "suggested_params": {
      "SteerActuatorDelay": steer_suggest,
      "SteerActuatorDelay_int_x100": int(round(steer_suggest * 100)),
      "LongActuatorDelay": long_suggest,
      "LongActuatorDelay_int_x100": int(round(long_suggest * 100)),
      "LateralTorqueAccelFactor": lat_factor_suggest,
      "LateralTorqueAccelFactor_int_x1000": int(round(lat_factor_suggest * 1000)),
      "LateralTorqueFriction": friction_suggest,
      "LateralTorqueFriction_int_x1000": int(round(friction_suggest * 1000)),
    },
    "notes": notes,
  }


def _print_summary(result: dict[str, Any]) -> None:
  print("=== Carrot Standalone Analysis ===")
  print(f"samples={result['samples']}")
  print(f"timing={result['timing']}")
  print(f"metrics={result['metrics']}")
  print("")
  print("suggested_params:")
  for k, v in result["suggested_params"].items():
    print(f"  {k}: {v}")
  if result.get("notes"):
    print("notes:")
    for n in result["notes"]:
      print(f"  - {n}")


def main() -> None:
  p = argparse.ArgumentParser(description="Standalone carrot log analyzer")
  p.add_argument("log_files", nargs="+", help="Local qlog/rlog files")
  p.add_argument("--min-speed", type=float, default=8.0, help="Minimum speed for analysis (m/s)")
  p.add_argument("--json-out", default="", help="Output JSON path")
  args = p.parse_args()

  result = analyze_logs(args.log_files, min_speed=args.min_speed)
  _print_summary(result)
  if args.json_out:
    Path(args.json_out).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved: {args.json_out}")


if __name__ == "__main__":
  main()
