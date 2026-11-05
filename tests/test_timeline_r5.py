"""Locked tests for the #134 review round-5 findings (fan/power/battery review of PR #175).

Each test is named for the finding it locks:

  r5_recording_*      P1: recording never runs inside the hardware, Max Fans or Decky lock
  r5_queue_*          P1: the bounded recorder drops instead of blocking
  r5_main_*           P1/P3: Decky _main starts the watchdog before any timeline work
  r5_max_fans_*       P2: the Decky Max Fans "before" snapshot is read-only, lock-free
  r5_ensure_dir_*     P1/F4: _ensure_dir closes every intermediate descriptor
  r5_dir_fd_*         P1/F4: append and rotation use directory-relative calls
  r5_fifo_*           P3: a FIFO segment is skipped without blocking
  r5_single_record_*  P2/F10: one recording owner, no double record across rotation
  r5_unknown_*        P2: a None before-value gives no rollback hint
  r5_warning_*        P3: a failed record logs one bounded warning

New API the design must provide (see the Qwen brief):
  timeline.submit(entry, *, path=None, journal=..., journal_tag=..., journal_fn=..., owner=...) -> bool
  timeline.flush(timeout) -> bool            (True when the queue has drained)
  timeline.dropped_count() -> int

Tests that need the new API fail with "not implemented" until it exists. Tests that read the
timeline after a call site use _settle(), which waits for the writer only when flush() exists;
on the synchronous code they fail on their own assertion, not on the API.

No hardware, no real HOME, no real Qt. conftest.py supplies the autouse path and subprocess guards.
"""

import asyncio
import collections
import fcntl
import json
import logging
import os
import threading
import time

import pytest

import profiles
import state_authority as authority
import timeline

TS = "2026-11-05T07:00:00+0000"


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _require(*names):
    missing = [n for n in names if not hasattr(timeline, n)]
    if missing:
        pytest.fail("new recorder API not implemented yet: "
                    + ", ".join(f"timeline.{n}" for n in missing), pytrace=False)


def _settle():
    """Wait until queued recordings are written, when the async recorder exists."""
    flush = getattr(timeline, "flush", None)
    if flush is not None:
        assert flush(timeout=10), "timeline writer did not drain its queue"


def _entries(path):
    return timeline.read_entries(str(path))


def _line(record_id, kind="profile", summary="", pad_to=None):
    rec = {"id": record_id, "kind": kind, "summary": summary, "ts": TS}
    text = json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n"
    if pad_to is not None:
        extra = pad_to - len(text.encode("utf-8"))
        rec["summary"] = summary + "x" * extra
        text = json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n"
        assert len(text.encode("utf-8")) == pad_to
    return text


def _write_store(profiles_env, active="Balanced"):
    profiles.save_store({"version": 1, "active": active, "profiles": profiles.default_profiles()})


def _mutex_free(timeout):
    """True when state_authority's hardware RLock can be taken from another thread."""
    got = []

    def probe():
        ok = authority._MUTEX.acquire(timeout=timeout)
        if ok:
            authority._MUTEX.release()
        got.append(ok)

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(timeout + 2)
    return bool(got and got[0])


def _flock_free(path, timeout):
    """True when the inter-process hardware flock can be taken (briefly polled)."""
    if not os.path.exists(path):
        return True
    deadline = time.monotonic() + timeout
    fd = os.open(path, os.O_RDWR)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.01)
                continue
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
    finally:
        os.close(fd)


def _hardware_locks_held(timeout=0.5):
    return not (_mutex_free(timeout) and _flock_free(profiles.STORE_PATH + ".hardware.lock", timeout))


def _timeline_warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and "timeline" in r.getMessage().lower()]


def _break_recording(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("simulated recorder failure")
    monkeypatch.setattr(timeline, "submit", boom, raising=False)


class _FakeHW:
    """Stands in for hardware_controller.HardwareController: every write succeeds, none touches sysfs."""

    def __init__(self, *_a, **_k):
        pass

    def set_battery_limit(self, value):
        return ""

    def apply_tdp(self, value):
        return ""

    def set_brightness(self, value, devices=None):
        return False


@pytest.fixture
def fake_hw(monkeypatch):
    import hardware_controller
    monkeypatch.setattr(hardware_controller, "HardwareController", _FakeHW)
    return _FakeHW


@pytest.fixture
def dm(main, profiles_env, tmp_path, monkeypatch):
    """decky/main.py with every file it reads or writes pointed into tmp_path."""
    cfg = tmp_path / "config" / "z13-helper"
    install = str(tmp_path / "install.json")
    monkeypatch.setattr(main, "TIMELINE_PATH", str(timeline.TIMELINE_PATH))
    monkeypatch.setattr(timeline, "INSTALL_JSON", install)
    monkeypatch.setattr(main, "INSTALL_JSON", install)
    monkeypatch.setattr(main, "PROFILES_STORE", str(profiles_env.store))
    monkeypatch.setattr(main, "MAX_FANS_STATE", str(profiles_env.max_fans))
    monkeypatch.setattr(main, "SHARED_CONFIG", str(cfg / "config.json"))
    monkeypatch.setattr(main, "OS_RELEASE_PATH", str(tmp_path / "os-release"))
    return main


# ----------------------------------------------------------------------
# P1: recording is outside every lock
# ----------------------------------------------------------------------

@pytest.mark.parametrize("path", ["apply", "set_field"])
def test_r5_recording_never_runs_while_a_hardware_lock_is_held(path, profiles_env, fake_hw, monkeypatch):
    monkeypatch.setattr(profiles, "_apply_steps", lambda *a, **k: [("profile", True, "ok")])
    seen = []
    real_append = timeline.append

    def probe(entry, **kw):
        seen.append(_hardware_locks_held())
        return real_append(entry, **kw)

    monkeypatch.setattr(timeline, "append", probe)
    _write_store(profiles_env)
    if path == "apply":
        profiles.apply({"name": "Silent", "base": "quiet"},
                       previous={"name": "Balanced", "base": "balanced"}, skip_hooks=True)
    else:
        authority.set_field("battery_limit", 80, "Balanced")
    _settle()
    assert seen, "the write must still be recorded"
    assert not any(seen), "recording ran while a hardware lock was held"


@pytest.mark.parametrize("path", ["apply", "set_field"])
def test_r5_hardware_write_does_not_wait_for_a_stalled_timeline_writer(path, profiles_env, fake_hw, monkeypatch):
    gate = threading.Event()

    def stalled(_entry, **_kw):
        gate.wait(5.0)

    monkeypatch.setattr(timeline, "append", stalled)
    monkeypatch.setattr(profiles, "_apply_steps", lambda *a, **k: [("profile", True, "ok")])
    _write_store(profiles_env)
    t0 = time.monotonic()
    try:
        if path == "apply":
            profiles.apply({"name": "Silent", "base": "quiet"},
                           previous={"name": "Balanced", "base": "balanced"}, skip_hooks=True)
        else:
            authority.set_field("battery_limit", 80, "Balanced")
        elapsed = time.monotonic() - t0
    finally:
        gate.set()
    _settle()
    assert elapsed < 2.0, f"the hardware write waited {elapsed:.1f}s for the timeline"


def test_r5_decky_max_fans_does_not_wait_for_a_stalled_timeline_writer(dm, monkeypatch):
    gate = threading.Event()

    def stalled(_entry, **_kw):
        gate.wait(5.0)

    monkeypatch.setattr(timeline, "append", stalled)
    monkeypatch.setattr(authority, "set_max_fans", lambda enabled: "Max Fans ON")
    plugin = dm.Plugin()
    t0 = time.monotonic()
    try:
        res = asyncio.run(plugin.set_max_fans(True))
        elapsed = time.monotonic() - t0
    finally:
        gate.set()
    _settle()
    assert res["ok"] is True
    assert elapsed < 2.0, f"Decky Max Fans waited {elapsed:.1f}s for the timeline"


# ----------------------------------------------------------------------
# P1: bounded fire-and-forget recorder
# ----------------------------------------------------------------------

def test_r5_queue_full_drops_records_instead_of_blocking(tmp_path, monkeypatch):
    _require("submit", "dropped_count")
    gate = threading.Event()

    def stalled(_entry, **_kw):
        gate.wait(10.0)

    monkeypatch.setattr(timeline, "append", stalled)
    p = str(tmp_path / "timeline.jsonl")
    dropped_before = timeline.dropped_count()
    results = []

    def burst():
        for i in range(400):
            results.append(timeline.submit({"kind": "profile", "summary": f"e{i}"}, path=p, journal=False))

    t = threading.Thread(target=burst, daemon=True)
    t.start()
    t.join(timeout=5)
    try:
        assert not t.is_alive(), "submit blocked once the queue was full"
        assert len(results) == 400
        falses = results.count(False)
        assert falses >= 100, f"only {falses} of 400 submissions were dropped; the queue is not bounded"
        assert timeline.dropped_count() - dropped_before == falses, "every drop must be counted"
    finally:
        gate.set()
        _settle()


def test_r5_submit_records_an_immutable_copy(tmp_path):
    _require("submit", "flush")
    p = tmp_path / "timeline.jsonl"
    rec = {"kind": "profile", "summary": "before edit"}
    assert timeline.submit(rec, path=str(p), journal=False) is True
    rec["summary"] = "edited after submit"
    _settle()
    assert [e["summary"] for e in _entries(p)] == ["before edit"]


# ----------------------------------------------------------------------
# P1/P3: Decky startup order
# ----------------------------------------------------------------------

def test_r5_main_schedules_the_watchdog_before_any_timeline_work(main, monkeypatch):
    order = []
    steamos_ran = threading.Event()

    class _Guard:
        def __init__(self, *_a, **_k):
            pass

        def start(self):
            return self

        def close(self):
            pass

    class _Stream:
        def __init__(self, *_a, **_k):
            pass

        def start(self):
            return self

    async def _done():
        return None

    def fake_enforce():
        order.append("watchdog")
        return _done()

    def fake_desktop():
        return _done()

    def fake_steamos():
        order.append("steamos")
        steamos_ran.set()
        return None

    monkeypatch.setattr(main, "BackendMemoryGuard", _Guard)
    monkeypatch.setattr(main, "StateStream", _Stream)
    monkeypatch.setattr(main.crash_report, "install", lambda **_k: None)
    monkeypatch.setattr(main, "_load_max_fans", lambda: {"enabled": False})
    monkeypatch.setattr(main, "_check_steamos_build_id", fake_steamos)

    plugin = main.Plugin()
    plugin._enforce_max_fans = fake_enforce
    plugin._watch_desktop_app = fake_desktop
    asyncio.run(plugin._main())

    assert steamos_ran.wait(5), "the SteamOS build-id check never ran"
    assert "watchdog" in order, "_main must schedule the fan watchdog"
    assert order.index("watchdog") < order.index("steamos"), \
        f"the watchdog must be scheduled before any timeline work, got {order}"


# ----------------------------------------------------------------------
# P2: Decky Max Fans "before" snapshot
# ----------------------------------------------------------------------

def test_r5_max_fans_before_snapshot_is_read_only_and_lock_free(dm, monkeypatch, tmp_path):
    cfg = tmp_path / "config" / "z13-helper"
    cfg.mkdir(parents=True, exist_ok=True)
    # A legacy seed exists: a migrating read would create the shared state from it.
    (cfg / "config.json").write_text(json.dumps({"fans_max": True, "fan_curve_saved": [[40, 20]]}))

    events, written = [], {}
    real_load, real_lock = dm._load_max_fans, dm._file_lock

    def spy_load():
        if "at" not in written:
            events.append("load_max_fans")
        return real_load()

    def spy_lock(path):
        if "at" not in written:
            events.append(("lock", os.path.basename(str(path))))
        return real_lock(path)

    def fake_set(enabled):
        written["at"] = True
        written["max_fans_file_existed"] = os.path.exists(dm.MAX_FANS_STATE)
        written["store_existed"] = os.path.exists(dm.PROFILES_STORE)
        return "Max Fans ON"

    monkeypatch.setattr(dm, "_load_max_fans", spy_load)
    monkeypatch.setattr(dm, "_file_lock", spy_lock)
    monkeypatch.setattr(authority, "set_max_fans", fake_set)

    assert dm._set_max_fans_recorded(True) == "Max Fans ON"
    assert events == [], "the before snapshot must not migrate, lock or load shared state"
    assert written["max_fans_file_existed"] is False, "the before snapshot created the Max Fans state file"
    assert written["store_existed"] is False, "the before snapshot created the profile store"


# ----------------------------------------------------------------------
# P1/F4: directory handling
# ----------------------------------------------------------------------

def test_r5_ensure_dir_closes_every_intermediate_descriptor(tmp_path, monkeypatch):
    target = str(tmp_path / "a" / "b" / "c")
    opened, closed = collections.Counter(), collections.Counter()
    real_open, real_close = os.open, os.close

    def counting_open(*a, **k):
        fd = real_open(*a, **k)
        opened[fd] += 1
        return fd

    def counting_close(fd):
        closed[fd] += 1
        return real_close(fd)

    with monkeypatch.context() as mp:
        mp.setattr(os, "open", counting_open)
        mp.setattr(os, "close", counting_close)
        held = timeline._ensure_dir(target)

    outstanding = collections.Counter(opened)
    outstanding.subtract(closed)
    if isinstance(held, int):
        outstanding[held] -= 1      # the one descriptor the design hands back
        real_close(held)
    leaked = {fd: n for fd, n in outstanding.items() if n > 0}
    assert leaked == {}, f"_ensure_dir opened descriptors it never closed: {leaked}"


def test_r5_dir_fd_append_and_rotation_use_directory_descriptors(tmp_path, monkeypatch):
    target = str(tmp_path / "state" / "timeline.jsonl")
    monkeypatch.setattr(timeline, "INSTALL_JSON", str(tmp_path / "install.json"))
    unscoped = []

    def is_tl(p):
        return isinstance(p, (str, bytes)) and os.path.basename(os.fsdecode(p)).startswith("timeline.jsonl")

    def scoped_spy(name, real):
        def spy(path, *a, **k):
            if is_tl(path) and k.get("dir_fd") is None:
                unscoped.append((name, os.path.basename(os.fsdecode(path))))
            return real(path, *a, **k)
        return spy

    def rename_spy(name, real):
        def spy(src, dst, *a, **k):
            if is_tl(src) and k.get("src_dir_fd") is None:
                unscoped.append((name, os.path.basename(os.fsdecode(src))))
            if is_tl(dst) and k.get("dst_dir_fd") is None:
                unscoped.append((name, os.path.basename(os.fsdecode(dst))))
            return real(src, dst, *a, **k)
        return spy

    with monkeypatch.context() as mp:
        mp.setattr(os, "open", scoped_spy("open", os.open))
        mp.setattr(os, "stat", scoped_spy("stat", os.stat))
        mp.setattr(os, "lstat", scoped_spy("lstat", os.lstat))
        mp.setattr(os, "unlink", scoped_spy("unlink", os.unlink))
        mp.setattr(os, "rename", rename_spy("rename", os.rename))
        mp.setattr(os, "replace", rename_spy("replace", os.replace))
        # Each normalized line is about 340 bytes: one fits a 400-byte segment, two do not.
        for i in range(2):
            timeline.append({"id": f"e{i}", "kind": "profile", "summary": "s" * 150},
                            path=target, max_bytes=400, backups=2, journal=False)

    assert os.path.exists(target + ".1"), "the second append must have rotated the segment"
    assert unscoped == [], f"timeline files touched without a directory descriptor: {unscoped}"


# ----------------------------------------------------------------------
# P3: special files never block the reader
# ----------------------------------------------------------------------

def test_r5_fifo_segment_is_skipped_without_blocking(tmp_path):
    base = tmp_path / "timeline.jsonl"
    good = {"id": "ok-1", "kind": "profile", "summary": "fine", "ts": TS}
    base.write_text(json.dumps(good) + "\n")
    fifo = tmp_path / "timeline.jsonl.1"
    os.mkfifo(str(fifo))

    out = {}

    def reader():
        out["entries"] = timeline.read_entries(str(base))

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        t.join(timeout=3)
        assert not t.is_alive(), "reading a FIFO segment blocked the reader"
        assert out["entries"] == [good]
    finally:
        if t.is_alive():
            # Release the blocked open so the daemon thread can finish.
            os.close(os.open(str(fifo), os.O_RDWR))
            t.join(timeout=5)


# ----------------------------------------------------------------------
# P2/F10: one owner, one record
# ----------------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("battery_limit", 80),
    ("tdp", {"pl1": 45, "pl2": 55, "pl3": 55, "force": False}),
])
def test_r5_single_record_decky_write_is_recorded_once_when_rotation_keeps_the_count(
        dm, profiles_env, fake_hw, monkeypatch, field, value):
    _write_store(profiles_env)
    tl = timeline.TIMELINE_PATH
    os.makedirs(os.path.dirname(tl), exist_ok=True)
    # Rotation drops the one old record in .3 and the padded active segment
    # moves to .1, so the entry count stays the same across the write.
    with open(tl + ".3", "w", encoding="utf-8") as f:
        f.write(_line("old-1"))
    with open(tl, "w", encoding="utf-8") as f:
        f.write(_line("pad", pad_to=timeline.MAX_BYTES - 40))
    monkeypatch.setattr(dm, "_live_battery_limit", lambda: 60)

    result = dm._edit_field_recorded(field, value, "Balanced")
    assert result["ok"] is True
    _settle()
    assert os.path.exists(tl + ".1"), "the write must have rotated the segment"
    kinds = [e["kind"] for e in _entries(tl)]
    assert kinds.count(field) == 1, f"expected one {field} record, got kinds {kinds}"


# ----------------------------------------------------------------------
# P2: unknown previous value
# ----------------------------------------------------------------------

@pytest.mark.parametrize("kind,after", [
    ("battery_limit", {"battery_limit": 80}),
    ("brightness", {"brightness": "high"}),
    ("tdp", {"tdp": {"pl1": 45, "pl2": 55, "pl3": 55, "force": False}}),
])
def test_r5_unknown_previous_value_records_without_rollback(tmp_path, kind, after):
    p = tmp_path / "timeline.jsonl"
    rec = timeline.record_setting_change(kind=kind, summary=f"{kind} changed",
                                         before={kind: None}, after=after, ok=True, path=str(p))
    assert rec["rollback"] is None, "a None before-value is unknown, not a restorable state"
    assert rec["no_rollback_reason"], "the entry must say why it cannot be rolled back"
    assert _entries(p)[0]["rollback"] is None


# ----------------------------------------------------------------------
# P3: swallowed recording failures are logged once, boundedly
# ----------------------------------------------------------------------

def test_r5_warning_failed_profile_record_logs_one_bounded_warning(profiles_env, monkeypatch, caplog):
    monkeypatch.setattr(profiles, "_apply_steps", lambda *a, **k: [("profile", True, "ok")])
    _break_recording(monkeypatch)
    _write_store(profiles_env)
    with caplog.at_level(logging.WARNING):
        results = profiles.apply({"name": "Silent", "base": "quiet"},
                                 previous={"name": "Balanced", "base": "balanced"}, skip_hooks=True)
    assert results == [("profile", True, "ok")], "the hardware result must be unchanged"
    warns = _timeline_warnings(caplog)
    assert len(warns) == 1, f"expected exactly one timeline warning, got {warns}"
    assert "RuntimeError" in warns[0]
    assert len(warns[0]) < 300


def test_r5_warning_failed_decky_max_fans_record_logs_one_bounded_warning(dm, monkeypatch, caplog):
    monkeypatch.setattr(authority, "set_max_fans", lambda enabled: "Max Fans ON")
    _break_recording(monkeypatch)
    with caplog.at_level(logging.WARNING):
        msg = dm._set_max_fans_recorded(True)
    assert msg == "Max Fans ON", "the hardware result must be unchanged"
    warns = _timeline_warnings(caplog)
    assert len(warns) == 1, f"expected exactly one timeline warning, got {warns}"
    assert "RuntimeError" in warns[0]
    assert len(warns[0]) < 300
