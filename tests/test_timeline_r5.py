"""Locked tests for the #134 review round-5 findings (head b20288a).

1 recording never holds the hardware lock; 2 Decky's Max Fans "before" read is
read-only; 3 the fan watchdog starts without waiting on the timeline; 4 the
timeline folder descriptor is kept for lock, append and rotation; 5 the folder
walk closes every descriptor; 6 no count-based duplicate inference; 7 unknown
previous values give no rollback; 8 recording failures log bounded warnings.

No hardware, no real HOME: every hardware call is a stub.
"""

import asyncio
import logging
import os
import threading
import time
import types

import pytest

import profiles
import state_authority as authority
import timeline


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch):
    monkeypatch.setitem(timeline._WARN_STATE, "start", 0.0)
    monkeypatch.setitem(timeline._WARN_STATE, "count", 0)
    yield
    timeline.flush()


class _FakeHardware:
    def __init__(self, *a, **k):
        pass

    def set_battery_limit(self, value):
        return ""

    def apply_tdp(self, value):
        return ""


@pytest.fixture
def fake_hardware(monkeypatch):
    import hardware_controller
    monkeypatch.setattr(hardware_controller, "HardwareController", _FakeHardware)


@pytest.fixture
def decky_m(main, profiles_env, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TIMELINE_PATH", str(timeline.TIMELINE_PATH))
    monkeypatch.setattr(main, "PROFILES_STORE", str(profiles.STORE_PATH))
    monkeypatch.setattr(main, "MAX_FANS_STATE", str(profiles_env.max_fans))
    monkeypatch.setattr(main, "INSTALL_JSON", str(tmp_path / "install.json"))
    monkeypatch.setattr(main, "OS_RELEASE_PATH", str(tmp_path / "os-release"))
    return main


def _write_store():
    profiles.save_store({"version": 1, "active": "Balanced", "profiles": profiles.default_profiles()})


def _held_elsewhere(path):
    """Hold the timeline lock from another thread (as another process would)."""
    ready, release = threading.Event(), threading.Event()

    def hold():
        with timeline.timeline_lock(path):
            ready.set()
            release.wait(30)
    t = threading.Thread(target=hold, daemon=True)
    t.start()
    assert ready.wait(5)
    return release, t


# ---- 1: recording never holds the hardware lock -------------------------------

def test_r5_1_setting_write_does_not_wait_on_a_contended_timeline(profiles_env, fake_hardware):
    _write_store()
    release, t = _held_elsewhere(timeline.TIMELINE_PATH)
    try:
        start = time.monotonic()
        authority.set_field("battery_limit", 80, "Balanced")
        elapsed = time.monotonic() - start
        # The hardware mutex is free again at once, while the entry still waits.
        assert authority._MUTEX.acquire(blocking=False)
        authority._MUTEX.release()
    finally:
        release.set()
        t.join(5)
    assert elapsed < 2.0, "the hardware transaction waited on the timeline lock"
    assert timeline.flush()
    assert [e["kind"] for e in timeline.read_entries()] == ["battery_limit"]


def test_r5_1_profile_apply_does_not_wait_on_a_contended_timeline(profiles_env, monkeypatch):
    _write_store()
    monkeypatch.setattr(profiles, "_apply_steps", lambda *a, **k: [("profile", True, "ok")])
    release, t = _held_elsewhere(timeline.TIMELINE_PATH)
    try:
        start = time.monotonic()
        with authority.transaction():
            profiles.apply(profiles.get_profile("Balanced"))
        elapsed = time.monotonic() - start
    finally:
        release.set()
        t.join(5)
    assert elapsed < 2.0, "profiles.apply waited on the timeline lock"
    assert [e["kind"] for e in timeline.read_entries()] == ["profile"]


def test_r5_1_queued_entry_is_frozen_at_enqueue(tmp_path):
    path = str(tmp_path / "t.jsonl")
    before = {"battery_limit": 70}
    release, t = _held_elsewhere(path)
    try:
        timeline.record_setting_change(kind="battery_limit", summary="x", before=before,
                                       after={"battery_limit": 80}, path=path, defer=True)
        before["battery_limit"] = 1   # a later mutation must not reach the record
    finally:
        release.set()
        t.join(5)
    (e,) = timeline.read_entries(path)
    assert e["before"] == {"battery_limit": 70}


# ---- 2: Decky's "before" snapshot is read-only --------------------------------

def test_r5_2_decky_max_fans_before_snapshot_never_seeds_or_locks(decky_m, profiles_env, monkeypatch):
    m = decky_m
    assert not os.path.exists(m.MAX_FANS_STATE)
    monkeypatch.setattr(m, "_shared_config", lambda: {"fans_max": True, "fan_curve_saved": [[1, 2]]})
    monkeypatch.setattr(m, "_load_max_fans", lambda: pytest.fail("before snapshot seeded state"))
    monkeypatch.setattr(m, "_file_lock", lambda *a: pytest.fail("before snapshot took the Max Fans lock"))
    seen = []
    monkeypatch.setattr(authority, "set_max_fans",
                        lambda enabled: seen.append(os.path.exists(m.MAX_FANS_STATE)) or "Max Fans ON")
    assert m._set_max_fans_recorded(True) == "Max Fans ON"
    assert seen == [False], "max-fans.json must not be seeded before the authority write"


# ---- 3: watchdog start does not wait on the timeline --------------------------

def test_r5_3_watchdog_starts_while_build_id_record_is_stuck(decky_m, monkeypatch):
    m = decky_m
    gate = threading.Event()
    started = []
    monkeypatch.setattr(m, "_check_steamos_build_id", lambda: gate.wait(10))
    monkeypatch.setattr(m, "BackendMemoryGuard", lambda: types.SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(m.crash_report, "install", lambda **k: None)
    monkeypatch.setattr(m, "StateStream", lambda *a, **k: types.SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(m, "_load_max_fans", lambda: {})

    async def enforce(self):
        started.append("watchdog")

    async def desktop(self):
        return None
    monkeypatch.setattr(m.Plugin, "_enforce_max_fans", enforce)
    monkeypatch.setattr(m.Plugin, "_watch_desktop_app", desktop)

    async def run():
        p = m.Plugin()
        await asyncio.wait_for(p._main(), 2)
        await asyncio.sleep(0.05)
        gate.set()
        await p._timeline_task
    try:
        asyncio.run(run())
    finally:
        gate.set()
    assert started == ["watchdog"]


def test_r5_3_fifo_segment_is_rejected_without_blocking(tmp_path):
    fifo = tmp_path / "timeline.jsonl"
    os.mkfifo(fifo)
    out = []
    t = threading.Thread(target=lambda: out.append(timeline._read_single_file(str(fifo))), daemon=True)
    t.start()
    t.join(2)
    assert out == [[]], "reading a FIFO segment blocked or returned data"


# ---- 4: the folder descriptor is kept (no parent substitution) -----------------

def test_r5_4_swapping_the_folder_after_the_check_cannot_redirect_writes(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir()
    moved = tmp_path / "checked"
    path = str(d / "timeline.jsonl")
    timeline.append({"kind": "host", "summary": "seed" * 20}, path=path, journal=False)
    real_open_dir = timeline._open_dir

    def open_then_swap(dir_path, owner=None):
        fd = real_open_dir(dir_path, owner)
        if os.path.abspath(dir_path) == str(d) and not moved.exists():
            os.rename(d, moved)
            d.mkdir()          # an attacker's substitute
        return fd
    monkeypatch.setattr(timeline, "_open_dir", open_then_swap)
    timeline.append({"kind": "host", "summary": "x" * 80}, path=path, journal=False, max_bytes=400)

    assert sorted(os.listdir(d)) == [], "nothing may be written into the substituted folder"
    assert "timeline.jsonl.1" in os.listdir(moved), "rotation must stay in the checked folder"
    assert len(timeline.read_entries(str(moved / "timeline.jsonl"))) == 2


# ---- 5: no descriptor leak ----------------------------------------------------

def test_r5_5_directory_walk_closes_every_descriptor(tmp_path):
    path = str(tmp_path / "a" / "b" / "c" / "timeline.jsonl")
    timeline.append({"kind": "host"}, path=path, journal=False)
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(20):
        timeline.append({"kind": "host"}, path=path, journal=False)
    timeline._ensure_dir(os.path.dirname(path))
    assert len(os.listdir("/proc/self/fd")) == before


def test_r5_5_walk_closes_descriptors_on_refusal(tmp_path):
    (tmp_path / "file").write_text("x")
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(5):
        with pytest.raises(timeline.TimelineError):
            timeline._ensure_dir(str(tmp_path / "file" / "sub"))
    assert len(os.listdir("/proc/self/fd")) == before


# ---- 6: no count-based duplicate inference ------------------------------------

def test_r5_6_owner_recording_is_not_duplicated_when_rotation_keeps_count(decky_m, monkeypatch):
    m = decky_m
    monkeypatch.setattr(m, "_live_battery_limit", lambda: 70)
    # Rotation dropped one entry while the owner added one: the count is unchanged.
    monkeypatch.setattr(timeline, "read_entries", lambda *a, **k: [{"kind": "host"}] * 5)

    def owner_writes(field, value, expected):
        timeline.record_setting_change(kind=field, summary="owner", before={field: 70},
                                       after={field: value}, path=m.TIMELINE_PATH, defer=True)
        return {"ok": True, "saved": True, "message": "Applied", "failed": []}
    monkeypatch.setattr(m, "_edit_field", owner_writes)
    calls = []
    real = timeline.enqueue
    monkeypatch.setattr(timeline, "enqueue", lambda *a, **k: calls.append(1) or real(*a, **k))
    m._edit_field_recorded("battery_limit", 80, "Balanced")
    assert len(calls) == 1


def test_r5_6_fallback_records_when_owner_did_not(decky_m, monkeypatch):
    m = decky_m
    monkeypatch.setattr(m, "_live_battery_limit", lambda: 70)
    monkeypatch.setattr(m, "_edit_field",
                        lambda *a: {"ok": True, "saved": True, "message": "Applied", "failed": []})
    m._edit_field_recorded("battery_limit", 80, "Balanced")
    assert [e["summary"] for e in timeline.read_entries(m.TIMELINE_PATH)] == ["Charge limit 80%"]


# ---- 7: unknown previous values ----------------------------------------------

@pytest.mark.parametrize("kind,before", [
    ("battery_limit", {"battery_limit": None}),
    ("brightness", {"brightness": None}),
    ("tdp", {"tdp": timeline.UNKNOWN}),
    ("battery_limit", {"battery_limit": timeline.UNKNOWN}),
])
def test_r5_7_unknown_previous_value_offers_no_rollback(tmp_path, kind, before):
    after = {kind: {"pl1": 1, "pl2": 2, "pl3": 3} if kind == "tdp" else 80}
    rec = timeline.record_setting_change(kind=kind, summary="x", before=before, after=after,
                                         path=str(tmp_path / "t.jsonl"))
    assert rec["rollback"] is None
    assert rec["no_rollback_reason"] == "Previous value not known"
    assert rec["before"] == {kind: None}


def test_r5_7_tdp_off_is_a_real_previous_value(tmp_path):
    rec = timeline.record_setting_change(kind="tdp", summary="x", before={"tdp": None},
                                         after={"tdp": {"pl1": 1, "pl2": 2, "pl3": 3}},
                                         path=str(tmp_path / "t.jsonl"))
    assert rec["rollback"] == {"kind": "tdp", "state": {"tdp": None}}


def test_r5_7_edit_of_unknown_profile_records_no_rollback(profiles_env, fake_hardware, monkeypatch):
    _write_store()
    monkeypatch.setattr(authority, "_read_saved_brightness", lambda: None)
    # TDP None is a real value ("off"), so only before_known tells unknown apart.
    authority._push_field({"name": "Gone", "tdp": {"pl1": 1, "pl2": 2, "pl3": 3}}, "tdp",
                          before=None, before_known=False)
    (e,) = timeline.read_entries()
    assert e["rollback"] is None


# ---- 8: failures log bounded, secret-safe warnings ----------------------------

SECRET = "/srv/hooks/secret-hook --token=abc"


def _boom(*a, **k):
    raise OSError(13, SECRET)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_r5_8_profile_apply_logs_a_safe_warning(profiles_env, monkeypatch, caplog):
    _write_store()
    monkeypatch.setattr(profiles, "_apply_steps", lambda *a, **k: [("profile", True, "ok")])
    monkeypatch.setattr(timeline, "record_profile_apply", _boom)
    with caplog.at_level(logging.WARNING):
        assert profiles.apply(profiles.get_profile("Balanced")) == [("profile", True, "ok")]
    msgs = _warnings(caplog)
    assert msgs and "EACCES" in msgs[0]
    assert not any("secret" in x or "/srv" in x for x in msgs)


def test_r5_8_decky_setting_record_failure_logs_a_warning(decky_m, monkeypatch, caplog):
    monkeypatch.setattr(timeline, "record_setting_change", _boom)
    with caplog.at_level(logging.WARNING):
        decky_m._timeline_setting("battery_limit", "x", {}, {}, True)
    msgs = _warnings(caplog)
    assert msgs and not any("secret" in x for x in msgs)


def test_r5_8_dashboard_max_fans_record_failure_logs_a_warning(monkeypatch, caplog):
    from test_timeline_r4 import _FakeControl, _install_qt_stub
    import sys
    try:
        import PyQt6.QtWidgets  # noqa: F401
    except ImportError:
        _install_qt_stub(monkeypatch)
        for name in list(sys.modules):
            if name in ("dashboard_ui", "profile_control", "fans_window", "theme", "updates_dialog",
                        "post_update_view", "display_modes", "screen_brightness", "apply_worker"):
                monkeypatch.delitem(sys.modules, name, raising=False)
    import dashboard_ui
    monkeypatch.setattr(authority, "set_max_fans", lambda enabled: "Max Fans ON")
    monkeypatch.setattr(timeline, "record_goal_change", _boom)
    fake = types.SimpleNamespace(control=_FakeControl())
    monkeypatch.setattr(fake.control, "active_name", lambda: "Balanced")
    dashboard_ui.DashboardUI._toggle_max_fans(fake, True)
    with caplog.at_level(logging.WARNING):
        assert fake.control.submitted[0][1]() == "Max Fans ON"
    msgs = _warnings(caplog)
    assert msgs and not any("secret" in x for x in msgs)


def test_r5_8_warnings_are_rate_limited(caplog):
    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            timeline.warn_failure("x", OSError(5, SECRET))
    assert len(_warnings(caplog)) == timeline.WARN_MAX_PER_WINDOW
