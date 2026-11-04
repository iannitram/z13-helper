import pytest

import goal_control as g

CUR = g.Setpoints(tdp_w=30, fan_pct=50)


def run(goal, r, cur=CUR, state=g.State(), now=100.0):
    return g.decide(goal, r, cur, state, now)


def test_custom_never_changes():
    d = run(g.Goal("custom"), g.Readings(fan_rpm=9999, apu_temp_c=99))
    assert not d.changed and d.setpoints == CUR


def test_unknown_goal():
    with pytest.raises(ValueError):
        run(g.Goal("turbo"), g.Readings())


def test_quiet_over_ceiling_lowers_curve_by_one_step():
    d = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=4500))
    assert d.changed and d.setpoints == g.Setpoints(30, 40)


def test_quiet_at_curve_floor_lowers_tdp():
    d = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=4500), g.Setpoints(30, 20))
    assert d.setpoints == g.Setpoints(27, 20)


def test_quiet_hysteresis_band_holds():
    d = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=2800))
    assert not d.changed


def test_quiet_headroom_raises_tdp():
    d = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=1000))
    assert d.setpoints.tdp_w == 33


def test_step_is_capped_and_bounded():
    d = run(g.Goal("max"), g.Readings(), g.Setpoints(79, 95))
    assert d.setpoints == g.Setpoints(80, 100)


def test_max_wins_to_top():
    d = run(g.Goal("max"), g.Readings(fan_rpm=0))
    assert d.setpoints.fan_pct == 100 and d.setpoints.tdp_w == 33


def test_comfort_warm_and_cool_and_band():
    goal = g.Goal("comfort", 38)
    warm = run(goal, g.Readings(comfort_temp_c=42))
    assert warm.setpoints == g.Setpoints(27, 60)
    cool = run(goal, g.Readings(comfort_temp_c=33))
    assert cool.setpoints == g.Setpoints(33, 40)
    assert not run(goal, g.Readings(comfort_temp_c=38.5)).changed


def test_fps_goal():
    goal = g.Goal("fps", 60)
    assert run(goal, g.Readings(fps=40)).setpoints.tdp_w == 33
    assert run(goal, g.Readings(fps=80)).setpoints.tdp_w == 27
    assert not run(goal, g.Readings(fps=61)).changed
    assert not run(goal, g.Readings(fps=80), g.Setpoints(15, 50)).changed


def test_missing_reading_holds():
    assert not run(g.Goal("fps", 60), g.Readings()).changed


def test_min_dwell_blocks_then_releases():
    d = run(g.Goal("fps", 60), g.Readings(fps=40))
    assert d.state.last_change_at == 100.0
    held = g.decide(g.Goal("fps", 60), g.Readings(fps=40), d.setpoints, d.state, 110.0)
    assert not held.changed and held.reason == "minimum dwell"
    later = g.decide(g.Goal("fps", 60), g.Readings(fps=40), d.setpoints, d.state, 121.0)
    assert later.changed and later.setpoints.tdp_w == 36


def test_temperature_limit_overrides_dwell_and_goal():
    st = g.State(last_change_at=100.0)
    d = g.decide(g.Goal("max"), g.Readings(apu_temp_c=96), CUR, st, 101.0)
    assert d.changed and d.setpoints.tdp_w == 27 and d.setpoints.fan_pct == 100


def test_pure_and_deterministic():
    a = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=4500))
    b = run(g.Goal("quiet", 3000), g.Readings(fan_rpm=4500))
    assert a == b


def test_curve_points():
    pts = g.curve_points(100)
    assert [t for t, _ in pts] == list(g.CURVE_TEMPS)
    assert pts[0][1] == 20 and pts[-1][1] == 100
    assert all(p[1] <= q[1] for p, q in zip(pts, pts[1:]))
    assert g.curve_points(5)[-1][1] == 20
