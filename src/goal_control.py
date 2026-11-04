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

from dataclasses import dataclass, replace
from typing import Optional

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
    tdp_w: int
    fan_pct: int   # the curve's ceiling; see curve_points()


@dataclass(frozen=True)
class Limits:
    tdp_min_w: int = 5
    tdp_max_w: int = 80
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
    """The 8-point curve for a ceiling: ramps from the minimum to ``fan_pct``."""
    fan_pct = max(FAN_MIN_PCT, min(FAN_MAX_PCT, int(fan_pct)))
    n = len(temps) - 1
    return [(t, round(FAN_MIN_PCT + (fan_pct - FAN_MIN_PCT) * i / n))
            for i, t in enumerate(temps)]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _move(cur, want, step, lo, hi):
    return int(_clamp(cur + _clamp(want - cur, -step, step), lo, hi))


def _wanted(goal, r, cur, lim):
    """(tdp_w, fan_pct, reason) the goal would like now, before step limits."""
    tdp, fan = cur.tdp_w, cur.fan_pct
    t = goal.target
    if r.apu_temp_c is not None and r.apu_temp_c >= lim.apu_temp_limit_c:
        return tdp - MAX_TDP_STEP_W, FAN_MAX_PCT, "over APU temperature limit"
    if goal.kind == "max":
        return lim.tdp_max_w, FAN_MAX_PCT, "max"
    if goal.kind == "quiet" and t is not None and r.fan_rpm is not None:
        if r.fan_rpm > t:
            if fan > FAN_MIN_PCT:
                return tdp, fan - MAX_FAN_STEP_PCT, "fans above ceiling: lower curve"
            return tdp - MAX_TDP_STEP_W, fan, "fans above ceiling at curve floor: lower TDP"
        if r.fan_rpm < t - RPM_HYSTERESIS:
            return tdp + MAX_TDP_STEP_W, fan + MAX_FAN_STEP_PCT, "headroom under ceiling"
        return tdp, fan, "within quiet band"
    if goal.kind == "comfort" and t is not None and r.comfort_temp_c is not None:
        if r.comfort_temp_c > t + TEMP_HYSTERESIS_C:
            return tdp - MAX_TDP_STEP_W, fan + MAX_FAN_STEP_PCT, "too warm: lower TDP, raise fans"
        if r.comfort_temp_c < t - TEMP_HYSTERESIS_C:
            return tdp + MAX_TDP_STEP_W, fan - MAX_FAN_STEP_PCT, "cool enough: raise TDP"
        return tdp, fan, "within comfort band"
    if goal.kind == "fps" and t is not None and r.fps is not None:
        if r.fps < t - FPS_HYSTERESIS:
            return tdp + MAX_TDP_STEP_W, fan + MAX_FAN_STEP_PCT, "below target fps"
        if r.fps > t + FPS_HYSTERESIS and tdp > FPS_TDP_FLOOR_W:
            return tdp - MAX_TDP_STEP_W, fan, "above target fps: save power"
        return tdp, fan, "within fps band"
    return tdp, fan, "no change (custom goal or missing reading)"


def decide(goal, readings, current, state=State(), now=0.0, limits=Limits()):
    """Next setpoints for ``goal``. Pure: same inputs, same output."""
    if goal.kind not in GOALS:
        raise ValueError(f"unknown goal {goal.kind!r}")
    if goal.kind == "custom":
        return Decision(current, False, state, "custom: fixed profile")
    over_limit = (readings.apu_temp_c is not None
                  and readings.apu_temp_c >= limits.apu_temp_limit_c)
    dwelling = (state.last_change_at is not None
                and now - state.last_change_at < MIN_DWELL_S)
    if dwelling and not over_limit:
        return Decision(current, False, state, "minimum dwell")
    tdp, fan, reason = _wanted(goal, readings, current, limits)
    step = MAX_TDP_STEP_W
    new = Setpoints(
        _move(current.tdp_w, tdp, step, limits.tdp_min_w, limits.tdp_max_w),
        _move(current.fan_pct, fan, MAX_FAN_STEP_PCT, FAN_MIN_PCT, FAN_MAX_PCT)
        if goal.kind != "max" else FAN_MAX_PCT,
    )
    if new == current:
        return Decision(current, False, state, reason)
    return Decision(new, True, replace(state, last_change_at=now), reason)
