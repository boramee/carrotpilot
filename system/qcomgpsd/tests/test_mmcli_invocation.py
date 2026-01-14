import subprocess
import sys
import types


# `qcomgpsd.py` imports compiled/runtime-only dependencies (capnp/cereal, zstandard).
# These unit tests only verify subprocess argv construction, so stub those imports.
stub_cereal = types.ModuleType("cereal")
stub_cereal.log = types.ModuleType("cereal.log")
stub_cereal.messaging = types.ModuleType("cereal.messaging")
sys.modules.setdefault("cereal", stub_cereal)
sys.modules.setdefault("cereal.log", stub_cereal.log)
sys.modules.setdefault("cereal.messaging", stub_cereal.messaging)

stub_common_utils = types.ModuleType("openpilot.common.utils")

def _retry(*_args, **_kwargs):
  def decorator(fn):
    return fn
  return decorator

stub_common_utils.retry = _retry
sys.modules.setdefault("openpilot.common.utils", stub_common_utils)

# Stub hardware pins import chain to avoid pulling in capnp-based enums at import time.
stub_hw = types.ModuleType("openpilot.system.hardware")
stub_hw_tici = types.ModuleType("openpilot.system.hardware.tici")
stub_hw_tici_pins = types.ModuleType("openpilot.system.hardware.tici.pins")

class _GPIO:
  GNSS_PWR_EN = 0

stub_hw_tici_pins.GPIO = _GPIO
sys.modules.setdefault("openpilot.system.hardware", stub_hw)
sys.modules.setdefault("openpilot.system.hardware.tici", stub_hw_tici)
sys.modules.setdefault("openpilot.system.hardware.tici.pins", stub_hw_tici_pins)

# Stub cloudlog to avoid requiring pyzmq in this minimal unit test environment.
stub_swaglog = types.ModuleType("openpilot.common.swaglog")

class _Cloudlog:
  def exception(self, *args, **kwargs):  # pragma: no cover
    pass
  def info(self, *args, **kwargs):  # pragma: no cover
    pass
  def warning(self, *args, **kwargs):  # pragma: no cover
    pass
  def error(self, *args, **kwargs):  # pragma: no cover
    pass
  def debug(self, *args, **kwargs):  # pragma: no cover
    pass

stub_swaglog.cloudlog = _Cloudlog()
sys.modules.setdefault("openpilot.common.swaglog", stub_swaglog)

# Stub qcomgpsd modemdiag/structs to avoid pyserial and binary protocol deps during import.
stub_modemdiag = types.ModuleType("openpilot.system.qcomgpsd.modemdiag")
stub_modemdiag.ModemDiag = object
stub_modemdiag.DIAG_LOG_F = 0
stub_modemdiag.setup_logs = lambda *_args, **_kwargs: None
stub_modemdiag.send_recv = lambda *_args, **_kwargs: None
sys.modules.setdefault("openpilot.system.qcomgpsd.modemdiag", stub_modemdiag)

stub_structs = types.ModuleType("openpilot.system.qcomgpsd.structs")
stub_structs.dict_unpacker = lambda *_args, **_kwargs: (lambda _b: {}, 0)
stub_structs.position_report = object()
stub_structs.relist = lambda x: x
stub_structs.gps_measurement_report = object()
stub_structs.gps_measurement_report_sv = object()
stub_structs.glonass_measurement_report = object()
stub_structs.glonass_measurement_report_sv = object()
stub_structs.oemdre_measurement_report = object()
stub_structs.oemdre_measurement_report_sv = object()
stub_structs.oemdre_svpoly_report = object()
stub_structs.LOG_GNSS_GPS_MEASUREMENT_REPORT = 0
stub_structs.LOG_GNSS_GLONASS_MEASUREMENT_REPORT = 0
stub_structs.LOG_GNSS_POSITION_REPORT = 0
stub_structs.LOG_GNSS_OEMDRE_MEASUREMENT_REPORT = 0
stub_structs.LOG_GNSS_OEMDRE_SVPOLY_REPORT = 0
sys.modules.setdefault("openpilot.system.qcomgpsd.structs", stub_structs)

from openpilot.system.qcomgpsd.qcomgpsd import at_cmd, wait_for_modem


def test_at_cmd_uses_argv_not_shell(monkeypatch):
  called = {}

  def fake_check_output(args, **kwargs):
    assert isinstance(args, list)
    assert args[:5] == ["mmcli", "-m", "any", "--timeout", "30"]
    assert any(a.startswith("--command=") for a in args)
    assert "shell" not in kwargs
    called["ok"] = True
    return "OK"

  monkeypatch.setattr(subprocess, "check_output", fake_check_output)
  assert at_cmd('AT+QGPSXTRATIME=0,"2025/01/01,00:00:00",1,1,1000') == "OK"
  assert called["ok"] is True


def test_wait_for_modem_uses_argv_not_shell(monkeypatch):
  calls = {"n": 0}

  def fake_call(args, **kwargs):
    calls["n"] += 1
    assert isinstance(args, list)
    assert args[:5] == ["mmcli", "-m", "any", "--timeout", "10"]
    assert any(a.startswith("--command=") for a in args)
    assert "shell" not in kwargs
    return 0

  monkeypatch.setattr(subprocess, "call", fake_call)
  wait_for_modem("AT+QGPS?")
  assert calls["n"] == 1

