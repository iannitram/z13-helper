"""Goal control: the pure decision function (Phase 15a, #123).

``decide(goal, readings, current, state, now)`` turns a goal, the latest
readings and the current setpoints into the next setpoints. It does no I/O, no
clock reads and no hardware writes: the runner (#125) owns those and passes
``now`` in. Tests drive it with plain numbers.

Stability rules, so the runner never makes the firmware thrash:
- hysteresis: a dead band around each target in which nothing changes;
- maximum step: TDP moves at most ``MAX_TDP_STEP_W`` and the fan ceiling at most
  ``MAX_FAN_STEP_PCT`` per decision;
- minimum dwell: after a change nothing moves for ``MIN_DWELL_S``.

Goals: quiet (fan RPM ceiling), comfort (proxy temperature target, #127),
fps (frame-rate target, #126), max (full fans, top TDP; Max wins) and custom
(the fixed profile; this function never changes anything).
"""

from dataclasses import dataclass, field, replace
from typing import Optional

from power_range import DEFAULT_RANGES

GOALS = ("quiet", "comfort", "fps", "max", "custom")

MAX_TDP_STEP_W = 3
MAX_FAN_STEP_PCT = 10
MIN_DWELL_S = 20.0
FAN_MIN_PCT = 20          # the lowest fan ceiling the controller asks for
FAN_MAX_PCT = 100
RPM_HYSTERESIS = 300      # RPM dead band below the quiet ceiling
TEMP_HYSTERESIS_C = 1.5   # dead band around the comfort target
FPS_HYSTERESIS = 3.0      # dead band around the frame-rate target
FPS_TDP_FLOOR_W = 15
CURVE_TEMPS = (20, 30, 40, 50, 60, 70, 80, 90)   # same anchors as Max Fans


@dataclass(frozen=True)
class Goal:
    kind: str = "custom"
    target: Optional[float] = None   # quiet: RPM; comfort: deg C; fps: frames/s


@dataclass(frozen=True)
class Readings:
    fan_rpm: Optional[float] = None       # highest fan reading
    apu_temp_c: Optional[float] = None
    comfort_temp_c: Optional[float] = None  # proxy from #127
    fps: Optional[float] = None


@dataclass(frozen=True)
class Setpoints:
    spl_w: int     # PL1, sustained
    sppt_w: int    # PL2, slow boost
    fppt_w: int    # PL3, fast boost
    fan_pct: int   # the curve's ceiling; see curve_points()


@dataclass(frozen=True)
class Limits:
    """Per-rail kernel ranges (``power_range.detect_ranges()``) and the APU cap."""
    ranges: dict = field(default_factory=lambda: dict(DEFAULT_RANGES))
    apu_temp_limit_c: float = 95.0   # above this, shed power whatever the goal


@dataclass(frozen=True)
class State:
    """Carried between calls by the runner."""
    last_change_at: Optional[float] = None


@dataclass(frozen=True)
class Decision:
    setpoints: Setpoints
    changed: bool
    state: State
    reason: str


def curve_points(fan_pct, temps=CURVE_TEMPS):
    """The 8-point curve for a ceiling.

    Full speed (100) is the Max Fans curve: 100% at every anchor. Lower ceilings
    ramp from the minimum up to the ceiling.
    """
    fan_pct = max(FAN_MIN_PCT, min(FAN_MAX_PCT, int(fan_pct)))
    if fan_pct >= FAN_MAX_PCT:
        return [(t, FAN_MAX_PCT) for t in temps]
    n = len(temps) - 1
    return [(t, round(FAN_MIN_PCT + (fan_pct - FAN_MIN_PCT) * i / n))
            for i, t in enumerate(temps)]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _move_fan(cur, want):
    return int(_clamp(cur + _clamp(want - cur, -MAX_FAN_STEP_PCT, MAX_FAN_STEP_PCT),
                      FAN_MIN_PCT, FAN_MAX_PCT))


def _move_power(cur, direction, limits):
    """Move all three rails ``direction`` steps, inside each rail's own range and
    keeping SPL <= sPPT <= fPPT."""
    rails = []
    floor = limits.ranges["pl1"][0]
    for rail, v in zip(("pl1", "pl2", "pl3"), (cur.spl_w, cur.sppt_w, cur.fppt_w)):
        lo, hi = limits.ranges[rail]
        # Later rails must also respect every earlier rail's minimum.
        floor = max(floor, lo)
        rails.append(int(_clamp(v + direction * MAX_TDP_STEP_W, floor, hi)))
    spl, sppt, fppt = rails
    # Upper bounds win downwards so each value stays inside its own range and
    # SPL <= sPPT <= fPPT holds.
    fppt = max(fppt, limits.ranges["pl3"][0])
    sppt = min(sppt, fppt, limits.ranges["pl2"][1])
    spl = min(spl, sppt)
    return spl, sppt, fppt


def _needed(goal):
    return {"quiet": "fan_rpm", "comfort": "comfort_temp_c", "fps": "fps"}.get(goal.kind)


def _wanted(goal, r, cur, lim):
    """(power direction -1/0/+1, fan_pct, reason) the goal wants; steps applied later."""
    fan = cur.fan_pct
    t = goal.target
    if r.apu_temp_c is not None and r.apu_temp_c >= lim.apu_temp_limit_c:
        return -1, FAN_MAX_PCT, "over APU temperature limit"
    if goal.kind == "quiet":
        if r.fan_rpm > t:
            if fan > FAN_MIN_PCT:
                return 0, fan - MAX_FAN_STEP_PCT, "fans above ceiling: lower curve"
            return -1, fan, "fans above ceiling at curve floor: lower TDP"
        if r.fan_rpm < t - RPM_HYSTERESIS:
            return 1, fan + MAX_FAN_STEP_PCT, "headroom under ceiling"
        return 0, fan, "within quiet band"
    if goal.kind == "comfort":
        if r.comfort_temp_c > t + TEMP_HYSTERESIS_C:
            return -1, fan + MAX_FAN_STEP_PCT, "too warm: lower TDP, raise fans"
        if r.comfort_temp_c < t - TEMP_HYSTERESIS_C:
            return 1, fan - MAX_FAN_STEP_PCT, "cool enough: raise TDP"
        return 0, fan, "within comfort band"
    # fps
    if r.fps < t - FPS_HYSTERESIS:
        return 1, fan + MAX_FAN_STEP_PCT, "below target fps"
    if r.fps > t + FPS_HYSTERESIS and cur.spl_w > FPS_TDP_FLOOR_W:
        return -1, fan, "above target fps: save power"
    return 0, fan, "within fps band"


def _result(current, state, now, spl, sppt, fppt, fan, reason):
    new = Setpoints(spl, sppt, fppt, fan)
    if new == current:
        return Decision(current, False, state, reason)
    return Decision(new, True, replace(state, last_change_at=now), reason)


def decide(goal, readings, current, state=State(), now=0.0, limits=Limits()):
    """Next setpoints for ``goal``. Pure: same inputs, same output."""
    if goal.kind not in GOALS:
        raise ValueError(f"unknown goal {goal.kind!r}")
    if goal.kind == "custom":
        return Decision(current, False, state, "custom: fixed profile")
    dwelling = (state.last_change_at is not None
                and now - state.last_change_at < MIN_DWELL_S)
    over_limit = (readings.apu_temp_c is not None
                  and readings.apu_temp_c >= limits.apu_temp_limit_c)
    if goal.kind == "max":
        if readings == Readings():
            return Decision(current, False, state, "no change: missing reading")
        fan = _move_fan(current.fan_pct, FAN_MAX_PCT)
        if dwelling:
            # Max wins: fans step up; power waits out the dwell and the
            # dwell clock is not restarted by this fan-only change.
            new = replace(current, fan_pct=fan)
            return Decision(new, new != current, state, "max: raise fans, power in dwell")
        spl, sppt, fppt = _move_power(current, -1 if over_limit else 1, limits)
        return _result(current, state, now, spl, sppt, fppt, fan,
                       "over APU temperature limit" if over_limit else "max")
    need = _needed(goal)
    if goal.target is None or getattr(readings, need) is None:
        return Decision(current, False, state, "no change: missing reading")
    if dwelling:
        return Decision(current, False, state, "minimum dwell")
    direction, fan, reason = _wanted(goal, readings, current, limits)
    spl, sppt, fppt = _move_power(current, direction, limits)
    return _result(current, state, now, spl, sppt, fppt, _move_fan(current.fan_pct, fan), reason)
