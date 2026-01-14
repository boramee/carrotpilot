import os
import subprocess
import sys
import types
from pathlib import Path


# `tombstoned.py` pulls in runtime-only deps; this unit test only targets
# `get_apport_stacktrace`, so stub heavy imports.
stub_sentry = types.ModuleType("openpilot.system.sentry")
stub_sentry.SentryProject = types.SimpleNamespace(SELFDRIVE_NATIVE=0)
stub_sentry.init = lambda *_args, **_kwargs: False
stub_sentry.report_tombstone = lambda *_args, **_kwargs: None
sys.modules.setdefault("openpilot.system.sentry", stub_sentry)

stub_hw = types.ModuleType("openpilot.system.hardware.hw")

class _Paths:
  @staticmethod
  def log_root():
    return "/tmp"

stub_hw.Paths = _Paths
sys.modules.setdefault("openpilot.system.hardware.hw", stub_hw)

stub_swaglog = types.ModuleType("openpilot.common.swaglog")
stub_swaglog.cloudlog = types.SimpleNamespace(info=lambda *_a, **_k: None,
                                              error=lambda *_a, **_k: None,
                                              exception=lambda *_a, **_k: None)
sys.modules.setdefault("openpilot.common.swaglog", stub_swaglog)

stub_version = types.ModuleType("openpilot.system.version")
stub_version.get_build_metadata = lambda: types.SimpleNamespace(openpilot=types.SimpleNamespace(git_commit="deadbeef"))
sys.modules.setdefault("openpilot.system.version", stub_version)

from openpilot.system.tombstoned import get_apport_stacktrace


def test_get_apport_stacktrace_uses_tempfile_and_no_shell(tmp_path, monkeypatch):
  crash = tmp_path / "weird name\".crash"
  crash.write_text("Foo: bar\n")

  def fake_check_output(args, **kwargs):
    # Ensure argv form and no shell invocation
    assert args[:2] == ["apport-retrace", "-s"]
    assert "shell" not in kwargs

    supplied = Path(args[2])
    assert supplied.exists()
    data = supplied.read_text()
    assert data.startswith("Package: openpilot\n")
    assert "Foo: bar\n" in data
    return "stacktrace ok"

  monkeypatch.setattr(subprocess, "check_output", fake_check_output)
  out = get_apport_stacktrace(str(crash))
  assert out == "stacktrace ok"

