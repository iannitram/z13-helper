"""Phase 15a (#123): pure goal decision function, with recorded traces.

No hardware, no Qt, no clock: every test passes `now_s` explicitly.
"""

import math
import random

import pytest

import goal_control as gc
import power_range

RANGES = {"pl1": (28, 80), "pl2": (32, 92), "pl3": (45, 93)}
CURVE = [[20, 0], [30, 10], [40, 20], [50, 30], [60, 40], [70, 50], [80, 60], [90, 70]]
TDP = {"pl1": 30, "pl2": 40, "pl3": 50}
CURRENT = {"tdp": TDP, "fan_curve": CURVE}
COOL = {"apu_temp_c": 60.0}
FULL = [[t, 100] for t in gc.MAX_FAN_TEMPS]
SHED = {"pl1": 28, "pl2": 35, "pl3": 45}  # one 5 W step down, pl1 clamped to its 28 W floor


def _ordered(tdp):
    return tdp["pl2"] >= tdp["pl1"] and tdp["pl3"] >= tdp["pl2"]


def _in_bounds(tdp):
    return all(RANGES[r][0] <= tdp[r] <= RANGES[r][1] for r in gc.RAILS)


# ---- each goal moves in the right direction -------------------------------

def test_fps_below_target_raises_power_and_fans():
    out = gc.step({"kind": "fps", "target_fps": 60}, {"fps": 50}, CURRENT,
                  RANGES, now_s=100.0)
    assert out["tdp"]["pl1"] == 35 and out["tdp"]["pl2"] == 45 and out["tdp"]["pl3"] == 55
    assert all(new[1] >= old[1] for new, old in zip(out["fan_curve"], CURVE))
    assert any(new[1] > old[1] for new, old in zip(out["fan_curve"], CURVE))
    assert out["state"]["last_change_at"] == 100.0


def test_fps_above_target_lowers_power_and_fans():
    out = gc.step({"kind": "fps", "target_fps": 60}, {"fps": 75}, CURRENT,
                  RANGES, now_s=100.0)
    assert out["tdp"]["pl1"] == RANGES["pl1"][0]  # 30 W minus one 5 W step, clamped to the 28 W floor
    assert out["tdp"]["pl2"] == 35 and out["tdp"]["pl3"] == 45
    assert all(new[1] <= old[1] for new, old in zip(out["fan_curve"], CURVE))


def test_fps_within_band_is_unchanged():
    out = gc.step({"kind": "fps", "target_fps": 60}, {"fps": 61}, CURRENT,
                  RANGES, now_s=100.0)
    assert out["tdp"] == TDP and out["fan_curve"] == CURVE
    assert "hysteresis" in out["reason"]


def test_comfort_hot_lowers_power_and_raises_fans():
    out = gc.step({"kind": "comfort", "target_c": 40}, {"comfort_temp_c": 45}, CURRENT,
                  RANGES, now_s=100.0)
    assert out["tdp"]["pl1"] == RANGES["pl1"][0]
    assert all(new[1] >= old[1] for new, old in zip(out["fan_curve"], CURVE))


def test_comfort_cool_raises_power():
    out = gc.step({"kind": "comfort", "target_c": 40}, {"comfort_temp_c": 35}, CURRENT,
                  RANGES, now_s=100.0)
    assert out["tdp"]["pl1"] == 35


def test_quiet_above_ceiling_lowers_power_and_fans():
    out = gc.step({"kind": "quiet", "max_rpm": 2500}, {"fan_rpm": 3200}, CURRENT,
                  RANGES, now_s=100.0)
    assert out["tdp"]["pl1"] == RANGES["pl1"][0]
    assert all(new[1] <= old[1] for new, old in zip(out["fan_curve"], CURVE))


def test_quiet_below_ceiling_is_unchanged():
    out = gc.step({"kind": "quiet", "max_rpm": 2500}, {"fan_rpm": 1500}, CURRENT,
                  RANGES, now_s=100.0)
    assert out["tdp"] == TDP
    assert "below its ceiling" in out["reason"]


# ---- Max -------------------------------------------------------------------

def test_max_requests_full_curve_and_never_lowers_power():
    out = gc.step({"kind": "max"}, COOL, {"tdp": TDP, "fan_curve": CURVE},
                  RANGES, now_s=100.0)
    assert out["fan_curve"] == [[t, 100] for t in gc.MAX_FAN_TEMPS]
    assert all(out["tdp"][r] >= TDP[r] for r in gc.RAILS)
    assert out["tdp"]["pl1"] == TDP["pl1"] + gc.MAX_STEP_W


def test_max_respects_dwell_for_power_but_still_sets_curve():
    out = gc.step({"kind": "max"}, COOL, CURRENT, RANGES, now_s=100.0,
                  state={"last_change_at": 98.0})
    assert out["tdp"] == TDP
    assert out["fan_curve"] == [[t, 100] for t in gc.MAX_FAN_TEMPS]
    assert "dwell" in out["reason"]


def test_max_at_ceiling_keeps_full_curve_without_power_change():
    top = {"pl1": 80, "pl2": 92, "pl3": 93}
    out = gc.step({"kind": "max"}, COOL, {"tdp": top, "fan_curve": [[t, 100] for t in gc.MAX_FAN_TEMPS]},
                  RANGES, now_s=100.0)
    assert out["tdp"] == top
    assert "last_change_at" not in out["state"]


# ---- missing input, dwell and invalid shapes -------------------------------

@pytest.mark.parametrize("goal,readings", [
    ({"kind": "fps", "target_fps": 60}, {}),
    ({"kind": "fps", "target_fps": 60}, {"fps": None}),
    ({"kind": "comfort", "target_c": 40}, {"fan_rpm": 2000}),
    ({"kind": "quiet", "max_rpm": 2500}, {}),
])
def test_missing_reading_returns_current_setpoints_with_reason(goal, readings):
    out = gc.step(goal, readings, CURRENT, RANGES, now_s=100.0)
    assert out["tdp"] == TDP and out["fan_curve"] == CURVE
    assert out["reason"].startswith("Waiting for ")


def test_dwell_blocks_a_second_change_until_ten_seconds_pass():
    goal = {"kind": "fps", "target_fps": 60}
    blocked = gc.step(goal, {"fps": 50}, CURRENT, RANGES, now_s=105.0,
                      state={"last_change_at": 100.0})
    assert blocked["tdp"] == TDP and "dwell" in blocked["reason"]
    allowed = gc.step(goal, {"fps": 50}, CURRENT, RANGES, now_s=110.0,
                      state={"last_change_at": 100.0})
    assert allowed["tdp"]["pl1"] == 35


@pytest.mark.parametrize("goal", [
    {"kind": "nope"}, None, "fps", {"kind": "fps", "target_fps": True},
    {"kind": "fps", "target_fps": float("nan")}, {"kind": "quiet"},
    {"kind": "comfort", "target_c": -1},
])
def test_malformed_goal_holds_power_without_raising(goal):
    out = gc.step(goal, {"fps": 50}, CURRENT, RANGES, now_s=0.0)
    assert out["tdp"] == TDP and out["fan_curve"] == CURVE
    assert out["reason"] == "Malformed goal; holding power"


@pytest.mark.parametrize("goal", [
    {"kind": "nope"}, None, "fps", {"kind": "fps", "target_fps": True},
    {"kind": "quiet"}, {"kind": "comfort", "target_c": -1},
])
def test_malformed_goal_still_sheds_when_hot(goal):
    out = gc.step(goal, {"apu_temp_c": 99.0}, CURRENT, RANGES, now_s=0.0)
    assert out["reason"] == gc.OVER_LIMIT_REASON
    assert out["tdp"] == SHED
    assert all(n[1] >= o[1] for n, o in zip(out["fan_curve"], CURVE))
    assert out["fan_curve"] != CURVE


@pytest.mark.parametrize("huge", [10**400, -(10**400)])
def test_huge_int_reading_is_invalid_not_an_error(huge):
    assert gc._reading(huge) is None
    assert not gc._finite_number(huge)
    out = gc.step({"kind": "fps", "target_fps": 60}, {"fps": huge}, CURRENT, RANGES, now_s=0.0)
    assert out["tdp"] == TDP and out["reason"] == "Waiting for fps reading"
    out = gc.step({"kind": "max"}, {"apu_temp_c": huge}, CURRENT, RANGES, now_s=0.0)
    assert out["tdp"] == TDP and out["fan_curve"] == FULL
    out = gc.step({"kind": "fps", "target_fps": huge}, {"fps": 50}, CURRENT, RANGES, now_s=0.0)
    assert out["reason"] == "Malformed goal; holding power"


def test_default_limit_is_documented_as_unsourced():
    src = open(gc.__file__).read()
    assert gc.APU_TEMP_LIMIT_C == 95.0
    assert "Nothing in this repo\n# documents 95 C" in src


def test_bool_reading_is_missing_not_an_error():
    out = gc.step({"kind": "fps", "target_fps": 60}, {"fps": True}, CURRENT, RANGES, now_s=0.0)
    assert out["tdp"] == TDP and out["fan_curve"] == CURVE
    assert out["reason"] == "Waiting for fps reading"


def test_bad_bounds_raise():
    with pytest.raises(ValueError):
        gc.step({"kind": "max"}, {}, CURRENT, {"pl1": (28, 80), "pl2": (32, 92)}, now_s=0.0)
    with pytest.raises(ValueError):
        gc.step({"kind": "max"}, {}, CURRENT, {**RANGES, "pl2": (90, 92), "pl3": (45, 60)}, now_s=0.0)


def test_bad_curve_raises():
    with pytest.raises(ValueError):
        gc.step({"kind": "max"}, {}, {"tdp": TDP, "fan_curve": CURVE[:7]}, RANGES, now_s=0.0)
    with pytest.raises(ValueError):
        gc.step({"kind": "max"}, {}, {"tdp": TDP, "fan_curve": [[20, 50]] + CURVE[1:]},
                RANGES, now_s=0.0)


# ---- invariants over many inputs ------------------------------------------

def test_output_stays_in_bounds_and_ordered_for_random_inputs():
    rng = random.Random(15)
    goals = [{"kind": "quiet", "max_rpm": 2400}, {"kind": "comfort", "target_c": 42},
             {"kind": "fps", "target_fps": 60}, {"kind": "max"}]
    readings = {"quiet": "fan_rpm", "comfort": "comfort_temp_c", "fps": "fps"}
    for _ in range(500):
        goal = rng.choice(goals)
        tdp = {r: rng.randint(RANGES[r][0], RANGES[r][1]) for r in gc.RAILS}
        if not _ordered(tdp):
            tdp = {"pl1": min(tdp.values()), "pl2": sorted(tdp.values())[1], "pl3": max(tdp.values())}
        tdp = {r: min(max(v, RANGES[r][0]), RANGES[r][1]) for r, v in tdp.items()}
        if not _ordered(tdp):
            continue
        curve = sorted(CURVE[:], key=lambda p: p[0])
        reading = {}
        if goal["kind"] in readings:
            reading[readings[goal["kind"]]] = rng.uniform(0, 120)
        if rng.random() < 0.7:
            reading["apu_temp_c"] = rng.choice([rng.uniform(0, 110), float("nan"), -3, None])
        out = gc.step(goal, reading, {"tdp": tdp, "fan_curve": curve}, RANGES,
                      now_s=rng.uniform(0, 1000), state={"last_change_at": None})
        assert _in_bounds(out["tdp"]), (goal, tdp, out)
        assert _ordered(out["tdp"]), (goal, tdp, out)
        assert all(0 <= p <= 100 for _, p in out["fan_curve"])
        assert all(out["fan_curve"][i][1] <= out["fan_curve"][i + 1][1]
                   for i in range(len(out["fan_curve"]) - 1))


def test_step_size_is_bounded_per_tick():
    out = gc.step({"kind": "fps", "target_fps": 60}, {"fps": 10}, CURRENT, RANGES, now_s=0.0)
    for r in gc.RAILS:
        assert abs(out["tdp"][r] - TDP[r]) <= gc.MAX_STEP_W


# ---- recorded traces -------------------------------------------------------

def test_trace_hold_fps_converges_without_oscillation():
    """Plant: fps = 20 + pl1. Target 60 needs pl1 = 40. Power moves 5 W per tick."""
    goal = {"kind": "fps", "target_fps": 60}
    tdp, state, now = dict(TDP), {}, 0.0
    trace = []
    for _ in range(10):
        fps = 20 + tdp["pl1"]
        out = gc.step(goal, {"fps": fps}, {"tdp": tdp, "fan_curve": CURVE}, RANGES,
                      now_s=now, state=state)
        trace.append(out["tdp"]["pl1"])
        tdp, state = out["tdp"], out["state"]
        now += gc.MIN_DWELL_S
    assert trace == [35, 40, 40, 40, 40, 40, 40, 40, 40, 40]


def test_trace_constant_input_never_oscillates():
    """A constant reading that stays outside the band must move one way only."""
    goal = {"kind": "comfort", "target_c": 40}
    tdp, state, now = dict(TDP), {}, 0.0
    pl1 = []
    for _ in range(20):
        out = gc.step(goal, {"comfort_temp_c": 60}, {"tdp": tdp, "fan_curve": CURVE},
                      RANGES, now_s=now, state=state)
        tdp, state = out["tdp"], out["state"]
        pl1.append(tdp["pl1"])
        now += gc.MIN_DWELL_S
    assert pl1 == sorted(pl1, reverse=True)
    assert pl1[-1] == RANGES["pl1"][0]


def test_trace_max_climbs_to_ceiling_in_bounded_steps():
    tdp, state, now = dict(TDP), {}, 0.0
    seen = []
    for _ in range(20):
        out = gc.step({"kind": "max"}, COOL, {"tdp": tdp, "fan_curve": CURVE}, RANGES,
                      now_s=now, state=state)
        assert all(out["tdp"][r] - tdp[r] <= gc.MAX_STEP_W for r in gc.RAILS)
        tdp, state = out["tdp"], out["state"]
        seen.append(tdp["pl1"])
        now += gc.MIN_DWELL_S
    assert seen == sorted(seen)
    assert tdp == {"pl1": 80, "pl2": 92, "pl3": 93}


# ---- reading safety (#123 owner decisions) ---------------------------------
#
# 1. A reading is valid only if it is an int or float (not bool), finite and
#    >= 0; anything else is treated as missing and never moves power.
# 2. APU over-temperature is checked first for every goal: shed one power step
#    and step the fans up, regardless of dwell and missing goal readings.
# 3. Max needs a valid apu_temp_c; without one it holds power (fans still full).
# 4. Other goals hold everything when their own reading is missing or invalid.

BAD = [float("nan"), float("inf"), float("-inf"), -5, -0.1, True, False, "60", [60]]
BAD_IDS = ["nan", "inf", "-inf", "neg-int", "neg-float", "true", "false", "string", "list"]
GOALS = [
    ({"kind": "quiet", "max_rpm": 2500}, "fan_rpm"),
    ({"kind": "comfort", "target_c": 40}, "comfort_temp_c"),
    ({"kind": "fps", "target_fps": 60}, "fps"),
]
GOAL_IDS = ["quiet", "comfort", "fps"]


def _goal_reading_in_band(goal, sensor):
    return {"fan_rpm": goal.get("max_rpm"), "comfort_temp_c": goal.get("target_c"),
            "fps": goal.get("target_fps")}[sensor]


@pytest.mark.parametrize("goal,sensor", GOALS, ids=GOAL_IDS)
@pytest.mark.parametrize("bad", BAD, ids=BAD_IDS)
def test_invalid_goal_reading_holds_everything(goal, sensor, bad):
    out = gc.step(goal, {sensor: bad, **COOL}, CURRENT, RANGES, now_s=100.0)
    assert out["tdp"] == TDP and out["fan_curve"] == CURVE
    assert out["reason"] == f"Waiting for {sensor} reading"
    assert "last_change_at" not in out["state"]


@pytest.mark.parametrize("goal,sensor", GOALS, ids=GOAL_IDS)
@pytest.mark.parametrize("bad", BAD, ids=BAD_IDS)
def test_invalid_apu_reading_is_not_hot(goal, sensor, bad):
    # The goal reading is on target, so only the over-limit check could move.
    readings = {sensor: _goal_reading_in_band(goal, sensor), "apu_temp_c": bad}
    out = gc.step(goal, readings, CURRENT, RANGES, now_s=100.0)
    assert out["tdp"] == TDP and out["fan_curve"] == CURVE
    assert out["reason"] != gc.OVER_LIMIT_REASON


def test_zero_reading_is_valid():
    out = gc.step({"kind": "fps", "target_fps": 60}, {"fps": 0}, CURRENT, RANGES, now_s=100.0)
    assert out["tdp"]["pl1"] == 35


@pytest.mark.parametrize("goal,sensor", GOALS, ids=GOAL_IDS)
@pytest.mark.parametrize("readings", [{}, {"fps": None}, {"fan_rpm": float("nan"), "fps": -1,
                                                         "comfort_temp_c": "hot"}],
                         ids=["missing", "none", "all-invalid"])
@pytest.mark.parametrize("apu", [95.0, 95, 101.5])
def test_over_limit_sheds_when_goal_reading_missing_or_invalid(goal, sensor, readings, apu):
    out = gc.step(goal, {**readings, "apu_temp_c": apu}, CURRENT, RANGES, now_s=100.0)
    assert out["reason"] == gc.OVER_LIMIT_REASON
    assert out["tdp"] == SHED
    assert all(new[1] >= old[1] for new, old in zip(out["fan_curve"], CURVE))
    assert any(new[1] > old[1] for new, old in zip(out["fan_curve"], CURVE))
    assert out["state"]["last_change_at"] == 100.0


@pytest.mark.parametrize("goal,readings", [
    ({"kind": "fps", "target_fps": 60}, {"fps": 40}),          # wants more power
    ({"kind": "comfort", "target_c": 40}, {"comfort_temp_c": 30}),  # wants more power
    ({"kind": "quiet", "max_rpm": 2500}, {"fan_rpm": 1500}),    # below ceiling
    ({"kind": "fps", "target_fps": 60}, {"fps": 60}),          # in band
    ({"kind": "max"}, {}),
], ids=["fps-low", "comfort-cool", "quiet-below", "fps-in-band", "max"])
def test_over_limit_sheds_inside_dwell_for_every_goal(goal, readings):
    out = gc.step(goal, {**readings, "apu_temp_c": 96.0}, CURRENT, RANGES, now_s=101.0,
                  state={"last_change_at": 100.0})
    assert out["reason"] == gc.OVER_LIMIT_REASON
    assert out["tdp"] == SHED
    assert all(new[1] >= old[1] for new, old in zip(out["fan_curve"], CURVE))
    assert out["state"]["last_change_at"] == 101.0


def test_over_limit_on_max_keeps_full_fans_and_sheds_power():
    out = gc.step({"kind": "max"}, {"apu_temp_c": 99.0}, {"tdp": TDP, "fan_curve": FULL},
                  RANGES, now_s=100.0)
    assert out["fan_curve"] == FULL and out["tdp"] == SHED
    assert out["reason"] == gc.OVER_LIMIT_REASON


def test_over_limit_at_floor_with_full_fans_stays_put():
    floor = {"pl1": 28, "pl2": 32, "pl3": 45}
    out = gc.step({"kind": "fps", "target_fps": 60}, {"apu_temp_c": 99.0},
                  {"tdp": floor, "fan_curve": FULL}, RANGES, now_s=100.0)
    assert out["tdp"] == floor and out["fan_curve"] == FULL
    assert out["reason"] == gc.OVER_LIMIT_REASON
    assert "last_change_at" not in out["state"]


def test_just_under_limit_is_not_hot():
    out = gc.step({"kind": "fps", "target_fps": 60}, {"fps": 60, "apu_temp_c": 94.9},
                  CURRENT, RANGES, now_s=100.0)
    assert out["tdp"] == TDP and out["reason"] != gc.OVER_LIMIT_REASON


def test_caller_limit_overrides_default():
    readings = {"fps": 60, "apu_temp_c": 85.0}
    assert gc.step({"kind": "fps", "target_fps": 60}, readings, CURRENT, RANGES,
                   now_s=100.0)["tdp"] == TDP
    out = gc.step({"kind": "fps", "target_fps": 60}, readings, CURRENT, RANGES,
                  now_s=100.0, apu_temp_limit_c=85)
    assert out["reason"] == gc.OVER_LIMIT_REASON and out["tdp"] == SHED


@pytest.mark.parametrize("limit", [float("nan"), float("inf"), 0, -5, True, "95", None])
def test_invalid_limit_raises(limit):
    with pytest.raises(ValueError):
        gc.step({"kind": "fps", "target_fps": 60}, {"fps": 60}, CURRENT, RANGES,
                now_s=100.0, apu_temp_limit_c=limit)


@pytest.mark.parametrize("now", [float("nan"), float("inf"), True, None])
def test_invalid_now_raises(now):
    with pytest.raises(ValueError):
        gc.step({"kind": "fps", "target_fps": 60}, {"fps": 50}, CURRENT, RANGES, now_s=now)


@pytest.mark.parametrize("apu", [None, float("nan"), float("inf"), -1, True, "70"],
                         ids=["none", "nan", "inf", "negative", "bool", "string"])
@pytest.mark.parametrize("readings", [{}, {"fps": 30, "fan_rpm": 2000, "comfort_temp_c": 30}],
                         ids=["empty", "other-readings"])
def test_max_holds_power_without_valid_apu_temperature(apu, readings):
    out = gc.step({"kind": "max"}, {**readings, "apu_temp_c": apu}, CURRENT, RANGES,
                  now_s=100.0)
    assert out["tdp"] == TDP
    assert out["fan_curve"] == FULL
    assert out["reason"].startswith("Waiting for apu_temp_c reading")


def test_max_without_apu_temperature_never_climbs_over_a_trace():
    tdp, state, now = dict(TDP), {}, 0.0
    for _ in range(10):
        out = gc.step({"kind": "max"}, {}, {"tdp": tdp, "fan_curve": CURVE}, RANGES,
                      now_s=now, state=state)
        tdp, state = out["tdp"], out["state"]
        now += gc.MIN_DWELL_S
    assert tdp == TDP


def test_bad_readings_never_raise_or_raise_power_across_many_inputs():
    rng = random.Random(123)
    junk = [float("nan"), float("inf"), float("-inf"), -1, -1e9, True, False, "x", None,
            [], {}, object()]
    goals = [g for g, _ in GOALS] + [{"kind": "max"}]
    for _ in range(400):
        goal = rng.choice(goals)
        readings = {k: rng.choice(junk) for k in ("fps", "fan_rpm", "comfort_temp_c", "apu_temp_c")}
        out = gc.step(goal, readings, CURRENT, RANGES, now_s=rng.uniform(0, 1000))
        assert out["tdp"] == TDP, (goal, readings, out)


def test_trace_overheat_sheds_every_tick_until_floor_then_recovers():
    """Plant: hot for 6 ticks one second apart (inside dwell), then cool."""
    goal = {"kind": "fps", "target_fps": 60}
    tdp, state, now = {"pl1": 60, "pl2": 70, "pl3": 80}, {}, 0.0
    pl1 = []
    for tick in range(8):
        apu = 97.0 if tick < 6 else 70.0
        out = gc.step(goal, {"fps": 40, "apu_temp_c": apu}, {"tdp": tdp, "fan_curve": CURVE},
                      RANGES, now_s=now, state=state)
        tdp, state = out["tdp"], out["state"]
        pl1.append(tdp["pl1"])
        now += 1.0
    # Six sheds, one per second; then the normal raise waits for the dwell.
    assert pl1 == [55, 50, 45, 40, 35, 30, 30, 30]
    assert _ordered(tdp) and _in_bounds(tdp)


@pytest.mark.parametrize("ranges", [power_range.DEFAULT_RANGES, power_range.ARMOURY_RANGES],
                         ids=["default", "armoury"])
def test_accepts_power_range_shapes(ranges):
    out = gc.step({"kind": "fps", "target_fps": 60}, {"fps": 50, "apu_temp_c": 70.0},
                  CURRENT, ranges, now_s=100.0)
    assert all(ranges[r][0] <= out["tdp"][r] <= ranges[r][1] for r in gc.RAILS)
    assert _ordered(out["tdp"])


def test_detect_ranges_output_feeds_step(tmp_path):
    # An empty sysfs tree falls back to the documented default map.
    ranges = power_range.detect_ranges(str(tmp_path / "armoury"), str(tmp_path / "nbwmi"))
    out = gc.step({"kind": "max"}, COOL, CURRENT, ranges, now_s=100.0)
    assert _ordered(out["tdp"]) and math.isfinite(out["tdp"]["pl1"])
