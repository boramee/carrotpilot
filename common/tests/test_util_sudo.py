import builtins
import subprocess

import pytest

from openpilot.common.util import sudo_read, sudo_write


def test_sudo_read_does_not_use_shell(monkeypatch):
  called = {}

  def fake_check_output(args, **kwargs):
    # Ensure we pass argv list, not a shell string
    assert isinstance(args, list)
    assert args[:2] == ["sudo", "cat"]
    assert "shell" not in kwargs
    called["ok"] = True
    return "contents\n"

  monkeypatch.setattr(subprocess, "check_output", fake_check_output)
  assert sudo_read("/some/path") == "contents"
  assert called["ok"] is True


def test_sudo_write_falls_back_to_sudo_tee(monkeypatch):
  # Force the initial open() to fail with PermissionError, then ensure we use `sudo tee`
  def fake_open(*args, **kwargs):
    raise PermissionError

  called = {}

  def fake_run(args, **kwargs):
    assert args[:2] == ["sudo", "tee"]
    assert isinstance(args, list)
    assert kwargs.get("text") is True
    assert kwargs.get("check") is True
    assert kwargs.get("stdout") == subprocess.DEVNULL
    assert kwargs.get("input") == "abc"
    called["ok"] = True
    return subprocess.CompletedProcess(args=args, returncode=0)

  monkeypatch.setattr(builtins, "open", fake_open)
  monkeypatch.setattr(subprocess, "run", fake_run)

  sudo_write("abc", "/some/protected/path")
  assert called["ok"] is True

