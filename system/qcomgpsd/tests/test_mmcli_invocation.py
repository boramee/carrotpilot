import subprocess

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

