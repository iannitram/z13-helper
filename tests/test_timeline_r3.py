"""Locked test for the #134 round-3 timeline review findings (1 and 2).

Finding 1: manual TDP, battery-limit and brightness writes need a before/after
timeline entry, in decky/main.py and (brightness) in src/dashboard_ui.py.
Finding 2: a failed Max Fans toggle must record the unchanged state as "after",
so before == after, and still be marked failed.

No PyQt6 (dashboard_ui.py is covered through the pure helpers it calls), no
hardware: every z13ctl call and sysfs read in decky/main.py is replaced.
"""

import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest

import host
import timeline

REPO = Path(__file__).resolve().parent.parent
DECKY_MAIN = REPO / "decky" / "main.py"

SETTING_CASES = [
    ("tdp", {"tdp": None}, {"tdp": {"pl1": 45, "pl2": 55, "pl3": 55, "force": False}}),
    ("battery_limit", {"battery_limit": 80}, {"battery_limit": 60}),
    ("brightness", {"brightness": "low"}, {"brightness": "high"}),
]


def _entries(path):
    return timeline.read_entries(str(path))


def _only_entry(path):
    got = _entries(path)
    assert len(got) == 1, f"expected exactly one timeline entry, got {len(got)}"
    return got[0]


# ----------------------------------------------------------------------
# timeline.py: the generic setting recorder (finding 1)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("kind,before,after", SETTING_CASES)
def test_setting_change_success_records_before_after_and_rollback(tmp_path, kind, before, after):
    p = tmp_path / "timeline.jsonl"
    timeline.record_setting_change(
        kind=kind, summary=f"{kind} changed", before=before, after=after, ok=True, path=str(p),
    )
    e = _only_entry(p)
    assert e["kind"] == kind
    assert e["ok"] is True
    assert e["before"] == before
    assert e["after"] == after
    assert set(e["changed"]) == set(before)
    assert e["rollback"], "a successful change to a known value must be reversible"
    assert e["no_rollback_reason"] is None


@pytest.mark.parametrize("kind,before,after", SETTING_CASES)
def test_setting_change_failed_write_records_unchanged_after(tmp_path, kind, before, after):
    p = tmp_path / "timeline.jsonl"
    timeline.record_setting_change(
        kind=kind, summary=f"{kind} changed", before=before, after=after, ok=False, path=str(p),
    )
    e = _only_entry(p)
    assert e["ok"] is False
    assert e["before"] == before
    assert e["after"] == before, "a failed write must record the unchanged state as after"
    assert e["changed"] == []
    assert e["rollback"] is None
    assert e["summary"].endswith("(failed)")


def test_setting_change_writes_no_journal_line(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(host, "run", lambda *a, **k: seen.append(a))
    timeline.record_setting_change(
        kind="brightness", summary="Brightness high", before={"brightness": "low"},
        after={"brightness": "high"}, path=str(tmp_path / "timeline.jsonl"),
    )
    assert seen == [], "hardware settings are not Principle 7 host changes"


def test_setting_change_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError):
        timeline.record_setting_change(
            kind="fan", summary="x", before={"fan": 1}, after={"fan": 2},
            path=str(tmp_path / "timeline.jsonl"),
        )


# ----------------------------------------------------------------------
# timeline.py: Max Fans before/after decision (finding 2)
# ----------------------------------------------------------------------

def test_failed_max_fans_on_records_unchanged_state_even_if_saved_curve_drifted():
    before_mf = {"enabled": False, "saved_curve": [[20, 40], [30, 60]]}
    # The desktop app saves the snapshot before the write; the write then fails.
    after_mf = {"enabled": False, "saved_curve": [[20, 255], [30, 255]]}
    before, after = timeline.max_fans_goal_states(before_mf, after_mf, True, ok=False)
    assert after == before
    assert before["goal"] == "custom"
    assert before["max_fans"] is False


def test_failed_max_fans_off_records_unchanged_state():
    before_mf = {"enabled": True, "saved_curve": [[20, 40]]}
    after_mf = {"enabled": True, "saved_curve": [[20, 40]]}
    before, after = timeline.max_fans_goal_states(before_mf, after_mf, False, ok=False)
    assert after == before
    assert before["goal"] == "max"
    assert before["max_fans"] is True


def test_successful_max_fans_toggle_records_the_target_goal():
    before, after = timeline.max_fans_goal_states(
        {"enabled": False, "saved_curve": None}, {"enabled": True, "saved_curve": [[20, 40]]},
        True, ok=True,
    )
    assert before["goal"] == "custom"
    assert after["goal"] == "max"
    assert after["max_fans"] is True


def test_failed_max_fans_toggle_entry_has_before_equal_after(tmp_path):
    p = tmp_path / "timeline.jsonl"
    before, after = timeline.max_fans_goal_states(
        {"enabled": False, "saved_curve": [[20, 40]]},
        {"enabled": False, "saved_curve": [[20, 255]]},
        True, ok=False,
    )
    timeline.record_goal_change(
        goal="max", previous_goal="custom", before=before, after=after, ok=False, path=str(p),
    )
    e = _only_entry(p)
    assert e["ok"] is False
    assert e["before"] == e["after"]
    assert e["changed"] == []
    assert e["summary"].endswith("(failed)")


# ----------------------------------------------------------------------
# decky/main.py: manual TDP, battery limit, brightness (finding 1)
# ----------------------------------------------------------------------

@pytest.fixture
def decky_main(tmp_path, monkeypatch):
    stub = types.SimpleNamespace(
        logger=logging.getLogger("z13-r3-test"),
        DECKY_PLUGIN_SETTINGS_DIR=str(tmp_path / "settings"),
    )
    monkeypatch.setitem(sys.modules, "decky", stub)
    # decky/main.py overwrites timeline.INSTALL_JSON on import; monkeypatch restores it afterwards.
    monkeypatch.setattr(timeline, "INSTALL_JSON", str(tmp_path / "install.json"))
    spec = importlib.util.spec_from_file_location("z13_decky_main_r3", DECKY_MAIN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "TIMELINE_PATH", str(tmp_path / "timeline.jsonl"))
    # The write gate needs a GZ302 DMI product name; CI runners are not a Z13.
    monkeypatch.setattr(m, "_writes_allowed", lambda: True)
    return m


def _ok_run(*_a, **_k):
    return 0, ""


def _failed_run(*_a, **_k):
    return 1, "boom"


def _fake_tdp_store(*_a, **_k):
    return {
        "active": "Balanced",
        "profiles": [{"name": "Balanced", "tdp": {"pl1": 30, "pl2": 40, "pl3": 40, "force": False}}],
    }


def _stub_field_write(monkeypatch, m, ok):
    """Main routes TDP, charge limit and brightness through state_authority.set_field
    (no direct z13ctl call in decky/main.py). Stub that one write; the timeline
    recording around it is what these tests pin."""
    calls = []

    def set_field(field, value, expected=None, **_k):
        calls.append((field, value))
        return [] if ok else [("write", False, "boom")]

    monkeypatch.setattr(m.authority, "set_field", set_field)
    monkeypatch.setattr(m, "_save_plugin_state", lambda **_k: None)
    return calls


def _call(coro):
    import asyncio
    return asyncio.run(coro)


def test_manual_battery_limit_write_is_recorded(decky_main, monkeypatch):
    m = decky_main
    calls = _stub_field_write(monkeypatch, m, ok=True)
    monkeypatch.setattr(m, "_live_battery_limit", lambda: 80)
    res = _call(m.Plugin().set_battery_limit(60))
    assert res["ok"] is True
    assert calls == [("battery_limit", res["value"])]
    e = _only_entry(m.TIMELINE_PATH)
    assert e["kind"] == "battery_limit"
    assert e["ok"] is True
    assert e["before"]["battery_limit"] == 80
    assert e["after"]["battery_limit"] == res["value"] != 80


def test_failed_battery_limit_write_records_unchanged_after(decky_main, monkeypatch):
    m = decky_main
    _stub_field_write(monkeypatch, m, ok=False)
    monkeypatch.setattr(m, "_live_battery_limit", lambda: 80)
    res = _call(m.Plugin().set_battery_limit(60))
    assert res["ok"] is False
    e = _only_entry(m.TIMELINE_PATH)
    assert e["kind"] == "battery_limit"
    assert e["ok"] is False
    assert e["before"] == e["after"]
    assert e["after"]["battery_limit"] == 80


def test_manual_tdp_write_is_recorded(decky_main, monkeypatch):
    m = decky_main
    calls = _stub_field_write(monkeypatch, m, ok=True)
    monkeypatch.setattr(m, "_read_store", _fake_tdp_store)
    monkeypatch.setattr(m, "tdp_ranges", lambda: {"pl1": (4, 80), "pl2": (4, 80), "pl3": (4, 80)})
    res = _call(m.Plugin().set_tdp(True, 45, 55, 55))
    assert res["ok"] is True
    assert [c[0] for c in calls] == ["tdp"]
    e = _only_entry(m.TIMELINE_PATH)
    assert e["kind"] == "tdp"
    assert e["ok"] is True
    assert e["before"]["tdp"]["pl1"] == 30 and e["before"]["tdp"]["pl2"] == 40
    assert e["after"]["tdp"]["pl1"] == res["tdp"]["pl1"] == 45


def test_failed_tdp_write_records_unchanged_after(decky_main, monkeypatch):
    m = decky_main
    _stub_field_write(monkeypatch, m, ok=False)
    monkeypatch.setattr(m, "_read_store", _fake_tdp_store)
    monkeypatch.setattr(m, "tdp_ranges", lambda: {"pl1": (4, 80), "pl2": (4, 80), "pl3": (4, 80)})
    res = _call(m.Plugin().set_tdp(True, 45, 55, 55))
    assert res["ok"] is False
    e = _only_entry(m.TIMELINE_PATH)
    assert e["kind"] == "tdp"
    assert e["ok"] is False
    assert e["before"] == e["after"]


def test_manual_brightness_write_is_recorded(decky_main, monkeypatch):
    m = decky_main
    calls = _stub_field_write(monkeypatch, m, ok=True)
    monkeypatch.setattr(m, "_load_state", lambda: {"brightness": "low"})
    res = _call(m.Plugin().set_brightness("high"))
    assert res["ok"] is True
    assert calls == [("brightness", "high")]
    e = _only_entry(m.TIMELINE_PATH)
    assert e["kind"] == "brightness"
    assert e["ok"] is True
    assert e["before"]["brightness"] == "low"
    assert e["after"]["brightness"] == "high"


def test_failed_brightness_write_records_unchanged_after(decky_main, monkeypatch):
    m = decky_main
    _stub_field_write(monkeypatch, m, ok=False)
    monkeypatch.setattr(m, "_load_state", lambda: {"brightness": "low"})
    res = _call(m.Plugin().set_brightness("high"))
    assert res["ok"] is False
    e = _only_entry(m.TIMELINE_PATH)
    assert e["kind"] == "brightness"
    assert e["ok"] is False
    assert e["before"] == e["after"]
    assert e["after"]["brightness"] == "low"


def test_timeline_failure_never_breaks_a_battery_limit_write(decky_main, monkeypatch):
    """Recording is best-effort: the write is attempted, and the action still succeeds."""
    m = decky_main
    attempts = []

    def broken_append(*_a, **_k):
        attempts.append(1)
        raise OSError("disk full")

    monkeypatch.setattr(timeline, "append", broken_append)
    calls = _stub_field_write(monkeypatch, m, ok=True)
    monkeypatch.setattr(m, "_live_battery_limit", lambda: 80)
    res = _call(m.Plugin().set_battery_limit(60))
    assert res["ok"] is True
    assert calls, "the hardware write must still be made"
    assert attempts, "the battery write must reach the timeline recorder"
