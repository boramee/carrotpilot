#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

IMPORT_ERROR: str | None = None
try:
  from openpilot.tools.lib.logreader import LogReader
except ModuleNotFoundError as e:
  LogReader = None
  IMPORT_ERROR = str(e)


@dataclass
class AnalysisResult:
  samples_total: int
  samples_lat: int
  samples_long: int
  duration_s: float
  dt_s: float
  lateral_delay_s: float | None
  lateral_delay_corr: float | None
  longitudinal_delay_s: float | None
  longitudinal_delay_corr: float | None
  lateral_rms_error: float | None
  lateral_oscillation_hz: float | None
  lateral_saturation_ratio: float | None
  torque_limit_ratio: float | None
  torque_ratio_median: float | None
  suggested_steer_actuator_delay: float | None
  suggested_long_actuator_delay: float | None
  suggested_lateral_torque_accel_factor: float | None
  suggested_lateral_torque_friction: float | None
  notes: list[str]

  def to_dict(self) -> dict[str, Any]:
    return {
      "samples": {
        "total": self.samples_total,
        "lateral": self.samples_lat,
        "longitudinal": self.samples_long,
      },
      "timing": {
        "duration_s": self.duration_s,
        "dt_s": self.dt_s,
      },
      "metrics": {
        "lateral_delay_s": self.lateral_delay_s,
        "lateral_delay_corr": self.lateral_delay_corr,
        "longitudinal_delay_s": self.longitudinal_delay_s,
        "longitudinal_delay_corr": self.longitudinal_delay_corr,
        "lateral_rms_error": self.lateral_rms_error,
        "lateral_oscillation_hz": self.lateral_oscillation_hz,
        "lateral_saturation_ratio": self.lateral_saturation_ratio,
        "torque_limit_ratio": self.torque_limit_ratio,
        "torque_ratio_median": self.torque_ratio_median,
      },
      "suggested_params": {
        "SteerActuatorDelay": self.suggested_steer_actuator_delay,
        "SteerActuatorDelay_int_x100": None if self.suggested_steer_actuator_delay is None else int(round(self.suggested_steer_actuator_delay * 100)),
        "LongActuatorDelay": self.suggested_long_actuator_delay,
        "LongActuatorDelay_int_x100": None if self.suggested_long_actuator_delay is None else int(round(self.suggested_long_actuator_delay * 100)),
        "LateralTorqueAccelFactor": self.suggested_lateral_torque_accel_factor,
        "LateralTorqueAccelFactor_int_x1000": None if self.suggested_lateral_torque_accel_factor is None else int(round(self.suggested_lateral_torque_accel_factor * 1000)),
        "LateralTorqueFriction": self.suggested_lateral_torque_friction,
        "LateralTorqueFriction_int_x1000": None if self.suggested_lateral_torque_friction is None else int(round(self.suggested_lateral_torque_friction * 1000)),
      },
      "notes": self.notes,
    }


def safe_median(x: np.ndarray) -> float:
  return float(np.median(x)) if x.size > 0 else float("nan")


def clamp(v: float, lo: float, hi: float) -> float:
  return max(lo, min(hi, v))


def estimate_delay_seconds(x: np.ndarray, y: np.ndarray, dt: float, max_delay: float) -> tuple[float | None, float | None]:
  if x.size < 50 or y.size < 50:
    return None, None
  if not np.isfinite(x).all() or not np.isfinite(y).all():
    return None, None

  x0 = x - np.mean(x)
  y0 = y - np.mean(y)
  x_std = np.std(x0)
  y_std = np.std(y0)
  if x_std < 1e-6 or y_std < 1e-6:
    return None, None

  max_lag = int(round(max_delay / dt))
  best_corr = -2.0
  best_lag = 0

  for lag in range(0, max_lag + 1):
    if lag == 0:
      xx = x0
      yy = y0
    else:
      xx = x0[:-lag]
      yy = y0[lag:]
    if xx.size < 20:
      continue
    corr = float(np.corrcoef(xx, yy)[0, 1])
    if np.isfinite(corr) and corr > best_corr:
      best_corr = corr
      best_lag = lag

  if best_corr <= -1.5:
    return None, None
  return best_lag * dt, best_corr


def get_lateral_state(controls_state) -> tuple[str, Any]:
  lcs = controls_state.lateralControlState
  state_name = lcs.which()
  return state_name, getattr(lcs, state_name)


def analyze(identifier: str, min_speed: float) -> AnalysisResult:
  lr = LogReader(identifier, sort_by_time=True)

  last_car_state = None
  last_controls_state = None
  last_car_output = None
  current_params = {
    "steerActuatorDelay": None,
    "longitudinalActuatorDelay": None,
    "latAccelFactor": None,
    "friction": None,
  }

  rows: list[dict[str, float | bool]] = []

  for msg in lr:
    which = msg.which()
    if which == "carState":
      last_car_state = msg.carState
    elif which == "controlsState":
      last_controls_state = msg.controlsState
    elif which == "carOutput":
      last_car_output = msg.carOutput
    elif which == "carParams":
      cp = msg.carParams
      current_params["steerActuatorDelay"] = float(cp.steerActuatorDelay)
      current_params["longitudinalActuatorDelay"] = float(cp.longitudinalActuatorDelay)
      lt = cp.lateralTuning
      if lt.which() == "torque":
        current_params["latAccelFactor"] = float(lt.torque.latAccelFactor)
        current_params["friction"] = float(lt.torque.friction)
    elif which == "carControl":
      if last_car_state is None or last_controls_state is None:
        continue

      t = msg.logMonoTime * 1e-9
      cs = last_car_state
      cst = last_controls_state
      cc = msg.carControl

      lat_state_name, lat_state = get_lateral_state(cst)
      saturated = bool(getattr(lat_state, "saturated", False))

      desired_lat_accel = float("nan")
      actual_lat_accel = float("nan")
      if lat_state_name == "torqueState":
        desired_lat_accel = float(lat_state.desiredLateralAccel)
        actual_lat_accel = float(lat_state.actualLateralAccel)
      else:
        desired_curvature = float(cst.desiredCurvature)
        actual_curvature = float(cst.curvature)
        desired_lat_accel = desired_curvature * float(cs.vEgo) ** 2
        actual_lat_accel = actual_curvature * float(cs.vEgo) ** 2

      cmd_torque = float(cc.actuators.torque)
      out_torque = float(last_car_output.actuatorsOutput.torque) if last_car_output is not None else cmd_torque

      rows.append({
        "t": t,
        "vEgo": float(cs.vEgo),
        "aEgo": float(cs.aEgo),
        "standstill": bool(cs.standstill),
        "steeringPressed": bool(cs.steeringPressed),
        "brakePressed": bool(cs.brakePressed),
        "enabled": bool(cc.enabled),
        "latActive": bool(cc.latActive),
        "longActive": bool(cc.longActive),
        "cmdTorque": cmd_torque,
        "outTorque": out_torque,
        "cmdAccel": float(cc.actuators.accel),
        "desiredCurvature": float(cst.desiredCurvature),
        "actualCurvature": float(cst.curvature),
        "saturated": saturated,
        "desiredLatAccel": desired_lat_accel,
        "actualLatAccel": actual_lat_accel,
      })

  if len(rows) < 50:
    return AnalysisResult(
      samples_total=len(rows),
      samples_lat=0,
      samples_long=0,
      duration_s=0.0,
      dt_s=0.0,
      lateral_delay_s=None,
      lateral_delay_corr=None,
      longitudinal_delay_s=None,
      longitudinal_delay_corr=None,
      lateral_rms_error=None,
      lateral_oscillation_hz=None,
      lateral_saturation_ratio=None,
      torque_limit_ratio=None,
      torque_ratio_median=None,
      suggested_steer_actuator_delay=current_params["steerActuatorDelay"],
      suggested_long_actuator_delay=current_params["longitudinalActuatorDelay"],
      suggested_lateral_torque_accel_factor=current_params["latAccelFactor"],
      suggested_lateral_torque_friction=current_params["friction"],
      notes=["Not enough valid samples to build recommendations."],
    )

  t = np.array([r["t"] for r in rows], dtype=float)
  dt = float(np.median(np.diff(t)))
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

  notes: list[str] = []
  samples_lat = int(np.count_nonzero(lat_mask))
  samples_long = int(np.count_nonzero(long_mask))

  lat_delay, lat_corr = None, None
  if samples_lat >= 100:
    lat_delay, lat_corr = estimate_delay_seconds(desired_curvature[lat_mask], actual_curvature[lat_mask], dt, max_delay=0.6)
  else:
    notes.append("Not enough active lateral samples; steering delay confidence is low.")

  long_delay, long_corr = None, None
  if samples_long >= 100:
    long_delay, long_corr = estimate_delay_seconds(cmd_accel[long_mask], a_ego[long_mask], dt, max_delay=0.8)
  else:
    notes.append("Not enough active longitudinal samples; long delay confidence is low.")

  lat_err = desired_curvature[lat_mask] - actual_curvature[lat_mask] if samples_lat > 0 else np.array([], dtype=float)
  lat_rms = float(math.sqrt(float(np.mean(lat_err ** 2)))) if lat_err.size > 0 else None

  osc_hz = None
  if lat_err.size > 10 and duration > 1.0:
    zc = int(np.count_nonzero(np.diff(np.signbit(lat_err))))
    osc_hz = float(zc / max(1e-3, (t[lat_mask][-1] - t[lat_mask][0])))

  sat_ratio = float(np.mean(saturated[lat_mask])) if samples_lat > 0 else None
  torque_limit_ratio = None
  if samples_lat > 0:
    torque_limit_ratio = float(np.mean(np.abs(cmd_torque[lat_mask] - out_torque[lat_mask]) > 0.03))

  torque_ratio = None
  torque_mask = lat_mask & (np.abs(desired_lat_accel) > 0.5) & np.isfinite(desired_lat_accel) & np.isfinite(actual_lat_accel)
  if np.count_nonzero(torque_mask) >= 50:
    ratio_arr = np.abs(actual_lat_accel[torque_mask]) / np.maximum(1e-3, np.abs(desired_lat_accel[torque_mask]))
    torque_ratio = safe_median(ratio_arr)

  steer_delay_now = current_params["steerActuatorDelay"]
  long_delay_now = current_params["longitudinalActuatorDelay"]
  lat_factor_now = current_params["latAccelFactor"]
  friction_now = current_params["friction"]

  suggest_steer = steer_delay_now
  if lat_delay is not None:
    suggest_steer = clamp(lat_delay, 0.08, 0.25)
    if osc_hz is not None and osc_hz > 1.5:
      suggest_steer = clamp(suggest_steer + 0.01, 0.08, 0.28)
  elif steer_delay_now is None:
    suggest_steer = 0.14

  suggest_long = long_delay_now
  if long_delay is not None:
    suggest_long = clamp(long_delay, 0.15, 0.60)
  elif long_delay_now is None:
    suggest_long = 0.25

  suggest_lat_factor = lat_factor_now
  if lat_factor_now is not None and torque_ratio is not None and np.isfinite(torque_ratio):
    torque_ratio = clamp(torque_ratio, 0.85, 1.15)
    suggest_lat_factor = clamp(lat_factor_now * torque_ratio, lat_factor_now * 0.85, lat_factor_now * 1.15)
  elif lat_factor_now is None:
    suggest_lat_factor = 2.5

  suggest_friction = friction_now
  if friction_now is not None and osc_hz is not None and sat_ratio is not None:
    if osc_hz > 1.5 and sat_ratio < 0.2:
      suggest_friction = clamp(friction_now * 0.95, 0.03, 0.3)
    elif sat_ratio > 0.3:
      suggest_friction = clamp(friction_now * 1.03, 0.03, 0.3)
  elif friction_now is None:
    suggest_friction = 0.10

  if lat_corr is not None and lat_corr < 0.2:
    notes.append("Low lateral correlation. Log may include poor lane quality or rough surfaces.")
  if long_corr is not None and long_corr < 0.2:
    notes.append("Low longitudinal correlation. Log may include heavy lead-vehicle interactions.")
  if sat_ratio is not None and sat_ratio > 0.30:
    notes.append("High steering saturation ratio. Vehicle steering limits are frequently hit.")
  if torque_limit_ratio is not None and torque_limit_ratio > 0.20:
    notes.append("Torque output is frequently rate-limited by CarController/panda limits.")

  return AnalysisResult(
    samples_total=len(rows),
    samples_lat=samples_lat,
    samples_long=samples_long,
    duration_s=duration,
    dt_s=dt,
    lateral_delay_s=lat_delay,
    lateral_delay_corr=lat_corr,
    longitudinal_delay_s=long_delay,
    longitudinal_delay_corr=long_corr,
    lateral_rms_error=lat_rms,
    lateral_oscillation_hz=osc_hz,
    lateral_saturation_ratio=sat_ratio,
    torque_limit_ratio=torque_limit_ratio,
    torque_ratio_median=torque_ratio,
    suggested_steer_actuator_delay=suggest_steer,
    suggested_long_actuator_delay=suggest_long,
    suggested_lateral_torque_accel_factor=suggest_lat_factor,
    suggested_lateral_torque_friction=suggest_friction,
    notes=notes,
  )


def print_human(result: AnalysisResult):
  out = result.to_dict()
  sp = out["suggested_params"]
  m = out["metrics"]
  s = out["samples"]
  tm = out["timing"]

  print("=== Carrot Pilot GV70 Log Analysis ===")
  print(f"- Samples: total={s['total']}, lat={s['lateral']}, long={s['longitudinal']}")
  print(f"- Window: {tm['duration_s']:.1f}s, dt~{tm['dt_s']:.4f}s")
  print("")
  print("[Metrics]")
  print(f"- lateral_delay_s: {m['lateral_delay_s']}")
  print(f"- lateral_delay_corr: {m['lateral_delay_corr']}")
  print(f"- longitudinal_delay_s: {m['longitudinal_delay_s']}")
  print(f"- longitudinal_delay_corr: {m['longitudinal_delay_corr']}")
  print(f"- lateral_rms_error: {m['lateral_rms_error']}")
  print(f"- lateral_oscillation_hz: {m['lateral_oscillation_hz']}")
  print(f"- lateral_saturation_ratio: {m['lateral_saturation_ratio']}")
  print(f"- torque_limit_ratio: {m['torque_limit_ratio']}")
  print(f"- torque_ratio_median: {m['torque_ratio_median']}")
  print("")
  print("[Suggested Parameters]")
  print(f"- SteerActuatorDelay: {sp['SteerActuatorDelay']}  (int x100: {sp['SteerActuatorDelay_int_x100']})")
  print(f"- LongActuatorDelay: {sp['LongActuatorDelay']}  (int x100: {sp['LongActuatorDelay_int_x100']})")
  print(f"- LateralTorqueAccelFactor: {sp['LateralTorqueAccelFactor']}  (int x1000: {sp['LateralTorqueAccelFactor_int_x1000']})")
  print(f"- LateralTorqueFriction: {sp['LateralTorqueFriction']}  (int x1000: {sp['LateralTorqueFriction_int_x1000']})")
  if len(out["notes"]):
    print("")
    print("[Notes]")
    for n in out["notes"]:
      print(f"- {n}")


def main():
  if LogReader is None:
    raise SystemExit(
      "Failed to import LogReader dependencies.\n"
      f"Import error: {IMPORT_ERROR}\n"
      "Run from the openpilot environment with required Python deps (including capnp),\n"
      "for example:\n"
      "  PYTHONPATH=/workspace python3 tools/tuning/analyze_carrot_log.py <identifier>\n"
    )

  parser = argparse.ArgumentParser(
    description="Carrot Pilot log based GV70 tuning helper",
    formatter_class=argparse.RawTextHelpFormatter,
  )
  parser.add_argument(
    "identifier",
    help=(
      "LogReader identifier\n"
      "e.g. /path/to/rlog.bz2\n"
      "e.g. a2a0ccea32023010|2023-07-27--13-01-19/4\n"
    ),
  )
  parser.add_argument("--min-speed", type=float, default=8.0, help="Minimum analysis speed in m/s (default: 8.0)")
  parser.add_argument("--json-out", type=str, default="", help="Path to write JSON result")
  args = parser.parse_args()

  result = analyze(args.identifier, min_speed=args.min_speed)
  print_human(result)

  if args.json_out:
    with open(args.json_out, "w", encoding="utf-8") as f:
      json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
    print(f"\nSaved JSON result: {args.json_out}")


if __name__ == "__main__":
  main()
