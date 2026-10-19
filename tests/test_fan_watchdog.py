"""fan_watchdog.FanWatchdog with fake sensors, controller and clock."""

import math
import types

import pytest

from fan_watchdog import (AUTO_RETRY_S, AUTO_STALL_S, AUTO_STALL_TEMP_C, FROZEN_S, RETRY_S,
                          STALL_HYSTERESIS_C, STALL_TICKS, FanWatchdog)
from sensor_reader import SensorReader

TICK = 1.5   # the app's telemetry period

MAX_CURVE = [[t, 255] for t in (20, 30, 40, 50, 60, 70, 80, 90)]
# 100% from 40 C; idles below.
HIGH_FROM_40 = [[t, 255] for t in (40, 50, 60, 70, 75, 80, 85, 90)]
# Never asks for 50% below 80 C.
LOW_CURVE = [[40, 20], [50, 40], [60, 60], [70, 100], [75, 110], [80, 140], [85, 200], [90, 255]]


class FakeSensors(SensorReader):
    """Real expected_fan_pwm() (inherited) over fake readings."""

    def __init__(self, curve=MAX_CURVE, enabled=True, temp=45.0, fans=(0, 0)):
        super().__init__()
        self.curve, self.enabled, self.temp, self.fans = curve, enabled, temp, fans

    def custom_fan_curve_enabled(self):
        return self.enabled

    def custom_fan_curve_points(self):
        return [list(p) for p in self.curve] if self.curve else None

    def get_apu_temp(self):
        return self.temp

    def get_fan_speeds(self):
        return self.fans


class FakeController:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if name == self.fail_on:
            raise RuntimeError(f"z13ctl command failed: {name}")

    def rewrite_profile(self):
        self._call("rewrite_profile")

    def set_fans_max(self):
        self._call("set_fans_max")

    def set_fancurve_pwm(self, points):
        self._call("set_fancurve_pwm", points)

    def apply_tdp(self, tdp):
        self._call("apply_tdp", tdp)

    def set_fancurve(self, curve):         # must never be used by the watchdog
        self._call("set_fancurve", curve)

    def reset_fancurve(self):
        self._call("reset_fancurve")


@pytest.fixture
def env(clock):
    """Fake clock, Max Fans flag and may_act gate; build the watchdog with env.make().
    Set ns.allowed = False to have may_act() refuse; ns.asked counts its calls."""
    ns = types.SimpleNamespace(clock=clock, sensors=None, ctl=None, wd=None,
                               max_on=False, allowed=True, asked=0, tdp=None)

    def may_act():
        ns.asked += 1
        return ns.allowed

    def make(max_fans=False, fail_on=None, tdp=None, **sensor_kw):
        ns.max_on, ns.tdp = max_fans, tdp
        ns.sensors = FakeSensors(**sensor_kw)
        ns.ctl = FakeController(fail_on)
        ns.wd = FanWatchdog(ns.sensors, ns.ctl, max_fans_on=lambda: ns.max_on,
                            may_act=may_act, active_tdp=lambda: ns.tdp, clock=clock)
        return ns

    def set_max(v):
        ns.max_on = v

    ns.make, ns.set_max = make, set_max
    return ns


def run(ns, n, dt=TICK):
    """n ticks, advancing the clock by dt before each; returns the messages."""
    out = []
    for _ in range(n):
        ns.clock.advance(dt)
        out.append(ns.wd.tick())
    return out


def call_names(ctl):
    return [c[0] for c in ctl.calls]


def assert_recovery_order(ctl):
    """Every recovery = rewrite_profile THEN a curve write; never a bare curve write."""
    names = call_names(ctl)
    assert names, "expected a recovery"
    for i, n in enumerate(names):
        if n in ("set_fans_max", "set_fancurve_pwm"):
            assert i > 0 and names[i - 1] == "rewrite_profile", names
    assert "set_fancurve" not in names and "reset_fancurve" not in names


# ----------------------------------------------------------------------
# Rule 1: Max Fans ON, curve dropped
# ----------------------------------------------------------------------

def test_rule1_reasserts_max_fans_with_profile_rewrite(env):
    ns = env.make(max_fans=True, enabled=False, fans=(2400, 2500))
    msg = run(ns, 1)[0]
    assert call_names(ns.ctl) == ["rewrite_profile", "set_fans_max"]
    assert "Max Fans re-applied after a profile change" in msg


def test_rule1_throttled_then_retried_after_retry_s(env):
    ns = env.make(max_fans=True, enabled=False)
    run(ns, 1)
    assert run(ns, 1, dt=RETRY_S - 1) == [None]
    assert len(ns.ctl.calls) == 2
    run(ns, 1, dt=1.01)
    assert call_names(ns.ctl) == ["rewrite_profile", "set_fans_max"] * 2
    assert_recovery_order(ns.ctl)


def test_dropped_curve_with_max_fans_off_is_left_alone(env):
    # Max Fans OFF + auto mode = the profile wants firmware auto (or the app will fix it).
    # Idle fans on a cool APU are firmware auto doing its job (rule 4 needs it hot).
    ns = env.make(max_fans=False, enabled=False, temp=45.0, fans=(0, 0))
    assert run(ns, 10) == [None] * 10
    assert ns.ctl.calls == []


def test_unknown_curve_state_is_left_alone(env):
    ns = env.make(max_fans=True, enabled=None, fans=(0, 0))
    assert run(ns, 10) == [None] * 10
    assert ns.ctl.calls == []


# ----------------------------------------------------------------------
# Rule 2: stalled curve (both fans 0 RPM while the curve asks >= 50%)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("max_fans,expected_calls", [
    (False, ["rewrite_profile", "set_fancurve_pwm"]),
    (True, ["rewrite_profile", "set_fans_max"]),
])
def test_rule2_stall_recovers_after_stall_ticks(env, max_fans, expected_calls):
    ns = env.make(max_fans=max_fans, curve=MAX_CURVE, temp=39.0, fans=(0, 0))
    msgs = run(ns, STALL_TICKS)
    assert msgs[:-1] == [None] * (STALL_TICKS - 1)
    assert msgs[-1] == "Fans were stalled at 0 RPM; profile and curve re-applied"
    assert call_names(ns.ctl) == expected_calls
    if not max_fans:
        assert ns.ctl.calls[1][1] == MAX_CURVE        # the sysfs curve, as raw PWM


def test_rule2_counter_resets_when_a_fan_spins(env):
    ns = env.make(curve=MAX_CURVE, temp=50.0, fans=(0, 0))
    for _ in range(5):
        run(ns, STALL_TICKS - 1)
        ns.sensors.fans = (3000, 3100)
        run(ns, 1)
        ns.sensors.fans = (0, 0)
    assert ns.ctl.calls == []


def test_rule2_one_fan_at_zero_is_not_a_stall(env):
    ns = env.make(curve=MAX_CURVE, temp=50.0, fans=(0, 4000))
    run(ns, 20)
    assert ns.ctl.calls == []


def test_rule2_throttle(env):
    ns = env.make(curve=MAX_CURVE, temp=50.0, fans=(0, 0))
    run(ns, STALL_TICKS)
    assert len(ns.ctl.calls) == 2
    # Still stalled: no second attempt inside RETRY_S...
    n_within = int((RETRY_S - 0.01) // TICK)
    assert run(ns, n_within) == [None] * n_within
    assert len(ns.ctl.calls) == 2
    # ...but one once RETRY_S has passed.
    run(ns, 2)
    assert len(ns.ctl.calls) == 4
    assert_recovery_order(ns.ctl)


def test_low_curve_demand_does_not_trigger(env):
    # At 65 C the curve asks 60/255 (< 50%): 0 RPM can be legit.
    ns = env.make(curve=LOW_CURVE, temp=65.0, fans=(0, 0))
    run(ns, 20)
    assert ns.ctl.calls == []


@pytest.mark.parametrize("temp", [30.0, 39.9, 40.0, 42.9])
def test_idle_below_first_point_plus_hysteresis_does_not_trigger(env, temp):
    ns = env.make(curve=HIGH_FROM_40, temp=temp, fans=(0, 0))
    run(ns, 20)
    assert ns.ctl.calls == []


def test_stall_at_first_point_plus_hysteresis_triggers(env):
    ns = env.make(curve=HIGH_FROM_40, temp=40.0 + STALL_HYSTERESIS_C, fans=(0, 0))
    run(ns, STALL_TICKS)
    assert call_names(ns.ctl) == ["rewrite_profile", "set_fancurve_pwm"]


@pytest.mark.parametrize("temp,fans", [(None, (0, 0)), (50.0, (None, 0)), (50.0, (0, None))])
def test_missing_readings_do_nothing(env, temp, fans):
    ns = env.make(curve=MAX_CURVE, temp=temp, fans=fans)
    assert run(ns, 10) == [None] * 10
    assert ns.ctl.calls == []


def test_no_readable_curve_does_nothing(env):
    ns = env.make(curve=None, temp=80.0, fans=(0, 0))
    run(ns, 10)
    assert ns.ctl.calls == []


# ----------------------------------------------------------------------
# Rule 3: frozen tach
# ----------------------------------------------------------------------

def jitter(ns, n, frozen=8700, start=6000):
    """n ticks with fan 1 bit-identical and fan 2 moving."""
    out = []
    for k in range(n):
        ns.sensors.fans = (frozen, start + (k % 2) * 100)
        out.extend(run(ns, 1))
    return out


def test_rule3_frozen_fan_triggers_after_frozen_s(env):
    ns = env.make(curve=MAX_CURVE, temp=55.0, fans=(8700, 6000))
    ticks_before = int(FROZEN_S // TICK)            # first tick records the value
    msgs = jitter(ns, ticks_before)
    assert ns.ctl.calls == [] and set(msgs) == {None}
    msg = jitter(ns, 1)[0]
    assert msg and "Fan 1 reading frozen at 8700 RPM for 90s" in msg
    assert call_names(ns.ctl) == ["rewrite_profile", "set_fancurve_pwm"]


def test_rule3_frozen_second_fan(env):
    ns = env.make(curve=MAX_CURVE, temp=55.0)
    msgs = []
    for k in range(int(FROZEN_S // TICK) + 2):
        ns.sensors.fans = (6000 + (k % 2) * 100, 5100)
        msgs.extend(run(ns, 1))
    assert any(m and m.startswith("Fan 2 reading frozen at 5100") for m in msgs)


def test_rule3_timer_restarts_after_recovery(env):
    ns = env.make(max_fans=True, curve=MAX_CURVE, temp=55.0)
    jitter(ns, int(FROZEN_S // TICK) + 1)
    assert call_names(ns.ctl) == ["rewrite_profile", "set_fans_max"]
    # Still frozen right after: needs another full FROZEN_S, not just RETRY_S.
    jitter(ns, int(FROZEN_S // TICK) - 1)
    assert len(ns.ctl.calls) == 2
    jitter(ns, 3)
    assert call_names(ns.ctl) == ["rewrite_profile", "set_fans_max"] * 2
    assert_recovery_order(ns.ctl)


def test_rule3_needs_high_curve_demand(env):
    # LOW_CURVE at 65 C asks 60/255: a steady low-speed reading is plausible.
    ns = env.make(curve=LOW_CURVE, temp=65.0)
    jitter(ns, int(FROZEN_S // TICK) * 3, frozen=1200)
    assert ns.ctl.calls == []


def test_rule3_zero_rpm_is_not_frozen(env):
    # One fan at 0 for ages (other spinning) is not a frozen tach.
    ns = env.make(curve=MAX_CURVE, temp=55.0)
    jitter(ns, int(FROZEN_S // TICK) * 3, frozen=0)
    assert ns.ctl.calls == []


def test_rule3_jittering_fans_never_trigger(env):
    ns = env.make(curve=MAX_CURVE, temp=55.0)
    for k in range(200):
        ns.sensors.fans = (8600 + (k % 3) * 100, 8500 + (k % 2) * 100)
        run(ns, 1)
    assert ns.ctl.calls == []


def test_rule3_respects_throttle(env):
    ns = env.make(max_fans=True, enabled=False, curve=MAX_CURVE, temp=55.0, fans=(8700, 6000))
    run(ns, 1)                                       # rule 1 recovery at t0
    assert len(ns.ctl.calls) == 2
    ns.sensors.enabled = True
    # Long-frozen fan 1, but only RETRY_S/2 since the last recovery.
    ns.wd._last_rpm = [8700, 6000]
    ns.wd._rpm_changed_at = [ns.clock() - FROZEN_S - 10] * 2
    ns.clock.advance(RETRY_S / 2)
    ns.sensors.fans = (8700, 6100)
    assert ns.wd.tick() is None
    assert len(ns.ctl.calls) == 2


# ----------------------------------------------------------------------
# _recover
# ----------------------------------------------------------------------

def test_recover_failure_is_reported_not_raised(env):
    ns = env.make(curve=MAX_CURVE, temp=50.0, fans=(0, 0), fail_on="rewrite_profile")
    msg = run(ns, STALL_TICKS)[-1]
    assert msg.startswith("Fan recovery failed:")
    assert call_names(ns.ctl) == ["rewrite_profile"]     # no bare curve write after a failed rewrite


def test_recover_without_curve_only_rewrites_profile(env):
    ns = env.make(curve=None)
    msg = ns.wd._recover(False)
    assert "no curve to restore" in msg
    assert call_names(ns.ctl) == ["rewrite_profile"]


def test_recover_reads_curve_before_rewriting_profile(env):
    """The profile write drops the custom curve in sysfs, so the snapshot must come first."""
    ns = env.make(curve=MAX_CURVE)
    order = []
    orig_points = ns.sensors.custom_fan_curve_points
    ns.sensors.custom_fan_curve_points = lambda: (order.append("read"), orig_points())[1]
    orig_rewrite = ns.ctl.rewrite_profile
    ns.ctl.rewrite_profile = lambda: (order.append("rewrite"), orig_rewrite())[1]
    ns.wd._recover(False)
    assert order == ["read", "rewrite"]


# ----------------------------------------------------------------------
# may_act gate (desktop: no apply pending; Decky: Game Mode)
# ----------------------------------------------------------------------

def test_may_act_is_only_asked_when_a_recovery_is_due(env):
    ns = env.make(curve=MAX_CURVE, temp=55.0, fans=(6000, 6100))
    for k in range(50):
        ns.sensors.fans = (6000 + (k % 2) * 100, 6100 - (k % 2) * 100)
        run(ns, 1)
    assert ns.asked == 0


def test_refused_stall_recovers_on_first_allowed_tick(env):
    ns = env.make(curve=MAX_CURVE, temp=50.0, fans=(0, 0))
    ns.allowed = False
    assert run(ns, STALL_TICKS + 5) == [None] * (STALL_TICKS + 5)
    assert ns.ctl.calls == []
    ns.allowed = True
    # No retry gap started by the refusals, and the stall count was kept.
    assert run(ns, 1)[0] == "Fans were stalled at 0 RPM; profile and curve re-applied"
    assert call_names(ns.ctl) == ["rewrite_profile", "set_fancurve_pwm"]


def test_refused_dropped_curve_recovers_on_first_allowed_tick(env):
    ns = env.make(max_fans=True, enabled=False)
    ns.allowed = False
    run(ns, 3)
    assert ns.ctl.calls == []
    ns.allowed = True
    assert "Max Fans re-applied" in run(ns, 1)[0]


def test_frozen_timer_keeps_running_while_refused(env):
    ns = env.make(curve=MAX_CURVE, temp=55.0, fans=(8700, 6000))
    ns.allowed = False
    jitter(ns, int(FROZEN_S // TICK) + 5)
    assert ns.ctl.calls == []
    ns.allowed = True
    msg = jitter(ns, 1)[0]
    assert msg and "Fan 1 reading frozen" in msg


def test_throttle_is_checked_before_may_act(env):
    ns = env.make(curve=MAX_CURVE, temp=50.0, fans=(0, 0))
    run(ns, STALL_TICKS)
    asked = ns.asked
    run(ns, 2)                    # inside RETRY_S: throttled, gate not consulted
    assert ns.asked == asked


def test_max_fans_flag_is_read_every_tick(env):
    ns = env.make(max_fans=False, enabled=False)
    run(ns, 2)
    assert ns.ctl.calls == []
    ns.set_max(True)
    run(ns, 1)
    assert call_names(ns.ctl) == ["rewrite_profile", "set_fans_max"]


# ----------------------------------------------------------------------
# Rule 4: firmware auto mode, both fans stopped on a hot APU
# ----------------------------------------------------------------------

BOONDOCK_TDP = {"pl1": 20, "pl2": 30, "pl3": 40, "force": False}
# The streak starts on the first 0-RPM tick, so it reaches AUTO_STALL_S this many ticks in.
AUTO_TICKS = math.ceil(AUTO_STALL_S / TICK) + 1


def auto(env, **kw):
    kw.setdefault("temp", 70.0)
    kw.setdefault("fans", (0, 0))
    return env.make(max_fans=False, enabled=False, **kw)


def test_rule4_auto_stall_rewrites_profile_then_restores_tdp(env):
    # 2026-10-19: quiet profile, APU 69 -> 75 C, both fans 0 RPM in auto.
    ns = auto(env, tdp=BOONDOCK_TDP)
    msgs = run(ns, AUTO_TICKS)
    assert msgs[:-1] == [None] * (AUTO_TICKS - 1)
    # tdp --set re-applies z13ctl's stored curve, so the reset comes last.
    assert ns.ctl.calls == [("rewrite_profile",), ("apply_tdp", BOONDOCK_TDP), ("reset_fancurve",)]
    assert msgs[-1] == ("Fans stopped in auto mode at 70 °C; profile rewritten, "
                        "TDP 20/30/40 W re-applied")


def test_rule4_without_tdp_only_rewrites_profile(env):
    ns = auto(env, tdp=None)
    msgs = run(ns, AUTO_TICKS)
    assert call_names(ns.ctl) == ["rewrite_profile"]
    assert msgs[-1] == "Fans stopped in auto mode at 70 °C; profile rewritten"


def test_rule4_waits_the_full_auto_stall_s(env):
    ns = auto(env)
    run(ns, 1)                              # streak starts here
    run(ns, 1, dt=AUTO_STALL_S - 0.01)
    assert ns.ctl.calls == []
    run(ns, 1, dt=0.02)
    assert call_names(ns.ctl) == ["rewrite_profile"]


@pytest.mark.parametrize("temp", [40.0, 60.0, AUTO_STALL_TEMP_C - 0.1])
def test_rule4_cool_apu_is_left_alone(env, temp):
    ns = auto(env, temp=temp)
    assert run(ns, 100) == [None] * 100
    assert ns.ctl.calls == []


def test_rule4_one_fan_spinning_is_not_a_stall(env):
    ns = auto(env, fans=(0, 2400))
    run(ns, 100)
    assert ns.ctl.calls == []


def test_rule4_streak_resets_when_a_fan_spins_or_apu_cools(env):
    ns = auto(env)
    for breaker in ({"fans": (2400, 2500)}, {"temp": 50.0}, {"temp": None}, {"fans": (None, 0)}):
        run(ns, AUTO_TICKS - 1)
        saved = {k: getattr(ns.sensors, k) for k in breaker}
        for k, v in breaker.items():
            setattr(ns.sensors, k, v)
        run(ns, 1)
        for k, v in saved.items():
            setattr(ns.sensors, k, v)
    assert ns.ctl.calls == []


def test_rule4_rate_limited_to_auto_retry_s(env):
    ns = auto(env, tdp=BOONDOCK_TDP)
    run(ns, AUTO_TICKS)
    assert len(ns.ctl.calls) == 3
    # Still stopped: no second attempt inside AUTO_RETRY_S...
    n_within = int((AUTO_RETRY_S - 0.01) // TICK)
    assert run(ns, n_within) == [None] * n_within
    assert len(ns.ctl.calls) == 3
    # ...but one after it (the streak kept running meanwhile).
    run(ns, 2)
    assert call_names(ns.ctl) == ["rewrite_profile", "apply_tdp", "reset_fancurve"] * 2


def test_rule4_respects_may_act_and_acts_once_allowed(env):
    ns = auto(env)
    ns.allowed = False
    assert run(ns, AUTO_TICKS + 10) == [None] * (AUTO_TICKS + 10)
    assert ns.ctl.calls == []
    ns.allowed = True
    run(ns, 1)
    assert call_names(ns.ctl) == ["rewrite_profile"]


def test_rule4_failed_rewrite_skips_tdp(env):
    ns = auto(env, tdp=BOONDOCK_TDP, fail_on="rewrite_profile")
    msg = run(ns, AUTO_TICKS)[-1]
    assert call_names(ns.ctl) == ["rewrite_profile"]
    assert msg.startswith("Fan recovery failed:")


def test_rule4_failed_tdp_is_reported(env):
    ns = auto(env, tdp=BOONDOCK_TDP, fail_on="apply_tdp")
    msg = run(ns, AUTO_TICKS)[-1]
    assert call_names(ns.ctl) == ["rewrite_profile", "apply_tdp"]
    assert "profile rewritten, but TDP re-apply failed" in msg


def test_rule4_unreadable_store_is_reported_not_raised(env):
    ns = auto(env)
    def boom():
        raise OSError("profiles.json unreadable")
    ns.wd._active_tdp = boom
    msg = run(ns, AUTO_TICKS)[-1]
    assert call_names(ns.ctl) == ["rewrite_profile"]
    assert "TDP re-apply failed: profiles.json unreadable" in msg


def test_rule4_never_writes_a_curve_or_reruns_hooks(env):
    ns = auto(env, tdp=BOONDOCK_TDP)
    run(ns, AUTO_TICKS + int(AUTO_RETRY_S // TICK) + 2)
    assert set(call_names(ns.ctl)) == {"rewrite_profile", "apply_tdp", "reset_fancurve"}


def test_rule4_does_not_apply_with_max_fans_on(env):
    # Max Fans ON + auto is rule 1 (re-pin 100%), never the TDP path.
    ns = env.make(max_fans=True, enabled=False, temp=80.0, fans=(0, 0), tdp=BOONDOCK_TDP)
    run(ns, AUTO_TICKS)
    assert "apply_tdp" not in call_names(ns.ctl)
    assert call_names(ns.ctl)[:2] == ["rewrite_profile", "set_fans_max"]
