"""Decky backend: profile hooks must not reach the plugin log (issue #8).

`get_profiles()` runs `z13-profile list --json`, whose output holds every
profile's enter/exit hooks (they may carry tokens or private paths). The
logging preference is off here and the host call is stubbed. Raw command output
may appear only at DEBUG; the INFO/WARNING records carry name, rc and length.
"""

import asyncio
import importlib.util
import json
import logging
import os
import sys
import types
from pathlib import Path

import pytest

import host

REPO = Path(__file__).resolve().parent.parent
SECRET = "SECRET-TOKEN-123"


@pytest.fixture
def main(monkeypatch):
    """decky/main.py imported with a stub `decky` module (the loader's is absent)."""
    stub = types.ModuleType("decky")
    stub.logger = logging.getLogger("decky.test")
    stub.DECKY_PLUGIN_SETTINGS_DIR = "/nonexistent-settings-dir"
    monkeypatch.setitem(sys.modules, "decky", stub)
    spec = importlib.util.spec_from_file_location("decky_main_under_test",
                                                  REPO / "decky" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "logging_enabled", lambda: False)   # the user said no logs
    return mod


def _store_with_hook():
    return json.dumps({
        "active": "Silent",
        "profiles": [
            {"name": "Silent", "hooks": {"enter": f"curl -H 'Authorization: {SECRET}' https://example.invalid",
                                         "exit": ""}},
            {"name": "Turbo", "hooks": {"enter": "", "exit": "/home/x/private/exit.sh"}},
        ],
    })


def test_get_profiles_does_not_log_hook_text(main, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)              # the most verbose capture there is
    monkeypatch.setattr(host, "run", lambda *a, **k: (0, _store_with_hook(), False))

    result = asyncio.run(main.Plugin().get_profiles())

    assert result == {"names": ["Silent", "Turbo"], "active": "Silent"}
    for rec in caplog.records:
        msg = rec.getMessage()
        if SECRET in msg or "/home/x/private" in msg:
            assert rec.levelno == logging.DEBUG, msg               # output: DEBUG only


def test_failing_command_output_is_not_logged_at_warning(main, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(host, "run", lambda *a, **k: (1, _store_with_hook(), False))

    result = asyncio.run(main.Plugin().get_profiles())

    assert result == {"names": [], "active": ""}
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a failing command should leave a WARNING"
    for rec in warnings:
        assert SECRET not in rec.getMessage()
    assert any("output_len=" in r.getMessage() for r in warnings)


def test_fan_trace_file_is_owner_only(tmp_path):
    from fan_watchdog import FanTrace
    path = tmp_path / "fan-trace-gamemode.log"
    FanTrace.to_file(str(path), name="perm-test")       # owner=None: no chown
    assert path.stat().st_mode & 0o777 == 0o600


def test_fan_trace_is_created_0600_without_chmod(tmp_path, monkeypatch):
    """No window at the umask mode: the file is 0600 even if chmod never runs."""
    import fan_watchdog
    def no_chmod(*a, **k):
        raise OSError("chmod disabled for this test")
    monkeypatch.setattr(fan_watchdog.os, "chmod", no_chmod)
    old = os.umask(0o022)
    try:
        path = tmp_path / "created-0600.log"
        fan_watchdog._OwnedRotatingHandler(str(path), None).close()
    finally:
        os.umask(old)
    assert path.stat().st_mode & 0o777 == 0o600


def _planted(tmp_path):
    target = tmp_path / "victim.txt"
    target.write_text("victim\n")
    target.chmod(0o644)
    link = tmp_path / "fan-trace.log"
    link.symlink_to(target)
    return target, link


def test_handler_refuses_planted_symlink(tmp_path, monkeypatch):
    """The watchdog runs as root in a deck-writable dir: a symlink at the log path
    must not be opened, chmod'ed or chowned (fail closed, no path-based fallback)."""
    import fan_watchdog
    target, link = _planted(tmp_path)
    calls = []
    monkeypatch.setattr(fan_watchdog.os, "chmod", lambda *a, **k: calls.append(("chmod", a)))
    monkeypatch.setattr(fan_watchdog.os, "chown", lambda *a, **k: calls.append(("chown", a)))
    with pytest.raises(OSError):
        fan_watchdog._OwnedRotatingHandler(str(link), (os.getuid(), os.getgid()))
    assert calls == []
    assert target.read_text() == "victim\n"
    assert target.stat().st_mode & 0o777 == 0o644


def test_to_file_with_planted_symlink_disables_logging(tmp_path, capsys):
    import fan_watchdog
    target, link = _planted(tmp_path)
    trace = fan_watchdog.FanTrace.to_file(str(link), name="symlink-test")
    trace.log.info("must not land in the victim")
    assert target.read_text() == "victim\n"
    assert target.stat().st_mode & 0o777 == 0o644
    assert "refus" in capsys.readouterr().err.lower()


def test_rollover_does_not_follow_symlink(tmp_path):
    import fan_watchdog
    path = tmp_path / "t.log"
    h = fan_watchdog._OwnedRotatingHandler(str(path), None, maxBytes=10, backupCount=2)
    target = tmp_path / "victim.txt"
    target.write_text("victim\n")
    target.chmod(0o644)
    path.unlink()
    path.symlink_to(target)
    h.doRollover()
    h.close()
    assert target.read_text() == "victim\n"
    assert target.stat().st_mode & 0o777 == 0o644
    assert path.is_file() and not path.is_symlink()
    assert path.stat().st_mode & 0o777 == 0o600


def test_handler_refuses_non_regular_file(tmp_path):
    import fan_watchdog
    fifo = tmp_path / "fifo.log"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        with pytest.raises(OSError):
            fan_watchdog._OwnedRotatingHandler(str(fifo), None)
    finally:
        os.close(reader)


def _hardlinked(tmp_path):
    target = tmp_path / "victim-hl.txt"
    target.write_text("victim\n")
    target.chmod(0o644)
    link = tmp_path / "fan-trace-hl.log"
    os.link(target, link)
    return target, link


def test_handler_refuses_hard_linked_log(tmp_path):
    import fan_watchdog
    target, link = _hardlinked(tmp_path)
    with pytest.raises(OSError):
        fan_watchdog._OwnedRotatingHandler(str(link), (os.getuid(), os.getgid()))
    assert target.read_text() == "victim\n"
    assert target.stat().st_mode & 0o777 == 0o644


def test_to_file_with_hard_linked_log_disables_logging(tmp_path, capsys):
    import fan_watchdog
    target, link = _hardlinked(tmp_path)
    trace = fan_watchdog.FanTrace.to_file(str(link), name="hardlink-test")
    trace.log.info("must not land in the victim")
    assert target.read_text() == "victim\n"
    assert target.stat().st_mode & 0o777 == 0o644
    assert "refus" in capsys.readouterr().err.lower()


def test_normal_log_file_still_logs(tmp_path):
    import fan_watchdog
    path = tmp_path / "ok.log"
    h = fan_watchdog._OwnedRotatingHandler(str(path), None)
    h.emit(logging.LogRecord("t", logging.INFO, "f", 1, "hello", None, None))
    h.close()
    assert "hello" in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


def test_rollover_reopen_refuses_hard_linked_log(tmp_path):
    import fan_watchdog
    path = tmp_path / "t.log"
    h = fan_watchdog._OwnedRotatingHandler(str(path), None, maxBytes=10, backupCount=0)
    target = tmp_path / "victim.txt"
    target.write_text("victim\n")
    target.chmod(0o644)
    path.unlink()
    os.link(target, path)
    with pytest.raises(OSError):
        h.doRollover()
    h.close()
    assert target.read_text() == "victim\n"
    assert target.stat().st_mode & 0o777 == 0o644
