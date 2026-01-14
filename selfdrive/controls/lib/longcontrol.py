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

    self.use_accel_pid = False
    if CP.brand == "toyota":
      self.use_accel_pid = True

  def reset(self):
    self.pid.reset()

  def update(self, active, CS, long_plan, accel_limits, t_since_plan, radarState):

    soft_hold_active = CS.softHoldActive > 0
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
          jerk_limit = float(np.interp(CS.vEgo, [0.0, 1.0], [0.3, 1.2]))  # m/s^3
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
        v_soft = 2.5  # m/s 기준 (~9km/h)
        ratio = max(0.0, min(1.0, CS.vEgo / v_soft))

        # 속도가 낮을수록 감속을 더 강하게 완화 (0.15~1.0)
        soft_factor = 0.15 + 0.85 * ratio
        # 속도가 낮을수록 허용 감속(절대값)을 더 작게 제한 (0.35~1.2 m/s^2)
        decel_cap = 0.35 + 0.85 * ratio

        if output_accel < 0.0:
          output_accel *= soft_factor
          # output_accel은 음수이므로, -decel_cap보다 더 큰(덜 음수) 값으로 캡
          output_accel = max(output_accel, -decel_cap)

        # Extra smoothing: limit braking jerk close to standstill.
        # Prevents sudden brake spikes that can still cause a bump even with decel caps.
        jerk_limit = float(np.interp(CS.vEgo, [0.0, v_soft], [0.25, 2.0]))  # m/s^3
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
