"""fan_watchdog.FanWatchdog with fake sensors, controller and clock."""

import types

import pytest

from fan_watchdog import FROZEN_S, MAX_RECOVERY_ATTEMPTS, ONE_FAN_ZERO_S, RETRY_S, STALL_HYSTERESIS_C, STALL_TICKS, FanWatchdog
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

    def set_fancurve(self, curve):         # must never be used by the watchdog
        self._call("set_fancurve", curve)

    def reset_fancurve(self):
        self._call("reset_fancurve")


@pytest.fixture
def env(clock):
    """Fake clock, Max Fans flag and may_act gate; build the watchdog with env.make().
    Set ns.allowed = False to have may_act() refuse; ns.asked counts its calls."""
    ns = types.SimpleNamespace(clock=clock, sensors=None, ctl=None, wd=None,
                               max_on=False, allowed=True, asked=0)

    def may_act():
        ns.asked += 1
        return ns.allowed

    def make(max_fans=False, fail_on=None, **sensor_kw):
        ns.max_on = max_fans
        ns.sensors = FakeSensors(**sensor_kw)
        ns.ctl = FakeController(fail_on)
        ns.wd = FanWatchdog(ns.sensors, ns.ctl, max_fans_on=lambda: ns.max_on,
                            may_act=may_act, clock=clock)
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
    ns = env.make(max_fans=False, enabled=False, fans=(0, 0))
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


def test_rule2_brief_one_fan_zero_is_not_a_stall(env):
    # A single fan at 0 is judged by the per-fan rule, which needs ONE_FAN_ZERO_S
    # after the grace; a short dip does nothing.
    ns = env.make(curve=MAX_CURVE, temp=50.0, fans=(0, 4000))
    run(ns, int(ONE_FAN_ZERO_S // TICK) - 1)
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

def jitter(ns, n, frozen=8700, start=6000, heat=0.1):
    """n ticks with fan 1 bit-identical and fan 2 moving, the APU warming
    (a stopped fan under full demand: the corroboration the rule needs)."""
    out = []
    for k in range(n):
        ns.sensors.temp += heat
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
        ns.sensors.temp += 0.1
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
    # One fan at 0 for ages (other spinning) is the per-fan zero rule's case,
    # never reported as a frozen tach.
    ns = env.make(curve=MAX_CURVE, temp=55.0)
    msgs = jitter(ns, int(FROZEN_S // TICK) * 3, frozen=0)
    assert not any(m and "frozen" in m for m in msgs)


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
# One fan stuck at 0 while the other runs (#6); constant RPM needs
# corroboration (#12)
# ----------------------------------------------------------------------

def alternate(ns, n, stuck=0, dt=TICK, heat=0.0):
    """n ticks with fan 1 at `stuck` and fan 2 alternating normally."""
    out = []
    for k in range(n):
        ns.sensors.fans = (stuck, 8000 + (k % 2) * 100)
        ns.sensors.temp += heat
        out.extend(run(ns, 1, dt=dt))
    return out


def test_one_fan_stuck_at_zero_at_high_temp_and_duty_recovers(env):
    ns = env.make(curve=MAX_CURVE, temp=80.0)
    alternate(ns, 101, dt=2.0)                       # 200 simulated seconds
    assert call_names(ns.ctl)[:2] == ["rewrite_profile", "set_fancurve_pwm"]


def test_steady_rpm_with_stable_temps_and_healthy_partner_is_left_alone(env):
    ns = env.make(curve=MAX_CURVE, temp=60.0)
    alternate(ns, int(FROZEN_S // TICK) * 3, stuck=8700)
    assert ns.ctl.calls == []


def test_steady_rpm_with_warming_apu_and_moving_partner_still_recovers(env):
    ns = env.make(curve=MAX_CURVE, temp=60.0)
    alternate(ns, int(FROZEN_S // TICK) + 3, stuck=8700, heat=0.1)
    assert call_names(ns.ctl)[:2] == ["rewrite_profile", "set_fancurve_pwm"]


def test_both_fans_flat_out_while_warming_is_not_corroboration(env):
    # Both tachs constant: a plateau at full speed, not a stopped fan.
    ns = env.make(curve=MAX_CURVE, temp=60.0, fans=(8700, 8700))
    for _ in range(int(FROZEN_S // TICK) * 3):
        ns.sensors.temp += 0.1
        run(ns, 1)
    assert ns.ctl.calls == []


def test_one_fan_zero_waits_out_the_grace_after_our_own_write(env):
    ns = env.make(max_fans=True, enabled=False, curve=MAX_CURVE, temp=80.0)
    run(ns, 1)                                   # rule 1 recovery writes
    ns.sensors.enabled = True
    n_before = len(ns.ctl.calls)
    alternate(ns, int((RETRY_S + ONE_FAN_ZERO_S) // TICK) - 2)
    assert len(ns.ctl.calls) == n_before         # zero not yet judged
    alternate(ns, 6)
    assert len(ns.ctl.calls) == n_before + 2


def test_one_fan_zero_is_not_judged_right_after_a_resume(env):
    ns = env.make(curve=MAX_CURVE, temp=80.0)
    alternate(ns, int(ONE_FAN_ZERO_S // TICK) - 2)
    ns.clock.advance(3600)                       # suspended for an hour
    alternate(ns, int(ONE_FAN_ZERO_S // TICK) - 2)
    assert ns.ctl.calls == []


def test_one_fan_recovery_is_bounded_and_becomes_a_visible_fault(env):
    ns = env.make(curve=MAX_CURVE, temp=80.0)
    assert ns.wd.fault is None
    msgs = alternate(ns, 400)                    # 10 simulated minutes
    assert call_names(ns.ctl).count("rewrite_profile") == MAX_RECOVERY_ATTEMPTS
    assert ns.wd.fault and "fan 1" in ns.wd.fault
    faults = [m for m in msgs if m and m.startswith("Fault:")]
    assert faults == [ns.wd.fault]               # announced once, not every tick
    # One spinning reading does not clear it; sustained moving readings do.
    ns.sensors.fans = (4000, 4100)
    run(ns, 1)
    assert ns.wd.fault
    for k in range(int(ONE_FAN_ZERO_S // TICK) + 2):
        ns.sensors.fans = (4000 + (k % 2) * 100, 8000 + (k % 2) * 100)
        run(ns, 1)
    assert ns.wd.fault is None


def test_both_fans_stalled_is_bounded_too(env):
    ns = env.make(curve=MAX_CURVE, temp=50.0, fans=(0, 0))
    run(ns, 300)
    assert call_names(ns.ctl).count("rewrite_profile") == MAX_RECOVERY_ATTEMPTS
    assert ns.wd.fault and "fan 1 and fan 2" in ns.wd.fault


def test_flapping_fan_that_spins_briefly_after_each_recovery_hits_the_bound(env):
    ns = env.make(curve=MAX_CURVE, temp=80.0)
    spin_until = [None]
    for _ in range(600):                         # 900 simulated seconds
        before = len(ns.ctl.calls)
        fan1 = 0
        if spin_until[0] is not None and ns.clock() < spin_until[0]:
            fan1 = 7000 + (int(ns.clock()) % 2) * 100
        ns.sensors.fans = (fan1, 8000 + (int(ns.clock()) % 2) * 100)
        run(ns, 1)
        if len(ns.ctl.calls) > before:           # a recovery: fan 1 spins ~3 s, then dies
            spin_until[0] = ns.clock() + 3
    assert call_names(ns.ctl).count("rewrite_profile") == MAX_RECOVERY_ATTEMPTS
    assert ns.wd.fault and "fan 1" in ns.wd.fault


def test_fan_holding_its_full_speed_plateau_is_not_frozen(env):
    ns = env.make(curve=MAX_CURVE, temp=60.0)
    for k in range(12):                          # ramps up, jittering, to its plateau
        ns.sensors.fans = (8000 + 100 * min(k, 8), 7000 + (k % 2) * 100)
        run(ns, 1)
    for k in range(400):                         # then holds 8800 for 10 minutes while warming 1 C/min
        ns.sensors.temp += TICK / 60
        ns.sensors.fans = (8800, 7000 + (k % 2) * 100)
        run(ns, 1)
    assert ns.ctl.calls == []
    assert ns.wd.fault is None
