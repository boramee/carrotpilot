import numpy as np
from cereal import car
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N
from openpilot.common.pid import PIDController
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.common.params import Params

CONTROL_N_T_IDX = ModelConstants.T_IDXS[:CONTROL_N]

LongCtrlState = car.CarControl.Actuators.LongControlState


def long_control_state_trans(CP, active, long_control_state, v_ego,
                             should_stop, brake_pressed, cruise_standstill, a_ego, stopping_accel, radarState):
  stopping_condition = should_stop
  starting_condition = (not should_stop and
                        not cruise_standstill and
                        not brake_pressed)
  started_condition = v_ego > CP.vEgoStarting

  if not active:
    long_control_state = LongCtrlState.off

  else:
    if long_control_state == LongCtrlState.off:
      if not starting_condition:
        long_control_state = LongCtrlState.stopping
      else:
        if starting_condition and CP.startingState:
          long_control_state = LongCtrlState.starting
        else:
          long_control_state = LongCtrlState.pid

    elif long_control_state == LongCtrlState.stopping:
      if starting_condition and CP.startingState:
        long_control_state = LongCtrlState.starting
      elif starting_condition:
        long_control_state = LongCtrlState.pid

    elif long_control_state in [LongCtrlState.starting, LongCtrlState.pid]:
      if stopping_condition:
        stopping_accel = stopping_accel if stopping_accel < 0.0 else -0.5
        leadOne = radarState.leadOne
        fcw_stop = leadOne.status and leadOne.dRel < 4.0
        if a_ego > stopping_accel or fcw_stop: # and v_ego < 1.0:
          long_control_state = LongCtrlState.stopping
        if long_control_state == LongCtrlState.starting:
          long_control_state = LongCtrlState.stopping
      elif started_condition:
        long_control_state = LongCtrlState.pid
  return long_control_state

class LongControl:
  def __init__(self, CP):
    self.CP = CP
    self.long_control_state = LongCtrlState.off
    self.pid = PIDController((CP.longitudinalTuning.kpBP, CP.longitudinalTuning.kpV),
                             (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV),
                             k_f=CP.longitudinalTuning.kf, rate=1 / DT_CTRL)
    self.last_output_accel = 0.0


    self.params = Params()
    self.readParamCount = 0
    self.stopping_accel = 0
    self.j_lead = 0.0
    # 소프트 스톱 모드 (0: off, 1: on)
    self.soft_stop_mode = 0
    self.pitch = 0.0

    self.use_accel_pid = False
    if CP.brand == "toyota":
      self.use_accel_pid = True

  def reset(self):
    self.pid.reset()

  def update(self, active, CS, long_plan, accel_limits, t_since_plan, radarState, pitch=0.0):

    soft_hold_active = CS.softHoldActive > 0
    # pitch in radians, positive ~= downhill (see offroad device position UI)
    try:
      self.pitch = float(pitch)
    except Exception:
      self.pitch = 0.0
    a_target_ff = long_plan.aTarget
    v_target_now = long_plan.vTargetNow
    j_target_now = long_plan.jTargetNow
    should_stop = long_plan.shouldStop

    self.readParamCount += 1
    if self.readParamCount >= 100:
      self.readParamCount = 0
      self.stopping_accel = self.params.get_float("StoppingAccel") * 0.01
      self.soft_stop_mode = int(self.params.get_int("SoftStopMode"))
    elif self.readParamCount == 10:
      if len(self.CP.longitudinalTuning.kpBP) == 1 and len(self.CP.longitudinalTuning.kiBP)==1:
        longitudinalTuningKpV = self.params.get_float("LongTuningKpV") * 0.01
        longitudinalTuningKiV = self.params.get_float("LongTuningKiV") * 0.001
        self.pid._k_p = (self.CP.longitudinalTuning.kpBP, [longitudinalTuningKpV])
        self.pid._k_i = (self.CP.longitudinalTuning.kiBP, [longitudinalTuningKiV])
        self.pid._k_f = self.params.get_float("LongTuningKf") * 0.01


    """Update longitudinal control. This updates the state machine and runs a PID loop"""
    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    self.long_control_state = long_control_state_trans(self.CP, active, self.long_control_state, CS.vEgo,
                                                       should_stop, CS.brakePressed,
                                                       CS.cruiseState.standstill, CS.aEgo, self.stopping_accel, radarState)
    if active and soft_hold_active:
      self.long_control_state = LongCtrlState.stopping

    if self.long_control_state == LongCtrlState.off:
      self.reset()
      output_accel = 0.

    elif self.long_control_state == LongCtrlState.stopping:
      output_accel = self.last_output_accel

      if soft_hold_active:
        # Some cars can report soft-hold while still creeping. Jumping straight to stopAccel can feel like a "bump".
        # When SoftStop is enabled, ramp towards stopAccel with a low-speed jerk limit instead of stepping.
        if self.soft_stop_mode > 0 and CS.vEgo < 1.0:
          target = float(self.CP.stopAccel)
          # tighter jerk limit near standstill
          level = int(np.clip(self.soft_stop_mode, 0, 4))
          # 1: mild, 2: normal, 3: strong, 4: adaptive (grade-aware)
          base_level = 2 if level == 4 else max(1, level)
          jerk_min = {1: 0.6, 2: 0.3, 3: 0.2}.get(base_level, 0.3)
          jerk_max = {1: 1.8, 2: 1.2, 3: 0.9}.get(base_level, 1.2)
          # Adaptive: downhill => stronger smoothing (lower jerk), uphill => slightly looser
          if level == 4:
            down = max(0.0, min(0.12, self.pitch))
            up = max(0.0, min(0.12, -self.pitch))
            adapt = float(np.clip(1.0 + 2.5 * down - 1.0 * up, 0.7, 1.6))
            jerk_min /= adapt
            jerk_max /= adapt
          jerk_limit = float(np.interp(CS.vEgo, [0.0, 1.0], [jerk_min, jerk_max]))  # m/s^3
          max_delta = jerk_limit * DT_CTRL
          output_accel = self.last_output_accel + np.clip(target - self.last_output_accel, -max_delta, max_delta)
        else:
          output_accel = self.CP.stopAccel

      stopAccel = self.stopping_accel if self.stopping_accel < 0.0 else self.CP.stopAccel
      if output_accel > stopAccel:
        output_accel = min(output_accel, 0.0)
        output_accel -= self.CP.stoppingDecelRate * DT_CTRL

      # Soft Stop: 정지 직전(아주 저속)에서 감속을 더 완만하게 만들어 '쿵'을 줄임
      # - 기존: 감속 비율만 줄여서 일부 차량에서 효과가 약할 수 있음
      # - 개선: 저속 구간을 넓히고(0~약 9km/h), 감속을 스케일링 + 최대 감속(음수) 캡으로 제한
      if self.soft_stop_mode > 0 and not soft_hold_active and CS.vEgo < 2.5:
        level = int(np.clip(self.soft_stop_mode, 0, 4))
        # 1: mild, 2: normal, 3: strong, 4: adaptive
        base_level = 2 if level == 4 else max(1, level)
        v_soft = {1: 2.0, 2: 2.5, 3: 2.8}.get(base_level, 2.5)  # m/s
        ratio = max(0.0, min(1.0, CS.vEgo / v_soft))

        # 속도가 낮을수록 감속을 더 강하게 완화 (레벨별 하한)
        min_soft_factor = {1: 0.25, 2: 0.15, 3: 0.10}.get(base_level, 0.15)
        soft_factor = min_soft_factor + (1.0 - min_soft_factor) * ratio
        # 초저속(0~약 2km/h)에서 더 부드럽게: 허용 감속(절대값) 하한을 더 낮춤
        v_creep = 0.6  # m/s (~2km/h)
        creep_ratio = max(0.0, min(1.0, CS.vEgo / v_creep))
        # 속도가 낮을수록 허용 감속(절대값)을 더 작게 제한 (레벨별 하한)
        min_decel_cap = {1: 0.35, 2: 0.25, 3: 0.20}.get(base_level, 0.25)
        creep_add = {1: 0.20, 2: 0.15, 3: 0.12}.get(base_level, 0.15)
        decel_cap = (min_decel_cap + creep_add * creep_ratio) + 0.85 * ratio

        # Adaptive: downhill => reduce allowed braking and jerk more; uphill => slightly relax
        if level == 4:
          down = max(0.0, min(0.12, self.pitch))
          up = max(0.0, min(0.12, -self.pitch))
          adapt = float(np.clip(1.0 + 3.0 * down - 1.0 * up, 0.7, 1.8))
          decel_cap = decel_cap / adapt
          # also extend soft-stop region slightly downhill to smooth earlier
          v_soft = float(np.clip(v_soft * (0.95 + 0.15 * adapt), 1.8, 3.2))
          ratio = max(0.0, min(1.0, CS.vEgo / v_soft))
          soft_factor = min_soft_factor + (1.0 - min_soft_factor) * ratio

        if output_accel < 0.0:
          output_accel *= soft_factor
          # output_accel은 음수이므로, -decel_cap보다 더 큰(덜 음수) 값으로 캡
          output_accel = max(output_accel, -decel_cap)

        # "Creep finish": very low speed, bleed off brake so final stop is gentler.
        # This mimics the user-observed behavior where slowly rolling into standstill reduces the bump.
        # Only enable for stronger modes. In Adaptive, reduce the effect on downhill to avoid rolling too far.
        if output_accel < 0.0 and base_level >= 3 and CS.vEgo < v_creep:
          creep_ratio2 = creep_ratio * creep_ratio
          # Keep some braking on downhill (pitch > 0), allow more release on flat/uphill.
          if level == 4:
            down = max(0.0, min(0.10, self.pitch))  # rad
            # 0 (flat/uphill) -> 1.0, 0.10rad downhill -> ~0.55
            downhill_gate = float(np.clip(1.0 - 4.5 * down, 0.55, 1.0))
          else:
            downhill_gate = 1.0

          # At 0 m/s scale ~0.35, at v_creep scale -> 1.0
          creep_scale = (0.35 + 0.65 * creep_ratio2) * downhill_gate
          output_accel *= creep_scale

        # Extra smoothing: limit braking jerk close to standstill.
        # Prevents sudden brake spikes that can still cause a bump even with decel caps.
        min_jerk = {1: 0.25, 2: 0.18, 3: 0.12}.get(base_level, 0.18)
        if level == 4:
          down = max(0.0, min(0.12, self.pitch))
          up = max(0.0, min(0.12, -self.pitch))
          adapt = float(np.clip(1.0 + 3.0 * down - 1.0 * up, 0.7, 1.8))
          min_jerk = min_jerk / adapt
        jerk_limit = float(np.interp(CS.vEgo, [0.0, v_soft], [min_jerk, 2.0]))  # m/s^3
        max_delta = jerk_limit * DT_CTRL
        output_accel = self.last_output_accel + np.clip(output_accel - self.last_output_accel, -max_delta, max_delta)

      self.reset()

    elif self.long_control_state == LongCtrlState.starting:
      output_accel = self.CP.startAccel
      self.reset()

    else:  # LongCtrlState.pid
      if self.use_accel_pid:
        error = a_target_ff - CS.aEgo
      else:
        error = v_target_now - CS.vEgo
      output_accel = self.pid.update(error, speed=CS.vEgo,
                                     feedforward=a_target_ff)

    self.last_output_accel = np.clip(output_accel, accel_limits[0], accel_limits[1])
    return self.last_output_accel, a_target_ff, j_target_now
