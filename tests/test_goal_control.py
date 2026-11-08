"""Phase 15a (#123): pure goal decision function, with recorded traces.

No hardware, no Qt, no clock: every test passes `now_s` explicitly.
"""

import random

import pytest

import goal_control as gc

RANGES = {"pl1": (28, 80), "pl2": (32, 92), "pl3": (45, 93)}
CURVE = [[20, 0], [30, 10], [40, 20], [50, 30], [60, 40], [70, 50], [80, 60], [90, 70]]
TDP = {"pl1": 30, "pl2": 40, "pl3": 50}
CURRENT = {"tdp": TDP, "fan_curve": CURVE}


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
    out = gc.step({"kind": "max"}, {}, {"tdp": TDP, "fan_curve": CURVE},
                  RANGES, now_s=100.0)
    assert out["fan_curve"] == [[t, 100] for t in gc.MAX_FAN_TEMPS]
    assert all(out["tdp"][r] >= TDP[r] for r in gc.RAILS)
    assert out["tdp"]["pl1"] == TDP["pl1"] + gc.MAX_STEP_W


def test_max_respects_dwell_for_power_but_still_sets_curve():
    out = gc.step({"kind": "max"}, {}, CURRENT, RANGES, now_s=100.0,
                  state={"last_change_at": 98.0})
    assert out["tdp"] == TDP
    assert out["fan_curve"] == [[t, 100] for t in gc.MAX_FAN_TEMPS]
    assert "dwell" in out["reason"]


def test_max_at_ceiling_keeps_full_curve_without_power_change():
    top = {"pl1": 80, "pl2": 92, "pl3": 93}
    out = gc.step({"kind": "max"}, {}, {"tdp": top, "fan_curve": [[t, 100] for t in gc.MAX_FAN_TEMPS]},
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
])
def test_invalid_goal_raises(goal):
    with pytest.raises(ValueError):
        gc.step(goal, {"fps": 50}, CURRENT, RANGES, now_s=0.0)


def test_non_numeric_reading_raises():
    with pytest.raises(ValueError):
        gc.step({"kind": "fps", "target_fps": 60}, {"fps": True}, CURRENT, RANGES, now_s=0.0)


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
        out = gc.step({"kind": "max"}, {}, {"tdp": tdp, "fan_curve": CURVE}, RANGES,
                      now_s=now, state=state)
        assert all(out["tdp"][r] - tdp[r] <= gc.MAX_STEP_W for r in gc.RAILS)
        tdp, state = out["tdp"], out["state"]
        seen.append(tdp["pl1"])
        now += gc.MIN_DWELL_S
    assert seen == sorted(seen)
    assert tdp == {"pl1": 80, "pl2": 92, "pl3": 93}
