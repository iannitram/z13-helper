"""fan_watchdog.FanTrace: what the watchdog writes for troubleshooting, and how much."""

import logging
import os

from fan_watchdog import FROZEN_S, STALL_TICKS, FanTrace, FanWatchdog
from test_fan_watchdog import MAX_CURVE, TICK, FakeController, FakeSensors


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class TraceSensors(FakeSensors):
    def get_acpi_fan_rpm(self):
        return 8823

    def get_platform_profile(self):
        return "performance"


def make(clock, max_on=False, allowed=True, **sensor_kw):
    logger = logging.getLogger(f"test.fantrace.{id(clock)}")
    logger.propagate = False
    logger.handlers[:] = []
    cap = Capture()
    logger.addHandler(cap)
    logger.setLevel(logging.INFO)
    sensors = TraceSensors(**sensor_kw)
    ctl = FakeController()
    gate = {"allowed": allowed}
    trace = FanTrace(logger, sensors, wall=lambda: 1_760_000_000.0 + clock())
    wd = FanWatchdog(sensors, ctl, max_fans_on=lambda: max_on, may_act=lambda: gate["allowed"],
                     clock=clock, trace=trace)
    return wd, sensors, ctl, cap, gate


def ticks(clock, wd, n, sensors=None, jitter=False):
    for k in range(n):
        clock.advance(TICK)
        if jitter:
            sensors.fans = (6000 + (k % 2) * 100, 6100 - (k % 2) * 100)
        wd.tick()


def samples(cap):
    return [l for l in cap.lines if " temp=" in l and not l.startswith("<")]


def test_steady_state_writes_one_sample_per_30s(clock):
    wd, s, _, cap, _ = make(clock, curve=MAX_CURVE, temp=55.0, fans=(6000, 6100))
    ticks(clock, wd, int(300 / TICK), s, jitter=True)          # 5 minutes
    assert 9 <= len(samples(cap)) <= 11
    assert not any("***" in l for l in cap.lines)


def test_line_has_every_reading(clock):
    wd, s, _, cap, _ = make(clock, max_on=True, curve=MAX_CURVE, temp=55.0, fans=(8700, 8800))
    ticks(clock, wd, 1)
    line = samples(cap)[0]
    for part in ("temp=55.0", "rpm=8700/8800", "acpi=8823", "curve=custom", "want=255",
                 "max=on", "prof=performance", "stall=0", "same=0/0s"):
        assert part in line, (part, line)


def test_auto_curve_is_not_tracked_as_frozen(clock):
    wd, s, _, cap, _ = make(clock, enabled=False, temp=55.0, fans=(3000, 3000))
    ticks(clock, wd, int(200 / TICK))
    assert all("curve=auto" in l and "same=-/-s" in l for l in samples(cap))
    assert len(samples(cap)) <= 8                              # samples only, no suspect run


def test_unchanged_reading_logs_every_tick_from_30s(clock):
    wd, s, _, cap, _ = make(clock, max_on=True, curve=MAX_CURVE, temp=55.0)
    for k in range(int(60 / TICK)):
        s.fans = (8700, 8800 + (k % 2) * 100)                  # fan 1 steady, fan 2 moving
        ticks(clock, wd, 1)
    # From 30 s on, every tick is written (the run-up a frozen-tach trigger would follow).
    assert len(samples(cap)) >= int(30 / TICK) - 1


def test_recovery_dumps_history_and_follows_up(clock):
    wd, s, ctl, cap, _ = make(clock, curve=MAX_CURVE, temp=55.0, fans=(6000, 6100))
    ticks(clock, wd, 20, s, jitter=True)                       # 30 s of normal running
    s.fans = (0, 0)
    ticks(clock, wd, STALL_TICKS)
    acts = [l for l in cap.lines if "*** ACT stall" in l]
    assert len(acts) == 1 and ctl.calls
    assert any("*** RESULT Fans were stalled" in l for l in cap.lines)
    history = [l for l in cap.lines if l.startswith("< ")]
    assert history and all("temp=" in l for l in history)
    n = len(cap.lines)
    s.fans = (5000, 5100)
    ticks(clock, wd, int(60 / TICK) - 2, s, jitter=True)       # the follow-up minute: every tick
    assert len(cap.lines) - n >= int(60 / TICK) - 3


def test_history_lines_are_not_written_twice(clock):
    wd, s, _, cap, _ = make(clock, curve=MAX_CURVE, temp=55.0)
    for k in range(int(FROZEN_S / TICK) + 5):                  # suspect run, then frozen trigger
        s.temp += 0.1                                          # warming: corroborates the freeze
        s.fans = (8700, 6000 + (k % 2) * 100)
        ticks(clock, wd, 1)
    assert any("*** ACT frozen tach: fan 1" in l for l in cap.lines)
    body = [l.lstrip("< ") for l in cap.lines if " temp=" in l]
    assert len(body) == len(set(body))


def test_refused_recovery_logs_hold_once(clock):
    wd, s, ctl, cap, gate = make(clock, allowed=False, curve=MAX_CURVE, temp=55.0, fans=(0, 0))
    ticks(clock, wd, 10)
    assert ctl.calls == []
    assert len([l for l in cap.lines if "*** HOLD stall" in l]) == 1
    gate["allowed"] = True
    ticks(clock, wd, 1)
    assert any("*** ACT stall" in l for l in cap.lines)


def test_trace_failure_never_stops_the_watchdog(clock):
    wd, s, ctl, cap, _ = make(clock, curve=MAX_CURVE, temp=55.0, fans=(0, 0))

    def boom(*a):
        raise RuntimeError("disk full")
    wd.trace.record = boom
    ticks(clock, wd, STALL_TICKS)
    assert ctl.calls


def test_to_file_writes_and_chowns(tmp_path, monkeypatch):
    chowned = []
    monkeypatch.setattr(os, "chown", lambda p, u, g: chowned.append((p, u, g)))
    path = tmp_path / "state" / "fan-trace-gamemode.log"
    trace = FanTrace.to_file(str(path), None, "gamemode-test", owner=(1000, 1000))
    trace.log.info("hello")
    for h in trace.log.handlers:
        h.flush()
    text = path.read_text()
    assert "fan trace started (gamemode-test" in text and "hello" in text
    assert (str(path), 1000, 1000) in chowned
    assert not trace.log.propagate


def test_deferred_recovery_result_is_traced_when_the_worker_finishes(clock):
    from fan_watchdog import FanWatchdogRunner
    wd, s, ctl, cap, _ = make(clock, curve=MAX_CURVE, temp=55.0, fans=(0, 0))
    jobs = []
    runner = FanWatchdogRunner(wd, lambda label, fn: jobs.append((label, fn)))
    for _ in range(STALL_TICKS):
        clock.advance(TICK)
        runner.tick()
    assert len(jobs) == 1 and not any("*** RESULT" in l for l in cap.lines)
    label, fn = jobs[0]
    clock.advance(1.0)
    text = runner.on_done(label, fn())
    results = [l for l in cap.lines if "*** RESULT" in l]
    assert len(results) == 1 and text in results[0]
    assert "*** RESULT Fans were stalled" in results[0]    # same text as the inline path
