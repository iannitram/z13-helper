import pytest

import goal_control as g

CUR = g.Setpoints(spl_w=30, sppt_w=40, fppt_w=50, fan_pct=50)


def sp(spl, sppt, fppt, fan):
    return g.Setpoints(spl, sppt, fppt, fan)


def run(goal, r, cur=CUR, state=g.State(), now=100.0, limits=g.Limits()):
    return g.decide(goal, r, cur, state, now, limits)


def test_custom_never_changes():
    d = run(g.Goal("custom"), g.Readings(fan_rpm=9999, apu_temp_c=99))
    assert not d.changed and d.setpoints == CUR


def test_unknown_goal():
    with pytest.raises(ValueError):
        run(g.Goal("turbo"), g.Readings())


def test_quiet_over_ceiling_lowers_curve_by_one_step():
    d = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=4500))
    assert d.changed and d.setpoints == sp(30, 40, 50, 40)


def test_quiet_at_curve_floor_lowers_all_rails():
    d = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=4500), sp(30, 40, 50, 20))
    assert d.setpoints == sp(27, 37, 47, 20)


def test_quiet_hysteresis_band_holds():
    assert not run(g.Goal("quiet", 3000), g.Readings(fan_rpm=2800)).changed


def test_quiet_headroom_raises_power():
    d = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=1000))
    assert d.setpoints == sp(33, 43, 53, 60)


# --- defect 1: three rails, per-rail ranges, ordering ---

RANGES = g.Limits(ranges={"pl1": (28, 80), "pl2": (32, 92), "pl3": (45, 93)})


def test_each_rail_uses_its_own_range():
    d = run(g.Goal("max"), g.Readings(apu_temp_c=70), sp(79, 91, 92, 50), limits=RANGES)
    assert d.setpoints == sp(80, 92, 93, 60)
    d = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=4500), sp(29, 33, 46, 20), limits=RANGES)
    assert d.setpoints == sp(28, 32, 45, 20)


@pytest.mark.parametrize("ranges,current,expected", [
    ({"pl1": (30, 80), "pl2": (20, 92), "pl3": (40, 93)},
     sp(30, 30, 40, 20), sp(30, 30, 40, 20)),
    ({"pl1": (30, 80), "pl2": (20, 92), "pl3": (40, 93)},
     sp(31, 32, 44, 20), sp(30, 30, 41, 20)),
    ({"pl1": (20, 80), "pl2": (40, 92), "pl3": (30, 93)},
     sp(30, 40, 40, 20), sp(27, 40, 40, 20)),
])
def test_downward_step_preserves_rail_minima(ranges, current, expected):
    d = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=4500), current,
            limits=g.Limits(ranges=ranges))
    assert d.setpoints == expected


def test_rail_ordering_enforced():
    # sPPT's range floor must not drop below SPL, fPPT not below sPPT.
    lim = g.Limits(ranges={"pl1": (5, 93), "pl2": (5, 93), "pl3": (5, 93)})
    d = run(g.Goal("max"), g.Readings(apu_temp_c=70), sp(40, 30, 20, 50), limits=lim)
    s = d.setpoints
    assert s.spl_w <= s.sppt_w <= s.fppt_w


def test_ordering_after_unequal_clamping():
    lim = g.Limits(ranges={"pl1": (5, 93), "pl2": (5, 50), "pl3": (5, 93)})
    d = run(g.Goal("max"), g.Readings(apu_temp_c=70), sp(49, 49, 49, 50), limits=lim)
    s = d.setpoints
    assert s.spl_w <= s.sppt_w <= s.fppt_w
    lo, hi = lim.ranges["pl2"]
    assert lo <= s.sppt_w <= hi


# --- defect 2: max curve is full speed everywhere ---

def test_curve_points():
    pts = g.curve_points(100)
    assert [t for t, _ in pts] == list(g.CURVE_TEMPS)
    assert all(v == 100 for _, v in pts)
    ramp = g.curve_points(60)
    assert ramp[0][1] == 20 and ramp[-1][1] == 60
    assert all(p[1] <= q[1] for p, q in zip(ramp, ramp[1:]))
    assert g.curve_points(5)[-1][1] == 20


# --- defect 3: dwell does not block Max fans ---

def test_max_steps_fans_during_dwell():
    st = g.State(last_change_at=100.0)
    d = run(g.Goal("max"), g.Readings(apu_temp_c=70), CUR, st, 101.0)
    assert d.changed and d.setpoints.fan_pct == 60
    assert (d.setpoints.spl_w, d.setpoints.sppt_w, d.setpoints.fppt_w) == (30, 40, 50)
    assert d.state == st


def test_max_wins_to_top():
    d = run(g.Goal("max"), g.Readings(fan_rpm=0))
    assert d.setpoints == sp(33, 43, 53, 60)


@pytest.mark.parametrize("state", [g.State(), g.State(last_change_at=99.0)])
def test_max_holds_when_all_readings_missing(state):
    d = run(g.Goal("max"), g.Readings(), state=state)
    assert not d.changed and d.setpoints == CUR
    assert d.state == state
    assert d.reason == "no change: missing reading"


@pytest.mark.parametrize("fan", [20, 50, 95, 100])
@pytest.mark.parametrize("state", [g.State(), g.State(last_change_at=99.0)])
def test_max_fan_step_is_bounded(fan, state):
    d = run(g.Goal("max"), g.Readings(apu_temp_c=70),
            sp(30, 40, 50, fan), state=state)
    assert d.setpoints.fan_pct == min(fan + g.MAX_FAN_STEP_PCT, 100)


# --- defect 4: dwell holds above the temperature limit too ---

def test_temperature_limit_respects_dwell():
    st = g.State(last_change_at=100.0)
    goal, r = g.Goal("fps", 60), g.Readings(fps=40, apu_temp_c=96)
    d = g.decide(goal, r, CUR, st, 101.0)
    assert not d.changed and d.setpoints == CUR and d.reason == "minimum dwell"
    d = g.decide(goal, r, CUR, st, 120.5)
    assert d.changed and d.setpoints == sp(27, 37, 47, 60)


def test_temperature_limit_sheds_power_for_max():
    d = run(g.Goal("max"), g.Readings(apu_temp_c=96))
    assert d.setpoints == sp(27, 37, 47, 60)


# --- defect 5: missing reading holds even when hot ---

def test_missing_goal_reading_holds_even_when_hot():
    for goal in (g.Goal("fps", 60), g.Goal("quiet", 3000), g.Goal("comfort", 38)):
        d = run(goal, g.Readings(apu_temp_c=96))
        assert not d.changed and d.setpoints == CUR


def test_missing_target_holds():
    assert not run(g.Goal("fps"), g.Readings(fps=10)).changed


def test_comfort_warm_and_cool_and_band():
    goal = g.Goal("comfort", 38)
    assert run(goal, g.Readings(comfort_temp_c=42)).setpoints == sp(27, 37, 47, 60)
    assert run(goal, g.Readings(comfort_temp_c=33)).setpoints == sp(33, 43, 53, 40)
    assert not run(goal, g.Readings(comfort_temp_c=38.5)).changed


def test_fps_goal():
    goal = g.Goal("fps", 60)
    assert run(goal, g.Readings(fps=40)).setpoints.spl_w == 33
    assert run(goal, g.Readings(fps=80)).setpoints.spl_w == 27
    assert not run(goal, g.Readings(fps=61)).changed
    assert not run(goal, g.Readings(fps=80), sp(15, 40, 50, 50)).changed


def test_min_dwell_blocks_then_releases():
    goal, r = g.Goal("fps", 60), g.Readings(fps=40)
    d = run(goal, r)
    assert d.state.last_change_at == 100.0
    held = g.decide(goal, r, d.setpoints, d.state, 110.0)
    assert not held.changed and held.reason == "minimum dwell"
    later = g.decide(goal, r, d.setpoints, d.state, 121.0)
    assert later.changed and later.setpoints.spl_w == 36


def test_pure_and_deterministic():
    a = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=4500))
    assert a == run(g.Goal("quiet", 3000), g.Readings(fan_rpm=4500))


# --- defect 6: recorded traces for every goal, replayed one tick per second ---

def replay(goal, trace, start=CUR, limits=g.Limits()):
    """Feed a recorded trace (one Readings per second) through decide()."""
    cur, st, out = start, g.State(), []
    for t, r in enumerate(trace):
        d = g.decide(goal, r, cur, st, float(t), limits)
        out.append((t, d))
        cur, st = d.setpoints, d.state
    return out


def change_times(out):
    return [t for t, d in out if d.changed]


def assert_stable(out, limits=g.Limits(), fan_during_dwell=False):
    prev = None
    lastsp = CUR
    for t, d in out:
        s = d.setpoints
        assert s.spl_w <= s.sppt_w <= s.fppt_w
        for rail, v in zip(("pl1", "pl2", "pl3"), (s.spl_w, s.sppt_w, s.fppt_w)):
            assert limits.ranges[rail][0] <= v <= limits.ranges[rail][1]
        power_changed = (s.spl_w, s.sppt_w, s.fppt_w) != (lastsp.spl_w, lastsp.sppt_w, lastsp.fppt_w)
        if d.changed:
            if prev is not None and (not fan_during_dwell or power_changed):
                assert t - prev >= g.MIN_DWELL_S
            assert abs(s.fan_pct - lastsp.fan_pct) <= g.MAX_FAN_STEP_PCT
            for value, old in zip((s.spl_w, s.sppt_w, s.fppt_w),
                                  (lastsp.spl_w, lastsp.sppt_w, lastsp.fppt_w)):
                assert abs(value - old) <= g.MAX_TDP_STEP_W
            if not fan_during_dwell or power_changed:
                prev = t
        lastsp = s


# recorded: fan RPM climbing in a game, then settling under the ceiling
QUIET_TRACE = [g.Readings(fan_rpm=r) for r in
               [4800] * 60 + [3200] * 60 + [2600] * 60 + [2900] * 120]
# recorded: chassis proxy warming past target, then cooling, then hovering in band
COMFORT_TRACE = [g.Readings(comfort_temp_c=c) for c in
                 [43] * 60 + [41] * 40 + [33] * 60 + [38.5, 37.2, 38.8, 37.9] * 40]
# recorded: frame rate dipping below target, recovering high, then flickering in band
FPS_TRACE = [g.Readings(fps=f) for f in
             [40] * 60 + [75] * 60 + [60, 58.5, 62, 61] * 60]
# recorded: Max with a hot spell
MAX_TRACE = ([g.Readings(apu_temp_c=70)] * 40 + [g.Readings(apu_temp_c=97)] * 40
             + [g.Readings(apu_temp_c=80)] * 100)


@pytest.mark.parametrize("goal,trace", [
    (g.Goal("quiet", 3000), QUIET_TRACE),
    (g.Goal("comfort", 38), COMFORT_TRACE),
    (g.Goal("fps", 60), FPS_TRACE),
    (g.Goal("max"), MAX_TRACE),
])
def test_trace_is_stable(goal, trace):
    out = replay(goal, trace, limits=RANGES)
    assert_stable(out, RANGES, fan_during_dwell=goal.kind == "max")


def test_quiet_trace_settles_and_stays():
    out = replay(g.Goal("quiet", 3000), QUIET_TRACE, limits=RANGES)
    assert change_times(out)[0] == 0
    assert change_times(out)[-1] < 240          # nothing changes in the last 120 s
    assert min(d.setpoints.fan_pct for _, d in out) < 50   # the curve came down first


def test_comfort_trace_band_is_quiet():
    out = replay(g.Goal("comfort", 38), COMFORT_TRACE, limits=RANGES)
    assert change_times(out)[-1] < 160          # 160 s of in-band hovering: no change


def test_fps_trace_band_is_quiet():
    out = replay(g.Goal("fps", 60), FPS_TRACE, limits=RANGES)
    assert change_times(out)[-1] < 120


def test_max_trace_full_fans_and_sheds_when_hot():
    out = replay(g.Goal("max"), MAX_TRACE, limits=RANGES)
    assert [d.setpoints.fan_pct for _, d in out[:5]] == [60, 70, 80, 90, 100]
    assert all(d.setpoints.fan_pct == 100 for _, d in out[4:])
    hot = [d.setpoints.spl_w for t, d in out if 40 <= t < 80]
    assert hot[-1] < hot[0]                     # power shed under the limit breach


def test_custom_trace_never_changes():
    out = replay(g.Goal("custom"), QUIET_TRACE)
    assert not change_times(out)
