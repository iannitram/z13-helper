"""Decky backend: profile hooks must not reach the plugin log (issue #8).

`get_profiles()` runs `z13-profile list --json`, whose output holds every
profile's enter/exit hooks (they may carry tokens or private paths). The
logging preference is off here, and the subprocess is stubbed; nothing in the
captured log records may contain the hook text.
"""

import asyncio
import importlib.util
import json
import logging
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
    assert SECRET not in caplog.text
    assert "/home/x/private" not in caplog.text
    for rec in caplog.records:
        assert SECRET not in rec.getMessage()


def test_fan_trace_file_is_owner_only(tmp_path):
    from fan_watchdog import FanTrace
    path = tmp_path / "fan-trace-gamemode.log"
    FanTrace.to_file(str(path), name="perm-test")       # owner=None: no chown
    assert path.stat().st_mode & 0o777 == 0o600
