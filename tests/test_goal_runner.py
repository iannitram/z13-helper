"""goal_runner.GoalRunner (#125) with fake writers, sensors, FPS source and clock.

The lease is the real one in state_authority (inside profiles_env's tmp_path),
so single-writer and handover go through the #23 state owner as on the device.
Every hardware write goes to FakeWriter; nothing here reaches z13ctl.
"""

import contextlib
import json
import os
import types

import pytest

import goal_control
import goal_runner
import state_authority
from goal_runner import GoalRunner, GoalRunnerDriver, GOAL_RUNNER_LABEL

RANGES = {"pl1": (5, 93), "pl2": (5, 93), "pl3": (5, 93)}
CURVE = [[30, 10], [40, 20], [50, 30], [60, 40], [70, 60], [80, 80], [90, 90], [100, 100]]
TDP = {"pl1": 40, "pl2": 50, "pl3": 60}
DWELL = goal_control.MIN_DWELL_S


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class FakeWriter:
    def __init__(self):
        self.calls = []
        self.fail = {}   # "tdp" / "fan_curve" -> problem text

    def tdp(self, tdp):
        self.calls.append(("tdp", dict(tdp)))
        return self.fail.get("tdp")

    def fan_curve(self, points):
        self.calls.append(("fan_curve", [list(p) for p in points]))
        return self.fail.get("fan_curve")


def profile(name="Quiet goal", goal=None, tdp=TDP, curve=CURVE):
    p = {"name": name, "base": "balanced"}
    if goal is not None:
        p["goal"] = goal
    if tdp is not None:
        p["tdp"] = dict(tdp)
    if curve is not None:
        p["fan_curve"] = [list(x) for x in curve]
    return p


QUIET = {"kind": "quiet", "max_rpm": 3000}
FPS = {"kind": "fps", "target_fps": 60}


@pytest.fixture
def env(profiles_env, monkeypatch):
    ns = types.SimpleNamespace(clock=Clock(), active=profile(goal=QUIET),
                               readings={"apu_temp_c": 60.0, "fan_rpm": 4000.0},
                               fps=None)
    # The lease's heartbeats use the fake clock too.
    monkeypatch.setattr(state_authority, "_now", ns.clock)

    def make(owner="desktop", may_run=lambda: True, writer=None):
        w = writer or FakeWriter()
        r = GoalRunner(owner, readings=lambda: dict(ns.readings), fps=lambda: ns.fps,
                       writer=w, ranges=lambda: RANGES, active_profile=lambda: ns.active,
                       may_run=may_run, clock=ns.clock)
        return r, w

    ns.make = make
    return ns


def lease():
    path = state_authority._goal_lease_path()
    with open(path) as f:
        return json.load(f)


def setpoint_now(ns):
    return state_authority.goal_setpoint(now=ns.clock())


# ----------------------------------------------------------------------
# Writes, dwell and readings
# ----------------------------------------------------------------------

def test_quiet_over_ceiling_writes_tdp_then_curve(env):
    r, w = env.make()
    msg = r.tick()
    assert [c[0] for c in w.calls] == ["tdp", "fan_curve"]   # principle 4: TDP before curve
    assert w.calls[0][1] == {"pl1": 35, "pl2": 45, "pl3": 55}
    assert msg.startswith("Goal: ")
    assert r.active
    assert setpoint_now(env)["tdp"] == {"pl1": 35, "pl2": 45, "pl3": 55}


def test_at_most_one_write_per_dwell(env):
    r, w = env.make()
    r.tick()
    for _ in range(int(DWELL) - 1):
        env.clock.advance(1)
        r.tick()
    assert len([c for c in w.calls if c[0] == "tdp"]) == 1
    env.clock.advance(1)
    r.tick()
    assert len([c for c in w.calls if c[0] == "tdp"]) == 2


def test_over_temperature_shed_also_waits_for_dwell(env):
    """goal_control sheds on every tick it applies; the runner still caps writes."""
    env.readings = {"apu_temp_c": 99.0, "fan_rpm": 1000.0}
    r, w = env.make()
    r.tick()
    env.clock.advance(DWELL / 2)
    r.tick()
    assert len([c for c in w.calls if c[0] == "tdp"]) == 1


def test_missing_fps_reading_holds_power(env):
    env.active = profile(goal=FPS)
    env.fps = None
    r, w = env.make()
    r.tick()
    assert w.calls == []
    assert "Waiting for fps" in r.last_reason


@pytest.mark.parametrize("bad", [float("nan"), "60", True, -1])
def test_bad_fps_values_never_move_power(env, bad):
    env.active = profile(goal=FPS)
    env.fps = bad
    r, w = env.make()
    r.tick()
    assert w.calls == []


def test_fps_source_that_raises_is_a_missing_reading(env):
    env.active = profile(goal=FPS)
    r, w = env.make()
    r._fps = lambda: (_ for _ in ()).throw(OSError("gamescope gone"))
    r.tick()
    assert w.calls == []


def test_fps_below_target_raises_power(env):
    env.active = profile(goal=FPS)
    env.fps = 40.0
    r, w = env.make()
    r.tick()
    assert w.calls[0] == ("tdp", {"pl1": 45, "pl2": 55, "pl3": 65})


def test_no_change_writes_nothing(env):
    env.readings = {"apu_temp_c": 60.0, "fan_rpm": 3000.0}   # inside the band
    r, w = env.make()
    r.tick()
    assert w.calls == []
    assert r.active   # still holds the lease


def test_sensor_readings_use_hwmon_reads_only():
    class S:
        def get_cpu_temp(self):
            return (71.5, "k10temp")

        def get_fan_speeds(self):
            return (2900, 3100)

        def __getattr__(self, name):   # anything else (an Armoury read) fails the test
            raise AssertionError(f"unexpected sensor read {name}")

    got = goal_runner.sensor_readings(S())
    assert got == {"apu_temp_c": 71.5, "fan_rpm": 3100, "comfort_temp_c": None}


def test_profile_without_fixed_tdp_holds_without_lease(env):
    env.active = profile(goal=QUIET, tdp=None)
    r, w = env.make()
    msg = r.tick()
    assert "needs a fixed TDP" in msg
    assert w.calls == [] and not r.active
    assert setpoint_now(env) is None


def test_force_flag_of_profile_tdp_is_kept(env):
    env.active = profile(goal=QUIET, tdp=dict(TDP, force=True))
    r, w = env.make()
    r.tick()
    assert w.calls[0][1]["force"] is True


# ----------------------------------------------------------------------
# Stop on a goal-less profile
# ----------------------------------------------------------------------

def test_switch_to_goal_less_profile_stops_runner_and_releases(env):
    r, w = env.make()
    r.tick()
    n = len(w.calls)
    env.active = profile(name="Balanced")          # no goal
    env.clock.advance(DWELL * 3)
    msg = r.tick()
    assert "no goal" in msg
    assert len(w.calls) == n                        # the profile apply writes, not the runner
    assert not r.active
    assert setpoint_now(env) is None
    env.clock.advance(DWELL * 3)
    assert r.tick() is None and len(w.calls) == n


def test_profile_change_starts_from_new_profile_values(env):
    r, w = env.make()
    r.tick()
    env.active = profile(name="Other quiet", goal=QUIET, tdp={"pl1": 20, "pl2": 25, "pl3": 30})
    env.clock.advance(1)          # a new goal is a new start: no dwell carried over
    r.tick()
    assert w.calls[-2] == ("tdp", {"pl1": 15, "pl2": 20, "pl3": 25})


# ----------------------------------------------------------------------
# Single writer and handover through the state owner
# ----------------------------------------------------------------------

def test_only_one_runner_writes(env):
    desk, wd = env.make("desktop")
    deck, wk = env.make("decky")
    desk.tick()
    for _ in range(5):
        env.clock.advance(DWELL)
        deck.tick()
        desk.tick()
    assert wd.calls and wk.calls == []
    assert lease()["owner"] == "desktop"


def test_handover_desktop_to_game_mode_continues_setpoint(env):
    mode = {"game": False}
    desk, wd = env.make("desktop", may_run=lambda: not mode["game"])
    deck, wk = env.make("decky", may_run=lambda: mode["game"])
    desk.tick()
    reached = desk.setpoint["tdp"]
    mode["game"] = True
    env.clock.advance(1)
    assert desk.tick() == "Goal runner handed over"
    assert setpoint_now(env) is None               # nobody holds it between ticks
    deck.tick()
    assert lease()["owner"] == "decky"
    assert deck.setpoint["tdp"] == reached          # continued, not reset to the profile
    assert wk.calls == []                            # the dwell spans the handover
    env.clock.advance(DWELL)
    deck.tick()
    assert wk.calls[0][1] == {r: reached[r] - 5 for r in reached}


def test_dead_holder_is_replaced_after_ttl(env):
    desk, wd = env.make("desktop")
    deck, wk = env.make("decky")
    desk.tick()                                    # then the desktop app dies
    env.clock.advance(state_authority.GOAL_LEASE_TTL_S - 1)
    deck.tick()
    assert wk.calls == []
    env.clock.advance(2)
    deck.tick()
    assert lease()["owner"] == "decky" and wk.calls


def test_lease_lost_mid_run_stops_writes(env):
    desk, wd = env.make("desktop")
    desk.tick()
    n = len(wd.calls)
    state_authority.goal_lease_release("desktop")
    assert state_authority.goal_lease_acquire("decky", env.clock()) is not None
    env.clock.advance(DWELL)
    desk.tick()
    assert len(wd.calls) == n


def test_stop_releases_lease(env):
    r, _ = env.make()
    r.tick()
    r.stop()
    assert lease()["owner"] is None and setpoint_now(env) is None


def test_lease_from_before_a_reboot_is_stale(env):
    state_authority._write_goal_lease({"owner": "decky", "heartbeat": env.clock() + 5000,
                                       "setpoint": None})
    r, w = env.make("desktop")
    r.tick()
    assert lease()["owner"] == "desktop" and w.calls


# ----------------------------------------------------------------------
# Failed and timed-out z13ctl writes (#79)
# ----------------------------------------------------------------------

def test_failed_tdp_write_keeps_setpoint_and_waits_a_dwell(env):
    w = FakeWriter()
    w.fail["tdp"] = "z13ctl command failed: busy"
    r, _ = env.make(writer=w)
    msg = r.tick()
    assert "TDP write failed" in msg
    assert [c[0] for c in w.calls] == ["tdp"]       # no curve after a failed TDP
    assert r.setpoint["tdp"] == TDP
    env.clock.advance(1)
    r.tick()
    assert len(w.calls) == 1                         # not hammered
    env.clock.advance(DWELL)
    r.tick()
    assert len(w.calls) == 2


def test_failed_curve_write_keeps_written_tdp(env):
    w = FakeWriter()
    w.fail["fan_curve"] = "z13ctl command failed"
    r, _ = env.make(writer=w)
    r.tick()
    assert r.setpoint["tdp"] == {"pl1": 35, "pl2": 45, "pl3": 55}
    assert r.setpoint["fan_curve"] == CURVE


def test_timed_out_write_suspends_until_profile_changes(env):
    w = FakeWriter()
    w.fail["tdp"] = "z13ctl write timed out after 20 s (kernel asus-wmi hang?)"
    r, _ = env.make(writer=w)
    msg = r.tick()
    assert "suspended" in msg and r.suspended
    assert setpoint_now(env) is None                 # recovery is not pinned to it
    w.fail.clear()
    for _ in range(5):
        env.clock.advance(DWELL)
        r.tick()
    assert len(w.calls) == 1
    env.active = profile(name="Quiet 2", goal=QUIET)  # a profile apply re-establishes state
    env.clock.advance(DWELL)
    r.tick()
    assert len(w.calls) == 3 and not r.suspended


# ----------------------------------------------------------------------
# Desktop driver and the slider gate
# ----------------------------------------------------------------------

def test_driver_queues_one_tick_at_a_time(env):
    r, w = env.make()
    jobs = []
    d = GoalRunnerDriver(r, lambda label, fn: jobs.append((label, fn)))
    d.tick()
    d.tick()
    assert len(jobs) == 1 and jobs[0][0] == GOAL_RUNNER_LABEL and d.pending
    assert d.on_done("Fan recovery", "x") is None and d.pending
    msg = d.on_done(GOAL_RUNNER_LABEL, jobs[0][1]())
    assert msg.startswith("Goal: ") and not d.pending
    d.tick()
    assert len(jobs) == 2


def test_driver_reports_worker_exception(env):
    r, _ = env.make()
    d = GoalRunnerDriver(r, lambda label, fn: None)
    d.tick()
    assert d.on_done(GOAL_RUNNER_LABEL, RuntimeError("boom")) == "Goal runner error: boom"


def test_slider_push_is_saved_not_written_while_runner_holds_lease(env, monkeypatch):
    r, _ = env.make()
    r.tick()
    import hardware_controller

    class NoWrites:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            raise AssertionError(f"slider wrote {name} while a goal runner held the lease")

    monkeypatch.setattr(hardware_controller, "HardwareController", NoWrites)
    p = profile(goal=QUIET, tdp={"pl1": 30, "pl2": 30, "pl3": 30})
    for field in ("tdp", "fan_curve"):
        assert "goal runner" in state_authority._push_field(p, field)


def test_recover_performance_overlays_goal_setpoint(env, monkeypatch):
    seen = {}

    class Client:
        def get_state(self):
            return {"profile": "x"}

        def activate_app_profile(self, p):
            seen.update(p)
            return "x"

    import profiles
    monkeypatch.setattr(state_authority, "named_client", lambda: Client())
    monkeypatch.setattr(profiles, "sync_active_from_backend", lambda state: None)
    monkeypatch.setattr(profiles, "active_name", lambda create=True: "Quiet goal")
    monkeypatch.setattr(profiles, "get_profile", lambda name, store=None: profile(goal=QUIET))
    monkeypatch.setattr(profiles, "apply_epp", lambda p: [])
    sp = {"tdp": {"pl1": 20, "pl2": 25, "pl3": 30}, "fan_curve": CURVE}
    assert "re-applied" in state_authority.recover_performance(setpoint=sp)
    assert seen["tdp"] == {"pl1": 20, "pl2": 25, "pl3": 30, "force": False}
    assert seen["fan_curve"] == CURVE
