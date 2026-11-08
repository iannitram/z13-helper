"""Review regressions for manual writes, failed goals and strict size caps.

Desktop callbacks are extracted without importing Qt or starting the app.
All host commands and hardware controllers are fakes.
"""

import ast
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

import pytest

import max_fans_state
import timeline

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "decky", types.SimpleNamespace(
        logger=logging.getLogger("timeline.test"),
        DECKY_PLUGIN_SETTINGS_DIR=str(tmp_path / "settings")))
    spec = importlib.util.spec_from_file_location("timeline_backend", REPO / "decky/main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "TIMELINE_PATH", timeline.TIMELINE_PATH)
    monkeypatch.setattr(mod, "INSTALL_JSON", str(tmp_path / "install.json"))
    monkeypatch.setattr(timeline, "INSTALL_JSON", mod.INSTALL_JSON)
    store = {"active": "Balanced", "profiles": [{
        "name": "Balanced", "tdp": {"pl1": 20, "pl2": 30, "pl3": 40},
        "battery_limit": 80, "brightness": "medium"}]}
    monkeypatch.setattr(mod, "_read_store", lambda: store)
    monkeypatch.setattr(mod, "_set_active_field", lambda *a, **kw: ("Balanced", None))
    monkeypatch.setattr(mod, "tdp_ranges", lambda: None)
    monkeypatch.setattr(mod, "_load_state", lambda: {})
    monkeypatch.setattr(mod, "_shared_config", lambda: {})
    return mod


def desktop_method(name):
    tree = ast.parse((REPO / "src/dashboard_ui.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DashboardUI")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"timeline": timeline, "max_fans_state": max_fans_state,
          "_LIGHT_TARGETS": [("Both", ("keyboard", "lightbar"))]}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "dashboard_ui.py", "exec"), ns)
    return ns[name]


@pytest.mark.parametrize("setting", ["tdp", "battery_limit"])
@pytest.mark.parametrize("ok", [True, False])
def test_manual_decky_power_writes_record_outcome(backend, monkeypatch, setting, ok):
    monkeypatch.setattr(backend, "_run", lambda *a, **kw: (0 if ok else 1, "failed"))
    before = backend.active_profile(backend._read_store())[setting]
    if setting == "tdp":
        result = backend.apply_tdp(True, 40, 50, 60)
        requested = result["tdp"]
    else:
        result = backend.apply_battery_limit(60)
        requested = 60
    entry, = timeline.read_entries()
    assert entry["before"] == {setting: before}
    assert entry["after"] == {setting: requested if ok else None}
    assert entry["requested"] == {setting: requested}
    assert entry["ok"] == ok
    assert entry["changed"] == ([setting] if ok else [])
    assert entry["rollback"] is None
    assert entry["before_source"] == "saved_settings"


def test_manual_decky_tdp_off_records_firmware_default(backend, monkeypatch):
    monkeypatch.setattr(backend, "_run", lambda *a, **kw: (0, ""))
    assert backend.apply_tdp(False, 40, 50, 60)["ok"]
    entry, = timeline.read_entries()
    assert entry["after"] == {"tdp": None}
    assert entry["changed"] == ["tdp"]


@pytest.mark.parametrize("failed", [(), ("lightbar",), ("keyboard", "lightbar")])
@pytest.mark.parametrize("timeout", [False, True])
def test_manual_decky_brightness_records_each_device(backend, monkeypatch, failed, timeout):
    def run(argv, **kwargs):
        if argv[2] in failed:
            return 1, "z13ctl write timed out after 20 s" if timeout else "failed"
        return 0, ""

    monkeypatch.setattr(backend, "_run", run)
    backend.set_brightness_per_device("low")
    entry, = timeline.read_entries()
    assert entry["before"] == {"keyboard_brightness": "medium", "lightbar_brightness": "medium"}
    assert entry["after"]["keyboard_brightness"] == (None if "keyboard" in failed else "low")
    assert entry["after"]["lightbar_brightness"] == (None if "lightbar" in failed else "low")
    assert entry["ok"] == (not failed)
    assert entry["changed"] == [f"{dev}_brightness" for dev in ("keyboard", "lightbar") if dev not in failed]


def test_keyboard_timeout_records_unattempted_lightbar_as_unknown(backend, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return 1, "z13ctl write timed out after 20 s"

    monkeypatch.setattr(backend, "_run", run)
    assert not backend.set_brightness_per_device("low")["ok"]
    assert len(calls) == 1
    entry, = timeline.read_entries()
    assert entry["after"] == {"keyboard_brightness": None, "lightbar_brightness": None}
    assert entry["changed"] == []
    assert not entry["ok"]


@pytest.mark.parametrize("failed", [[], ["lightbar"], ["keyboard", "lightbar"]])
def test_manual_desktop_brightness_records_success_partial_and_failure(failed):
    def write(*args, **kwargs):
        if len(failed) == 2:
            raise RuntimeError("no devices responded")
        return failed

    config = {"brightness": "medium"}
    window = types.SimpleNamespace(
        light_target=types.SimpleNamespace(currentIndex=lambda: 0),
        _highlight_brightness=lambda level: None,
        controller=types.SimpleNamespace(set_brightness=write),
        config=types.SimpleNamespace(get=config.get, set=config.__setitem__),
        control=types.SimpleNamespace(_submit=lambda label, fn: fn()))
    if len(failed) == 2:
        with pytest.raises(RuntimeError):
            desktop_method("_change_brightness")(window, "low", "Low")
    else:
        desktop_method("_change_brightness")(window, "low", "Low")
    entry, = timeline.read_entries()
    assert entry["before"] == {"keyboard_brightness": "medium", "lightbar_brightness": "medium"}
    assert entry["after"] == {f"{dev}_brightness": None if dev in failed else "low"
                              for dev in ("keyboard", "lightbar")}
    assert entry["ok"] == (not failed)


@pytest.mark.parametrize("before_on", [True, False])
@pytest.mark.parametrize("fails", [True, False])
def test_desktop_max_fans_goal_matches_actual_state(monkeypatch, before_on, fails):
    state = {"enabled": before_on}
    monkeypatch.setattr(max_fans_state, "load", lambda: dict(state))
    monkeypatch.setattr(max_fans_state, "save", lambda **kw: state.update(kw))
    monkeypatch.setattr(max_fans_state, "restore_plan", lambda: ("auto", None))

    def write():
        if fails:
            raise RuntimeError("hardware write failed")

    window = types.SimpleNamespace(
        controller=types.SimpleNamespace(get_fancurve=lambda: {}, set_fans_max=write, reset_fancurve=write),
        control=types.SimpleNamespace(active_name=lambda: "Balanced", active=lambda: {},
                                      _submit=lambda label, fn: fn()))
    if fails:
        with pytest.raises(RuntimeError):
            desktop_method("_toggle_max_fans")(window, not before_on)
    else:
        desktop_method("_toggle_max_fans")(window, not before_on)
    entry, = timeline.read_entries()
    after_on = before_on if fails else not before_on
    assert entry["after"]["max_fans"] == after_on
    assert entry["after"]["goal"] == ("max" if after_on else "custom")
    assert entry["ok"] == (not fails)
    if fails:
        assert entry["changed"] == []
        assert entry["rollback"] is None


@pytest.mark.parametrize("existing", [False, True])
def test_oversized_entry_is_rejected_without_rotating_or_changing_history(tmp_path, existing):
    path = tmp_path / "timeline.jsonl"
    if existing:
        timeline.append({"kind": "goal", "summary": "seed"}, path=str(path))
    before = path.read_bytes() if existing else None
    with pytest.raises(timeline.TimelineError, match="exceeds max_bytes"):
        timeline.append({"kind": "goal", "summary": "é" * 1000}, path=str(path), max_bytes=500)
    assert (path.read_bytes() if path.exists() else None) == before
    assert not path.with_suffix(".jsonl.1").exists()


def test_partial_line_separator_counts_toward_size_cap(tmp_path):
    path = tmp_path / "timeline.jsonl"
    entry = {"id": "fixed", "ts": "fixed", "kind": "goal", "summary": "test"}
    line_size = len((json.dumps(timeline._normalize_entry(entry), separators=(",", ":")) + "\n").encode())
    path.write_bytes(b"x")
    timeline.append(entry, path=str(path), max_bytes=line_size + 1)
    assert path.stat().st_size == line_size
    assert path.with_suffix(".jsonl.1").read_bytes() == b"x"


@pytest.mark.parametrize("overflow", [False, True])
def test_entry_cap_uses_exact_utf8_byte_count(tmp_path, overflow):
    path = tmp_path / "timeline.jsonl"
    entry = {"id": "fixed", "ts": "fixed", "kind": "goal", "summary": "é" * 20}
    payload = (json.dumps(timeline._normalize_entry(entry), separators=(",", ":"),
                          ensure_ascii=False) + "\n").encode("utf-8")
    if overflow:
        with pytest.raises(timeline.TimelineError, match="exceeds max_bytes"):
            timeline.append(entry, path=str(path), max_bytes=len(payload) - 1)
        assert not path.exists()
    else:
        timeline.append(entry, path=str(path), max_bytes=len(payload))
        assert path.read_bytes() == payload


def test_manual_write_is_not_failed_by_timeline_error(backend, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("bad data")

    monkeypatch.setattr(backend, "_run", lambda *a, **kw: (0, ""))
    monkeypatch.setattr(timeline, "append", fail)
    assert backend.apply_battery_limit(60)["ok"]
