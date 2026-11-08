"""Locked tests for the #134 review findings of 2026-11-05 (fix attempt 1).

P1-1  recording never runs inside a hardware transaction (deferred writer)
P1-2  Decky's Max Fans "before" snapshot is read-only and non-migrating
P1-3  Decky startup does not wait for the SteamOS timeline check
P1-4  the timeline folder is held by descriptor (ancestor swaps cannot redirect)
P1-5  the directory walk closes every descriptor it opens
P2-6  Decky's duplicate suppression survives rotation (explicit acknowledgement)
P2-7  an unknown previous value never advertises a rollback
P3-8  recording failures are logged, never silent
Plus: a planted FIFO segment cannot hang a reader.

No hardware, no real HOME: every path is in tmp_path.
"""

import asyncio
import json
import logging
import os
import threading
import time

import pytest

import profiles
import state_authority as authority
import timeline


def _entries(path):
    return timeline.read_entries(str(path))


def _fd_count():
    return len(os.listdir("/proc/self/fd"))


@pytest.fixture
def real_enqueue(monkeypatch):
    """The production enqueue (conftest wraps it so other tests can read right away)."""
    monkeypatch.setattr(timeline, "enqueue", timeline.enqueue.__wrapped__)
    yield timeline.enqueue
    timeline.flush(10)


# ----------------------------------------------------------------------
# P1-1: recording is deferred off the caller's thread
# ----------------------------------------------------------------------

def test_p1_1_deferred_record_returns_while_the_writer_is_blocked(real_enqueue, monkeypatch, tmp_path):
    gate = threading.Event()
    seen = []
    real_append = timeline.append

    def slow_append(rec, **kw):
        seen.append(threading.current_thread().name)
        gate.wait(10)
        return real_append(rec, **kw)

    monkeypatch.setattr(timeline, "append", slow_append)
    path = tmp_path / "tl" / "timeline.jsonl"
    started = time.monotonic()
    rec = timeline.record_setting_change(
        kind="battery_limit", summary="Charge limit 80%",
        before={"battery_limit": 100}, after={"battery_limit": 80},
        path=str(path), defer=True)
    assert time.monotonic() - started < 1.0, "the caller must not wait for the write"
    assert rec["kind"] == "battery_limit"
    assert not path.exists()
    gate.set()
    assert timeline.flush(10)
    assert seen and seen[0] == "z13-timeline-writer", "the write happens on the background writer"
    assert [e["kind"] for e in _entries(path)] == ["battery_limit"]


def test_p1_1_queued_entry_is_a_snapshot_of_the_callers_data(real_enqueue, monkeypatch, tmp_path):
    gate = threading.Event()
    real_append = timeline.append
    monkeypatch.setattr(timeline, "append", lambda rec, **kw: (gate.wait(10), real_append(rec, **kw))[1])
    before = {"tdp": {"pl1": 20, "pl2": 25, "pl3": 30}}
    after = {"tdp": {"pl1": 40, "pl2": 50, "pl3": 60}}
    path = tmp_path / "timeline.jsonl"
    timeline.record_setting_change(kind="tdp", summary="TDP", before=before, after=after,
                                   path=str(path), defer=True)
    before["tdp"]["pl1"] = 999     # the caller keeps mutating its own dicts
    after["tdp"]["pl1"] = 999
    gate.set()
    assert timeline.flush(10)
    got = _entries(path)[0]
    assert got["before"]["tdp"]["pl1"] == 20 and got["after"]["tdp"]["pl1"] == 40


def test_p1_1_authority_write_never_persists_on_the_hardware_thread(real_enqueue, monkeypatch, profiles_env):
    import hardware_controller

    class Hw:
        def set_battery_limit(self, value):
            return ""

    monkeypatch.setattr(hardware_controller, "HardwareController", lambda *a, **k: Hw())
    profiles.save_store({"version": 1, "active": "Balanced", "profiles": profiles.default_profiles()})
    threads = []
    real_append = timeline.append
    monkeypatch.setattr(timeline, "append",
                        lambda rec, **kw: (threads.append(threading.current_thread().name), real_append(rec, **kw))[1])
    authority.set_field("battery_limit", 80, "Balanced")
    assert timeline.flush(10)
    assert threads == ["z13-timeline-writer"]


def test_p1_1_full_queue_drops_with_a_warning_and_never_blocks(real_enqueue, monkeypatch, tmp_path, caplog):
    gate = threading.Event()
    monkeypatch.setattr(timeline, "append", lambda rec, **kw: gate.wait(10))
    monkeypatch.setattr(timeline, "MAX_PENDING", 2)
    import queue
    monkeypatch.setattr(timeline, "_PENDING", queue.Queue(maxsize=2))
    caplog.set_level(logging.WARNING, logger="timeline")
    results = [timeline.enqueue({"kind": "goal", "summary": str(i)}, path=str(tmp_path / "t.jsonl"))
               for i in range(6)]
    gate.set()
    assert any(r is None for r in results)
    assert "dropped" in caplog.text


# ----------------------------------------------------------------------
# P1-2: Decky's snapshot is read-only
# ----------------------------------------------------------------------

@pytest.fixture
def decky_m(main, profiles_env, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TIMELINE_PATH", str(timeline.TIMELINE_PATH))
    monkeypatch.setattr(main, "PROFILES_STORE", str(profiles.STORE_PATH))
    monkeypatch.setattr(main, "MAX_FANS_STATE", str(profiles_env.max_fans))
    monkeypatch.setattr(main, "INSTALL_JSON", str(tmp_path / "install.json"))
    monkeypatch.setattr(main, "SHARED_CONFIG", str(tmp_path / "config.json"))
    return main


def test_p1_2_set_max_fans_snapshot_never_locks_migrates_or_writes(decky_m, profiles_env, tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(json.dumps({"fans_max": True, "fan_curve_saved": [[40, 30]] * 8}))
    assert not profiles_env.max_fans.exists()
    at_write = {}

    def fail(*a, **k):
        raise AssertionError("the timeline snapshot must not use the locking, migrating loader")

    monkeypatch.setattr(decky_m, "_load_max_fans", fail)
    monkeypatch.setattr(decky_m, "_file_lock", fail)
    monkeypatch.setattr(decky_m, "_save_max_fans", fail)

    def fake_set(enabled):
        at_write["state_file_exists"] = profiles_env.max_fans.exists()
        return "ok"

    monkeypatch.setattr(decky_m.authority, "set_max_fans", fake_set)
    assert decky_m._set_max_fans_recorded(False) == "ok"
    assert at_write == {"state_file_exists": False}, "nothing may be seeded before the hardware write"
    assert not profiles_env.max_fans.exists()
    got = _entries(timeline.TIMELINE_PATH)
    assert got and got[0]["kind"] == "goal"
    assert got[0]["before"]["max_fans"] is True       # legacy value seen without migrating it


def test_p1_2_peek_returns_a_copy_of_the_state_file(decky_m, profiles_env):
    profiles_env.max_fans.parent.mkdir(parents=True, exist_ok=True)
    profiles_env.max_fans.write_text(json.dumps({"enabled": True, "saved_curve": None}))
    got = decky_m._peek_max_fans()
    assert got == {"enabled": True, "saved_curve": None}


# ----------------------------------------------------------------------
# P1-3: startup does not wait for the timeline
# ----------------------------------------------------------------------

def test_p1_3_main_starts_the_watchdog_tasks_while_the_steamos_check_is_stuck(main, monkeypatch):
    gate = threading.Event()
    started = threading.Event()

    def stuck_check():
        started.set()
        gate.wait(10)

    class Guard:
        def start(self): pass
        def close(self): pass

    class Stream:
        def __init__(self, *a, **k): pass
        def start(self): return self
        def close(self): pass

    monkeypatch.setattr(main, "BackendMemoryGuard", Guard)
    monkeypatch.setattr(main, "StateStream", Stream)
    monkeypatch.setattr(main.crash_report, "install", lambda **kw: None)
    monkeypatch.setattr(main, "_load_max_fans", lambda: {})
    monkeypatch.setattr(main, "_check_steamos_build_id", stuck_check)

    async def idle(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(main.Plugin, "_enforce_max_fans", idle)
    monkeypatch.setattr(main.Plugin, "_watch_desktop_app", idle)
    out = {}

    async def run():
        plugin = main.Plugin()
        began = time.monotonic()
        await plugin._main()
        out["elapsed"] = time.monotonic() - began
        out["enforce"] = plugin._enforce_task is not None
        await plugin._unload()
        gate.set()
        await asyncio.gather(plugin._enforce_task, plugin._desktop_task, plugin._steamos_task,
                             return_exceptions=True)

    asyncio.run(run())
    assert out["enforce"] and out["elapsed"] < 5.0
    assert started.is_set()


# ----------------------------------------------------------------------
# P1-4: the folder is held by descriptor
# ----------------------------------------------------------------------

def test_p1_4_ancestor_swap_after_the_check_cannot_redirect_appends_or_rotation(tmp_path):
    outer = tmp_path / "a"
    target = outer / "b" / "timeline.jsonl"
    line = b'{"id":"1","kind":"goal"}\n'
    with timeline.timeline_lock(str(target)) as dir_fd:
        os.rename(outer, tmp_path / "a_moved")        # attacker swaps a writable ancestor
        (tmp_path / "a" / "b").mkdir(parents=True)
        timeline._append_locked(line, str(target), 1 << 20, 3, None, dir_fd)
        timeline._append_locked(line, str(target), len(line) + 1, 3, None, dir_fd)   # forces a rotation
    moved = tmp_path / "a_moved" / "b"
    assert (moved / "timeline.jsonl").exists() and (moved / "timeline.jsonl.1").exists()
    assert os.listdir(tmp_path / "a" / "b") == [], "nothing may land in the swapped-in folder"


def test_p1_4_lock_is_reentrant_and_shares_the_folder_descriptor(tmp_path):
    target = str(tmp_path / "x" / "timeline.jsonl")
    with timeline.timeline_lock(target) as outer_fd:
        with timeline.timeline_lock(target) as inner_fd:
            assert inner_fd == outer_fd
        os.fstat(outer_fd)    # still open after the inner block


# ----------------------------------------------------------------------
# P1-5: no descriptor leaks
# ----------------------------------------------------------------------

@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc")
def test_p1_5_directory_walk_closes_every_descriptor(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d"
    before = _fd_count()
    for _ in range(5):
        os.close(timeline._open_dir_chain(str(deep)))
    assert _fd_count() == before


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc")
def test_p1_5_append_and_failed_walk_do_not_leak(tmp_path):
    path = str(tmp_path / "a" / "b" / "c" / "timeline.jsonl")
    before = _fd_count()
    for i in range(5):
        timeline.append({"kind": "goal", "summary": str(i)}, path=path, journal=False)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "a", target_is_directory=True)
    for _ in range(3):
        with pytest.raises(OSError):
            timeline._open_dir_chain(str(link / "b"))
    assert _fd_count() == before


# ----------------------------------------------------------------------
# P2-6: duplicate suppression is by acknowledgement, not by line count
# ----------------------------------------------------------------------

def test_p2_6_rotation_does_not_cause_a_second_record(decky_m, profiles_env, monkeypatch):
    import hardware_controller

    class Hw:
        def set_battery_limit(self, value):
            return ""

    monkeypatch.setattr(hardware_controller, "HardwareController", lambda *a, **k: Hw())
    profiles.save_store({"version": 1, "active": "Balanced", "profiles": profiles.default_profiles()})
    # A tiny cap makes every append rotate, so the number of lines never grows.
    real_append = timeline.append
    monkeypatch.setattr(timeline, "append", lambda rec, **kw: real_append(rec, **{**kw, "max_bytes": 600}))
    timeline.append({"kind": "goal", "summary": "x" * 300}, journal=False)
    result = decky_m._edit_field_recorded("battery_limit", 80, "Balanced")
    assert result["ok"] is True
    kinds = [e["kind"] for e in timeline.read_entries(timeline.TIMELINE_PATH)]
    assert kinds.count("battery_limit") == 1


def test_p2_6_write_refused_before_the_push_is_recorded_by_decky_once(decky_m, profiles_env, monkeypatch):
    """When the shared layer never reached the hardware it recorded nothing, so Decky records the failure."""
    def refused(*a, **k):
        raise OSError("store locked")

    monkeypatch.setattr(decky_m.authority, "set_field", refused)
    result = decky_m._edit_field_recorded("battery_limit", 70, "Balanced")
    assert result["ok"] is False
    got = timeline.read_entries(timeline.TIMELINE_PATH)
    assert [(e["kind"], e["ok"]) for e in got] == [("battery_limit", False)]


# ----------------------------------------------------------------------
# P2-7: unknown previous values never advertise a rollback
# ----------------------------------------------------------------------

@pytest.mark.parametrize("kind,key,new", [("battery_limit", "battery_limit", 80), ("brightness", "brightness", "high")])
def test_p2_7_unknown_previous_value_has_no_rollback(tmp_path, kind, key, new):
    rec = timeline.record_setting_change(kind=kind, summary="s", before={key: None}, after={key: new},
                                         path=str(tmp_path / "t.jsonl"))
    assert rec["rollback"] is None
    assert rec["no_rollback_reason"] == "Previous value not known"


def test_p2_7_known_previous_value_still_rolls_back_and_tdp_none_is_a_reset(tmp_path):
    rec = timeline.record_setting_change(kind="battery_limit", summary="s", before={"battery_limit": 100},
                                         after={"battery_limit": 80}, path=str(tmp_path / "t.jsonl"))
    assert rec["rollback"] == {"kind": "battery_limit", "state": {"battery_limit": 100}}
    rec = timeline.record_setting_change(kind="tdp", summary="s", before={"tdp": None},
                                         after={"tdp": {"pl1": 30, "pl2": 35, "pl3": 40}},
                                         path=str(tmp_path / "t.jsonl"))
    assert rec["rollback"] == {"kind": "tdp", "state": {"tdp": None}}


# ----------------------------------------------------------------------
# P3-8: failures are logged
# ----------------------------------------------------------------------

def test_p3_8_failed_background_write_is_logged_not_silent(real_enqueue, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="timeline")

    def boom(rec, **kw):
        raise timeline.TimelineLockError(110, "timed out locking")

    monkeypatch.setattr(timeline, "append", boom)
    timeline.enqueue({"kind": "goal", "summary": "SECRET-TOKEN-123"}, path=str(tmp_path / "t.jsonl"))
    assert timeline.flush(10)
    assert "TimelineLockError" in caplog.text
    assert "SECRET-TOKEN-123" not in caplog.text


def test_p3_8_profile_apply_logs_a_timeline_failure_and_keeps_the_result(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(profiles, "_apply_steps", lambda *a, **k: [("profile", True, "ok")])

    def boom(**kw):
        raise OSError("disk full")

    monkeypatch.setattr(profiles.timeline, "record_profile_apply", boom)
    got = profiles.apply({"name": "Balanced", "base": "balanced"})
    assert got == [("profile", True, "ok")]
    assert "timeline recording failed" in caplog.text and "disk full" in caplog.text


# ----------------------------------------------------------------------
# Planted FIFO segment cannot hang a reader
# ----------------------------------------------------------------------

def test_planted_fifo_segment_is_skipped_without_blocking(tmp_path):
    base = tmp_path / "timeline.jsonl"
    timeline.append({"kind": "goal", "summary": "real"}, path=str(base), journal=False)
    os.mkfifo(str(base) + ".1")
    done = []
    t = threading.Thread(target=lambda: done.append(timeline.read_entries(str(base))), daemon=True)
    t.start()
    t.join(5)
    assert done and [e["summary"] for e in done[0]] == ["real"]
